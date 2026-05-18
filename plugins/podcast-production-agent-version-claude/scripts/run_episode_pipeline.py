#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROBUST_TTS = ROOT / "scripts/robust_episode_tts.py"
DIRECT_TTS = ROOT / "scripts/cosyvoice_ws_tts.py"
EPISODE_EDITOR = ROOT / "scripts/build_episode.py"
BODY_TTS_MODE = "single_task"
ROBUST_TTS_MODE = "chunked_external_orchestration"
VALID_BODY_TTS_MODES = {BODY_TTS_MODE, ROBUST_TTS_MODE}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def python_executable():
    configured = os.environ.get("PODCAST_AUDIO_PYTHON")
    if configured:
        return configured
    venv_python = ROOT / ".venv/bin/python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def require_file(path, label):
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"missing {label}: {path}")
    if path.stat().st_size <= 0:
        raise RuntimeError(f"empty {label}: {path}")
    return path.resolve()


def validate_narration(episode_dir):
    narration = require_file(episode_dir / "narration.txt", "narration.txt")
    text = narration.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("narration.txt has no speakable text")
    forbidden = [
        (r"(?m)^\s{0,3}#{1,6}\s+", "Markdown heading"),
        (r"(?m)^\s*[-*+]\s+", "Markdown list"),
        (r"\[[^\]]+\]\([^)]+\)", "Markdown link"),
        (r"<[^>\n]+>", "TTS or markup tag"),
        (r"\[[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?\]", "timestamp"),
        (r"(?i)\b(fact[-_ ]?check|production note|制作备注|事实核查)\b", "production note"),
    ]
    for pattern, label in forbidden:
        if re.search(pattern, text):
            raise RuntimeError(f"narration.txt contains {label}; keep it clean for TTS")


def tts_complete(episode_dir):
    voice = episode_dir / "voice.wav"
    manifest_path = episode_dir / "tts_manifest.json"
    raw = episode_dir / "voice_timeline_raw.json"
    compact = episode_dir / "voice_timeline_compact.json"
    if not all(p.exists() and p.stat().st_size > 0 for p in [voice, manifest_path, raw, compact]):
        return False
    try:
        manifest = load_json(manifest_path)
    except Exception:
        return False
    return manifest.get("failed_reason") is None and manifest.get("generation_mode") in VALID_BODY_TTS_MODES


def mp3_complete(episode_dir):
    mp3 = episode_dir / "episode.mp3"
    manifest_path = episode_dir / "production_manifest.json"
    if not all(p.exists() and p.stat().st_size > 0 for p in [mp3, manifest_path]):
        return False
    try:
        manifest = load_json(manifest_path)
    except Exception:
        return False
    return manifest.get("failed_reason") is None


def find_state_episode(state, episode_dir):
    resolved = str(episode_dir.resolve())
    for episode in state.get("episodes", []):
        if episode.get("episode_dir") and str(Path(episode["episode_dir"]).resolve()) == resolved:
            return episode
    return None


def update_state(state_path, episode_dir, updates):
    state = load_json(state_path)
    episode = find_state_episode(state, episode_dir)
    if not episode:
        brief = load_json(episode_dir / "episode_brief.json")
        episode = {
            "episode_no": brief.get("episode_no"),
            "episode_title": brief.get("episode_title"),
            "status": "planned",
            "episode_dir": str(episode_dir.resolve()),
        }
        state.setdefault("episodes", []).append(episode)
    episode.update(updates)
    episode["updated_at"] = now_iso()
    completed = sorted(
        e.get("episode_no")
        for e in state.get("episodes", [])
        if e.get("status") == "mp3_done" and isinstance(e.get("episode_no"), int)
    )
    if completed:
        state["next_recommended_episode_no"] = completed[-1] + 1
    write_json(state_path, state)


def mark_failed(state_path, episode_dir, step, reason):
    update_state(
        state_path,
        episode_dir,
        {
            "status": "failed",
            "failed_step": step,
            "failed_reason": str(reason),
        },
    )


def run(cmd):
    print("+ " + " ".join(str(part) for part in cmd))
    subprocess.run(cmd, cwd=str(ROOT), check=True)


def record_tts_done(state_path, episode_dir):
    tts_manifest = load_json(episode_dir / "tts_manifest.json")
    update_state(
        state_path,
        episode_dir,
        {
            "status": "tts_done",
            "voice": str((episode_dir / "voice.wav").resolve()),
            "tts_manifest": str((episode_dir / "tts_manifest.json").resolve()),
            "tts_generation_mode": tts_manifest.get("generation_mode", BODY_TTS_MODE),
            "tts_task_count": tts_manifest.get("task_count", tts_manifest.get("chunk_count")),
            "tts_work_dir": tts_manifest.get("task_audio_dir") or tts_manifest.get("chunk_audio_dir"),
            "retryable": True,
            "failed_step": None,
            "failed_reason": None,
        },
    )


def run_tts(args, state_path, episode_dir, py):
    if args.skip_tts:
        if not tts_complete(episode_dir):
            raise RuntimeError("--skip-tts requires existing valid voice.wav and tts_manifest.json")
        print("Reusing existing TTS output because --skip-tts was provided.")
        record_tts_done(state_path, episode_dir)
        return

    if tts_complete(episode_dir) and not args.force_tts:
        print("Reusing existing complete TTS output. Use --force-tts to regenerate.")
        record_tts_done(state_path, episode_dir)
        return

    if not os.environ.get("DASHSCOPE_API_KEY"):
        raise RuntimeError("DASHSCOPE_API_KEY is not set")

    max_chars = args.max_chars_per_task or (300 if args.use_robust_chunking else 10000)
    common = [
        "--narration",
        str(episode_dir / "narration.txt"),
        "--out-dir",
        str(episode_dir),
        "--output-prefix",
        "voice",
        "--manifest-name",
        "tts_manifest.json",
        "--model",
        args.model,
        "--voice",
        args.voice,
        "--max-chars-per-task",
        str(max_chars),
        "--tail-silence-ms",
        str(args.tail_silence_ms),
    ]
    if args.use_robust_chunking:
        cmd = [
            py,
            str(ROBUST_TTS),
            *common,
            "--chunk-silence-ms",
            str(args.chunk_silence_ms),
            "--retries",
            str(args.retries),
        ]
    else:
        cmd = [
            py,
            str(DIRECT_TTS),
            *common,
            "--send-mode",
            "combined",
            "--chunk-silence-ms",
            "0",
            "--timeout",
            str(args.timeout),
        ]
    run(cmd)
    record_tts_done(state_path, episode_dir)


def run_edit(args, state_path, series_dir, episode_dir, py, opening_voice):
    if args.skip_edit:
        print("Skipping episode edit because --skip-edit was provided.")
        return

    if mp3_complete(episode_dir):
        print("Reusing existing complete episode.mp3.")
    else:
        cmd = [
            py,
            str(EPISODE_EDITOR),
            "--opening-voice",
            str(opening_voice),
            "--voice",
            str(episode_dir / "voice.wav"),
            "--out-dir",
            str(episode_dir),
            "--episode-slug",
            args.episode_slug,
        ]
        opening_timeline = series_dir / "opening_voice_timeline_compact.json"
        voice_timeline = episode_dir / "voice_timeline_compact.json"
        if opening_timeline.exists():
            cmd.extend(["--opening-timeline", str(opening_timeline)])
        if voice_timeline.exists():
            cmd.extend(["--voice-timeline", str(voice_timeline)])
        run(cmd)

    tts_manifest = load_json(episode_dir / "tts_manifest.json")
    update_state(
        state_path,
        episode_dir,
        {
            "status": "mp3_done",
            "voice": str((episode_dir / "voice.wav").resolve()),
            "tts_manifest": str((episode_dir / "tts_manifest.json").resolve()),
            "tts_generation_mode": tts_manifest.get("generation_mode", BODY_TTS_MODE),
            "tts_task_count": tts_manifest.get("task_count", tts_manifest.get("chunk_count")),
            "tts_work_dir": tts_manifest.get("task_audio_dir") or tts_manifest.get("chunk_audio_dir"),
            "retryable": True,
            "episode_mp3": str((episode_dir / f"{args.episode_slug}.mp3").resolve()),
            "production_manifest": str((episode_dir / "production_manifest.json").resolve()),
            "failed_step": None,
            "failed_reason": None,
        },
    )


def main():
    parser = argparse.ArgumentParser(description="Run the deterministic audio half of one podcast episode.")
    parser.add_argument("--series-dir", required=True)
    parser.add_argument("--episode-dir", required=True)
    parser.add_argument("--opening-voice")
    parser.add_argument("--episode-slug", default="episode")
    parser.add_argument("--force-tts", action="store_true")
    parser.add_argument("--skip-tts", action="store_true")
    parser.add_argument("--skip-edit", action="store_true")
    parser.add_argument("--model", default="cosyvoice-v3-flash")
    parser.add_argument("--voice", default="longsanshu_v3")
    parser.add_argument("--max-chars-per-task", type=int)
    parser.add_argument("--chunk-silence-ms", type=int, default=450)
    parser.add_argument("--tail-silence-ms", type=int, default=3500)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--use-robust-chunking", action="store_true")
    args = parser.parse_args()

    started = time.time()
    py = python_executable()
    series_dir = Path(args.series_dir).resolve()
    episode_dir = Path(args.episode_dir).resolve()
    state_path = require_file(series_dir / "production_state.json", "production_state.json")
    require_file(series_dir / "series_plan.json", "series_plan.json")
    require_file(episode_dir / "episode_brief.json", "episode_brief.json")
    validate_narration(episode_dir)
    opening_voice = require_file(args.opening_voice or (series_dir / "opening_voice.wav"), "opening_voice.wav")

    try:
        run_tts(args, state_path, episode_dir, py)
        if not args.skip_edit:
            require_file(episode_dir / "voice.wav", "voice.wav")
            require_file(episode_dir / "tts_manifest.json", "tts_manifest.json")
        run_edit(args, state_path, series_dir, episode_dir, py, opening_voice)
    except Exception as exc:
        step = "tts"
        if tts_complete(episode_dir) and not args.skip_edit:
            step = "edit"
        mark_failed(state_path, episode_dir, step, exc)
        print(f"FAILED {step}: {exc}", file=sys.stderr)
        return 1

    print(f"Pipeline completed in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
