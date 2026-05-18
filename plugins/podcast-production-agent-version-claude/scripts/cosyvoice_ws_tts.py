#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import time
import uuid
import wave
from pathlib import Path


ENDPOINT = "wss://dashscope.aliyuncs.com/api-ws/v1/inference/"


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def derive_paragraphs(narration_path):
    raw = Path(narration_path).read_text(encoding="utf-8")
    blocks = [block.strip() for block in raw.split("\n\n")]
    paragraphs = []
    for block in blocks:
        text = "\n".join(line.strip() for line in block.splitlines() if line.strip()).strip()
        if not text:
            continue
        paragraphs.append(
            {
                "id": f"p{len(paragraphs) + 1:03d}",
                "text": text,
                "char_count": len(text),
                "role": "narrator",
            }
        )
    if not paragraphs:
        raise RuntimeError(f"narration.txt has no speakable paragraphs: {narration_path}")
    return paragraphs


def paragraph_pairs(paragraphs):
    return [(p["id"], p["text"]) for p in paragraphs if p.get("text")]


def build_task_chunks(paragraphs, max_chars):
    chunks = []
    current = []
    current_chars = 0
    for para_id, text in paragraphs:
        text_len = len(text)
        if current and current_chars + text_len > max_chars:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append((para_id, text))
        current_chars += text_len
    if current:
        chunks.append(current)
    return chunks


def event_sentence_record(event):
    payload = event.get("payload") or {}
    output = payload.get("output") or {}
    sentence = output.get("sentence") or {}
    words = sentence.get("words") or []
    if not words:
        return None
    start_times = [w.get("begin_time") for w in words if isinstance(w.get("begin_time"), int)]
    end_times = [w.get("end_time") for w in words if isinstance(w.get("end_time"), int)]
    return {
        "index": sentence.get("index"),
        "original_text": output.get("original_text", ""),
        "start_ms": min(start_times) if start_times else None,
        "end_ms": max(end_times) if end_times else None,
        "words": [
            {
                "text": w.get("text"),
                "begin_index": w.get("begin_index"),
                "end_index": w.get("end_index"),
                "start_ms": w.get("begin_time"),
                "end_ms": w.get("end_time"),
            }
            for w in words
        ],
    }


def dedupe_sentences(events):
    records = []
    for event in events:
        rec = event_sentence_record(event)
        if not rec or not rec.get("original_text"):
            continue
        if (
            records
            and records[-1].get("original_text") == rec.get("original_text")
            and records[-1].get("start_ms") == rec.get("start_ms")
        ):
            if (rec.get("end_ms") or -1) >= (records[-1].get("end_ms") or -1):
                records[-1] = rec
            continue
        records.append(rec)
    records.sort(key=lambda r: (r.get("start_ms") is None, r.get("start_ms") or 0))
    return records


def compact_paragraphs(paragraphs, sentences):
    ordered_words = []
    for sent in sentences:
        for word in sent.get("words") or []:
            text = word.get("text")
            start_ms = word.get("start_ms")
            end_ms = word.get("end_ms")
            if not text or not isinstance(start_ms, int) or not isinstance(end_ms, int):
                continue
            ordered_words.append({"text": text, "start_ms": start_ms, "end_ms": end_ms})

    compact = []
    cursor = 0
    skippable = set(" \t\r\n“”\"'《》〈〉")
    punctuation = set("，。！？：；、,.!?;:()（）")
    for para in paragraphs:
        text = para.get("text", "")
        matched = []
        local_cursor = cursor
        for ch in text:
            if local_cursor >= len(ordered_words):
                break
            current = ordered_words[local_cursor]["text"]
            if ch == current:
                matched.append(ordered_words[local_cursor])
                local_cursor += 1
                continue
            if ch in skippable or ch in punctuation:
                continue
            found = None
            for look in range(local_cursor + 1, min(local_cursor + 4, len(ordered_words))):
                if ordered_words[look]["text"] == ch:
                    found = look
                    break
            if found is not None:
                matched.extend(ordered_words[local_cursor : found + 1])
                local_cursor = found + 1
        if matched:
            cursor = local_cursor
            compact.append(
                {
                    "id": para.get("id"),
                    "start_sec": min(w["start_ms"] for w in matched) / 1000,
                    "end_sec": max(w["end_ms"] for w in matched) / 1000,
                    "text": text,
                    "match_status": "matched",
                }
            )
        else:
            compact.append(
                {
                    "id": para.get("id"),
                    "text": text,
                    "match_status": "needs_review",
                    "error": "no returned word timestamps matched this paragraph",
                }
            )
    return compact


def offset_sentence(sentence, offset_ms, sentence_index):
    copied = json.loads(json.dumps(sentence, ensure_ascii=False))
    copied["index"] = sentence_index
    if isinstance(copied.get("start_ms"), int):
        copied["start_ms"] += offset_ms
    if isinstance(copied.get("end_ms"), int):
        copied["end_ms"] += offset_ms
    for word in copied.get("words") or []:
        if isinstance(word.get("start_ms"), int):
            word["start_ms"] += offset_ms
        if isinstance(word.get("end_ms"), int):
            word["end_ms"] += offset_ms
    return copied


async def connect_ws(api_key, workspace=None):
    import websockets

    headers = {"Authorization": f"bearer {api_key}"}
    if workspace:
        headers["X-DashScope-WorkSpace"] = workspace
    try:
        return await websockets.connect(ENDPOINT, additional_headers=headers, max_size=None)
    except TypeError:
        return await websockets.connect(ENDPOINT, extra_headers=headers, max_size=None)


async def synthesize_chunk(args, api_key, chunk, chunk_index, chunk_audio_path):
    task_id = str(uuid.uuid4())
    events = []
    usage_characters = None
    chunk_audio_path.write_bytes(b"")

    async with await connect_ws(api_key, os.environ.get("DASHSCOPE_WORKSPACE")) as ws:
        run_task = {
            "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "model": args.model,
                "parameters": {
                    "text_type": "PlainText",
                    "voice": args.voice,
                    "format": "wav",
                    "sample_rate": args.sample_rate,
                    "volume": args.volume,
                    "rate": args.rate,
                    "pitch": args.pitch,
                    "enable_ssml": False,
                    "word_timestamp_enabled": True,
                },
                "input": {},
            },
        }
        await ws.send(json.dumps(run_task, ensure_ascii=False))
        task_started = False
        task_finished = False

        async def receiver():
            nonlocal task_started, task_finished, usage_characters
            while True:
                message = await ws.recv()
                if isinstance(message, bytes):
                    with chunk_audio_path.open("ab") as f:
                        f.write(message)
                    continue
                event = json.loads(message)
                events.append(event)
                usage = (event.get("payload") or {}).get("usage") or {}
                if isinstance(usage.get("characters"), int):
                    usage_characters = usage["characters"]
                ev = (event.get("header") or {}).get("event")
                if ev == "task-started":
                    task_started = True
                elif ev == "task-failed":
                    raise RuntimeError(json.dumps(event, ensure_ascii=False))
                elif ev == "task-finished":
                    task_finished = True
                    return

        receive_task = asyncio.create_task(receiver())
        while not task_started:
            await asyncio.sleep(0.05)

        if args.send_mode == "combined":
            send_texts = ["\n".join(text for _, text in chunk)]
        else:
            send_texts = [text for _, text in chunk]

        for text in send_texts:
            await ws.send(
                json.dumps(
                    {
                        "header": {
                            "action": "continue-task",
                            "task_id": task_id,
                            "streaming": "duplex",
                        },
                        "payload": {"input": {"text": text}},
                    },
                    ensure_ascii=False,
                )
            )
            await asyncio.sleep(args.chunk_delay)

        await ws.send(
            json.dumps(
                {
                    "header": {
                        "action": "finish-task",
                        "task_id": task_id,
                        "streaming": "duplex",
                    },
                    "payload": {"input": {}},
                },
                ensure_ascii=False,
            )
        )
        await asyncio.wait_for(receive_task, timeout=args.timeout)
        if not task_finished:
            raise RuntimeError(f"task did not finish for chunk {chunk_index}")

    return {
        "chunk_index": chunk_index,
        "task_id": task_id,
        "paragraph_ids": [p[0] for p in chunk],
        "text_chars": sum(len(p[1]) for p in chunk),
        "audio_path": str(chunk_audio_path),
        "events": events,
        "sentences": dedupe_sentences(events),
        "usage_characters": usage_characters,
    }


def wav_pcm_bytes(path):
    data = Path(path).read_bytes()
    pos = data.find(b"data")
    if pos < 0 or pos + 8 > len(data):
        raise RuntimeError(f"cannot find WAV data chunk: {path}")
    size = int.from_bytes(data[pos + 4 : pos + 8], "little", signed=False)
    start = pos + 8
    end = min(start + size, len(data))
    return data[start:end]


def wav_duration_sec(path, sample_rate):
    pcm = wav_pcm_bytes(path)
    return len(pcm) / float(sample_rate * 2)


def concatenate_wavs(chunk_paths, output_path, sample_rate, silence_ms, tail_silence_ms):
    silence_frames = int(sample_rate * silence_ms / 1000)
    tail_frames = int(sample_rate * tail_silence_ms / 1000)
    silence_bytes = b"\x00\x00" * silence_frames
    tail_bytes = b"\x00\x00" * tail_frames
    with wave.open(str(output_path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        for i, path in enumerate(chunk_paths):
            out.writeframes(wav_pcm_bytes(path))
            if i < len(chunk_paths) - 1 and silence_frames:
                out.writeframes(silence_bytes)
        if tail_frames:
            out.writeframes(tail_bytes)


async def run_tts(args):
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not set")

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_prefix
    chunk_dir = out_dir / f"{prefix}_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / f"{prefix}.wav"
    raw_path = out_dir / f"{prefix}_timeline_raw.json"
    compact_path = out_dir / f"{prefix}_timeline_compact.json"
    manifest_path = out_dir / args.manifest_name

    paragraphs = derive_paragraphs(args.narration)
    pairs = paragraph_pairs(paragraphs)
    task_chunks = build_task_chunks(pairs, args.max_chars_per_task)
    input_chars = sum(len(text) for _, text in pairs)
    generation_mode = "single_task" if len(task_chunks) == 1 else "chunked"
    manifest = {
        "model": args.model,
        "voice": args.voice,
        "api_type": "websocket",
        "generation_mode": generation_mode,
        "send_mode": args.send_mode,
        "max_chars_per_task": args.max_chars_per_task,
        "chunk_silence_ms": args.chunk_silence_ms,
        "tail_silence_ms": args.tail_silence_ms,
        "word_timestamp_enabled": True,
        "input_chars": input_chars,
        "usage_characters": None,
        "output_audio": str(audio_path),
        "raw_timeline": str(raw_path),
        "compact_timeline": str(compact_path),
        "license_status": "aliyun_bailian_standard_tts_system_voice",
        "api_key_source": "DASHSCOPE_API_KEY",
        "failed_reason": None,
    }

    started = time.time()
    chunk_results = []
    try:
        for idx, chunk in enumerate(task_chunks, 1):
            chunk_audio = chunk_dir / f"voice_chunk_{idx:03d}.wav"
            result = await synthesize_chunk(args, api_key, chunk, idx, chunk_audio)
            chunk_results.append(result)
            await asyncio.sleep(args.task_delay)

        chunk_paths = [Path(r["audio_path"]) for r in chunk_results]
        concatenate_wavs(chunk_paths, audio_path, args.sample_rate, args.chunk_silence_ms, args.tail_silence_ms)

        offset_ms = 0
        sentence_index = 0
        merged_sentences = []
        raw_chunks = []
        for result in chunk_results:
            duration_ms = int(round(wav_duration_sec(result["audio_path"], args.sample_rate) * 1000))
            offset_sentences = []
            for sent in result["sentences"]:
                sentence_index += 1
                shifted = offset_sentence(sent, offset_ms, sentence_index)
                offset_sentences.append(shifted)
                merged_sentences.append(shifted)
            raw_chunks.append(
                {
                    "chunk_index": result["chunk_index"],
                    "task_id": result["task_id"],
                    "paragraph_ids": result["paragraph_ids"],
                    "text_chars": result["text_chars"],
                    "audio_path": result["audio_path"],
                    "offset_ms": offset_ms,
                    "duration_ms": duration_ms,
                    "usage_characters": result["usage_characters"],
                    "sentences": offset_sentences,
                    "raw_events": result["events"],
                }
            )
            offset_ms += duration_ms + args.chunk_silence_ms

        tail_ms = args.tail_silence_ms
        raw = {
            "audio_path": str(audio_path),
            "model": args.model,
            "voice": args.voice,
            "timeline_source": f"aliyun_bailian_websocket_word_timestamp_{generation_mode}",
            "sentences": merged_sentences,
            "chunks": raw_chunks,
        }
        write_json(raw_path, raw)

        paragraphs_compact = compact_paragraphs(paragraphs, merged_sentences)
        ends = [p.get("end_sec") for p in paragraphs_compact if isinstance(p.get("end_sec"), (int, float))]
        compact = {
            "audio_path": str(audio_path),
            "duration_sec": wav_duration_sec(audio_path, args.sample_rate),
            "speech_end_sec": max(ends) if ends else None,
            "tail_silence_sec": tail_ms / 1000,
            "paragraphs": paragraphs_compact,
        }
        write_json(compact_path, compact)

        manifest["usage_characters"] = sum((r.get("usage_characters") or 0) for r in chunk_results) or input_chars
        manifest["duration_sec"] = compact["duration_sec"]
        manifest["speech_end_sec"] = compact["speech_end_sec"]
        manifest["chunk_count"] = len(chunk_results)
        manifest["chunk_audio_dir"] = str(chunk_dir)
        manifest["task_count"] = len(chunk_results)
        manifest["task_audio_dir"] = str(chunk_dir)
        manifest["elapsed_sec"] = round(time.time() - started, 3)
        write_json(manifest_path, manifest)
    except Exception as exc:
        manifest["failed_reason"] = str(exc)
        manifest["elapsed_sec"] = round(time.time() - started, 3)
        write_json(manifest_path, manifest)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--narration", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--model", default="cosyvoice-v3-flash")
    parser.add_argument("--voice", default="longsanshu_v3")
    parser.add_argument("--sample-rate", type=int, default=24000)
    parser.add_argument("--volume", type=int, default=50)
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--pitch", type=float, default=1.0)
    parser.add_argument("--max-chars-per-task", type=int, default=700)
    parser.add_argument("--send-mode", choices=["combined", "paragraph"], default="combined")
    parser.add_argument("--chunk-silence-ms", type=int, default=450)
    parser.add_argument("--tail-silence-ms", type=int, default=3500)
    parser.add_argument("--chunk-delay", type=float, default=0.35)
    parser.add_argument("--task-delay", type=float, default=0.8)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--output-prefix", default="voice")
    parser.add_argument("--manifest-name", default="tts_manifest.json")
    args = parser.parse_args()

    asyncio.run(run_tts(args))


if __name__ == "__main__":
    main()
