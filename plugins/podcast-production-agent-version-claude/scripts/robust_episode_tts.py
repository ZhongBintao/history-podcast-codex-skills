#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TTS_SCRIPT = ROOT / "scripts/cosyvoice_ws_tts.py"


def python_executable():
    configured = os.environ.get("PODCAST_AUDIO_PYTHON")
    if configured:
        return configured
    venv_python = ROOT / ".venv/bin/python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path, text):
    Path(path).write_text(text.strip() + "\n", encoding="utf-8")


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


def chunk_signature(chunk):
    payload = json.dumps(
        [{"id": p.get("id"), "text": p.get("text")} for p in chunk],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def reusable_chunk(chunk_dir, expected_signature):
    signature_path = chunk_dir / "chunk_signature.txt"
    manifest_path = chunk_dir / "tts_manifest.json"
    required = [
        chunk_dir / "voice.wav",
        chunk_dir / "voice_timeline_raw.json",
        chunk_dir / "voice_timeline_compact.json",
        manifest_path,
    ]
    if not signature_path.exists() or signature_path.read_text(encoding="utf-8").strip() != expected_signature:
        return False
    if not all(p.exists() and p.stat().st_size > 0 for p in required):
        return False
    manifest = load_json(manifest_path)
    return manifest.get("failed_reason") is None


def split_chunks(paragraphs, max_chars):
    chunks = []
    current = []
    current_chars = 0
    for para in paragraphs:
        text_len = len(para["text"])
        if current and current_chars + text_len > max_chars:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(para)
        current_chars += text_len
    if current:
        chunks.append(current)
    return chunks


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
    return len(wav_pcm_bytes(path)) / float(sample_rate * 2)


def concat_wavs(paths, output, sample_rate, silence_ms, tail_silence_ms):
    silence_frames = int(sample_rate * silence_ms / 1000)
    tail_frames = int(sample_rate * tail_silence_ms / 1000)
    silence_bytes = b"\x00\x00" * silence_frames
    tail_bytes = b"\x00\x00" * tail_frames
    with wave.open(str(output), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        for index, path in enumerate(paths):
            out.writeframes(wav_pcm_bytes(path))
            if index < len(paths) - 1 and silence_frames:
                out.writeframes(silence_bytes)
        if tail_frames:
            out.writeframes(tail_bytes)


def offset_sentence(sentence, offset_ms, index):
    copied = json.loads(json.dumps(sentence, ensure_ascii=False))
    copied["index"] = index
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--narration", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--output-prefix", default="voice")
    parser.add_argument("--manifest-name", default="tts_manifest.json")
    parser.add_argument("--model", default="cosyvoice-v3-flash")
    parser.add_argument("--voice", default="longsanshu_v3")
    parser.add_argument("--sample-rate", type=int, default=24000)
    parser.add_argument("--max-chars-per-task", type=int, default=300)
    parser.add_argument("--chunk-silence-ms", type=int, default=450)
    parser.add_argument("--tail-silence-ms", type=int, default=3500)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()
    py = python_executable()

    started = time.time()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = out_dir / f"{args.output_prefix}_robust_chunks"
    work_dir.mkdir(parents=True, exist_ok=True)

    paragraphs = derive_paragraphs(args.narration)
    chunks = split_chunks(paragraphs, args.max_chars_per_task)
    input_chars = sum(len(p["text"]) for p in paragraphs)

    audio_path = out_dir / f"{args.output_prefix}.wav"
    raw_path = out_dir / f"{args.output_prefix}_timeline_raw.json"
    compact_path = out_dir / f"{args.output_prefix}_timeline_compact.json"
    manifest_path = out_dir / args.manifest_name

    manifest = {
        "model": args.model,
        "voice": args.voice,
        "api_type": "websocket",
        "generation_mode": "chunked_external_orchestration",
        "send_mode": "combined",
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
        "orchestrator": "scripts/robust_episode_tts.py",
        "retryable": True,
        "retries_per_chunk": args.retries,
        "completed_chunks": [],
        "reused_chunks": [],
        "failed_chunk_index": None,
        "failed_chunk_dir": None,
        "failed_reason": None,
    }

    failed_chunk_index = None
    failed_chunk_dir = None
    try:
        chunk_outputs = []
        for index, chunk in enumerate(chunks, 1):
            chunk_dir = work_dir / f"chunk_{index:03d}"
            chunk_dir.mkdir(parents=True, exist_ok=True)
            chunk_narration = chunk_dir / "narration.txt"
            signature = chunk_signature(chunk)
            signature_path = chunk_dir / "chunk_signature.txt"
            write_text(chunk_narration, "\n\n".join(p["text"] for p in chunk))

            cmd = [
                py,
                str(TTS_SCRIPT),
                "--narration",
                str(chunk_narration),
                "--out-dir",
                str(chunk_dir),
                "--output-prefix",
                "voice",
                "--manifest-name",
                "tts_manifest.json",
                "--model",
                args.model,
                "--voice",
                args.voice,
                "--send-mode",
                "combined",
                "--max-chars-per-task",
                str(args.max_chars_per_task),
                "--chunk-silence-ms",
                "0",
                "--tail-silence-ms",
                "0",
                "--timeout",
                "900",
            ]
            reused = reusable_chunk(chunk_dir, signature)
            attempts_used = 0
            if reused:
                manifest["reused_chunks"].append(index)
            else:
                for stale in [
                    chunk_dir / "voice.wav",
                    chunk_dir / "voice_timeline_raw.json",
                    chunk_dir / "voice_timeline_compact.json",
                    chunk_dir / "tts_manifest.json",
                ]:
                    if stale.exists():
                        stale.unlink()
                signature_path.write_text(signature + "\n", encoding="utf-8")
                last_error = None
                for attempt in range(1, args.retries + 2):
                    attempts_used = attempt
                    result = subprocess.run(cmd, cwd=str(ROOT), env=os.environ.copy(), text=True, capture_output=True)
                    if result.returncode == 0:
                        break
                    last_error = (result.stderr or result.stdout or "").strip()
                    time.sleep(1.5 * attempt)
                else:
                    failed_chunk_index = index
                    failed_chunk_dir = str(chunk_dir)
                    manifest["failed_chunk_index"] = failed_chunk_index
                    manifest["failed_chunk_dir"] = failed_chunk_dir
                    manifest["completed_chunks"] = [c["chunk_index"] for c in chunk_outputs]
                    write_json(manifest_path, manifest)
                    raise RuntimeError(f"chunk {index} failed after retries: {last_error}")

            chunk_manifest = load_json(chunk_dir / "tts_manifest.json")
            chunk_compact = load_json(chunk_dir / "voice_timeline_compact.json")
            for para, source_para in zip(chunk_compact.get("paragraphs", []), chunk):
                para["id"] = source_para["id"]
            if chunk_manifest.get("failed_reason"):
                failed_chunk_index = index
                failed_chunk_dir = str(chunk_dir)
                manifest["failed_chunk_index"] = failed_chunk_index
                manifest["failed_chunk_dir"] = failed_chunk_dir
                manifest["completed_chunks"] = [c["chunk_index"] for c in chunk_outputs]
                write_json(manifest_path, manifest)
                raise RuntimeError(f"chunk {index} failed: {chunk_manifest['failed_reason']}")
            manifest["completed_chunks"].append(index)
            chunk_outputs.append(
                {
                    "chunk_index": index,
                    "paragraph_ids": [p["id"] for p in chunk],
                    "signature": signature,
                    "reused": reused,
                    "attempts_used": attempts_used,
                    "audio_path": chunk_dir / "voice.wav",
                    "raw": load_json(chunk_dir / "voice_timeline_raw.json"),
                    "compact": chunk_compact,
                    "manifest": chunk_manifest,
                }
            )

        concat_wavs([c["audio_path"] for c in chunk_outputs], audio_path, args.sample_rate, args.chunk_silence_ms, args.tail_silence_ms)

        offset_ms = 0
        sentence_index = 0
        merged_sentences = []
        raw_chunks = []
        compact_paragraphs = []
        usage = 0
        for chunk in chunk_outputs:
            duration_ms = int(round(wav_duration_sec(chunk["audio_path"], args.sample_rate) * 1000))
            shifted_sentences = []
            for sent in chunk["raw"].get("sentences", []):
                sentence_index += 1
                shifted = offset_sentence(sent, offset_ms, sentence_index)
                shifted_sentences.append(shifted)
                merged_sentences.append(shifted)
            for para in chunk["compact"].get("paragraphs", []):
                copied = dict(para)
                if isinstance(copied.get("start_sec"), (int, float)):
                    copied["start_sec"] = copied["start_sec"] + offset_ms / 1000
                if isinstance(copied.get("end_sec"), (int, float)):
                    copied["end_sec"] = copied["end_sec"] + offset_ms / 1000
                compact_paragraphs.append(copied)
            raw_chunks.append(
                {
                    "chunk_index": chunk["chunk_index"],
                    "paragraph_ids": chunk["paragraph_ids"],
                    "audio_path": str(chunk["audio_path"]),
                    "offset_ms": offset_ms,
                    "duration_ms": duration_ms,
                    "signature": chunk["signature"],
                    "reused": chunk["reused"],
                    "attempts_used": chunk["attempts_used"],
                    "sentences": shifted_sentences,
                    "source_manifest": chunk["manifest"].get("output_audio"),
                }
            )
            usage += chunk["manifest"].get("usage_characters") or 0
            offset_ms += duration_ms + args.chunk_silence_ms

        write_json(
            raw_path,
            {
                "audio_path": str(audio_path),
                "model": args.model,
                "voice": args.voice,
                "timeline_source": "aliyun_bailian_websocket_word_timestamp_chunked_external_orchestration",
                "sentences": merged_sentences,
                "chunks": raw_chunks,
            },
        )
        ends = [p.get("end_sec") for p in compact_paragraphs if isinstance(p.get("end_sec"), (int, float))]
        compact = {
            "audio_path": str(audio_path),
            "duration_sec": wav_duration_sec(audio_path, args.sample_rate),
            "speech_end_sec": max(ends) if ends else None,
            "tail_silence_sec": args.tail_silence_ms / 1000,
            "paragraphs": compact_paragraphs,
        }
        write_json(compact_path, compact)

        manifest["usage_characters"] = usage or input_chars
        manifest["duration_sec"] = compact["duration_sec"]
        manifest["speech_end_sec"] = compact["speech_end_sec"]
        manifest["chunk_count"] = len(chunk_outputs)
        manifest["chunk_audio_dir"] = str(work_dir)
        manifest["completed_chunks"] = [c["chunk_index"] for c in chunk_outputs]
        manifest["elapsed_sec"] = round(time.time() - started, 3)
        write_json(manifest_path, manifest)
    except Exception as exc:
        if failed_chunk_index is not None:
            manifest["failed_chunk_index"] = failed_chunk_index
        if failed_chunk_dir is not None:
            manifest["failed_chunk_dir"] = failed_chunk_dir
        manifest["failed_reason"] = str(exc)
        manifest["elapsed_sec"] = round(time.time() - started, 3)
        write_json(manifest_path, manifest)
        raise


if __name__ == "__main__":
    main()
