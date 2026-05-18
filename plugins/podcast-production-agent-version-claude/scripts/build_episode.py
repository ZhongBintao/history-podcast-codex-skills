#!/usr/bin/env python3
import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_tool(name):
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"{name} is not available on PATH")
    return found


def run_cmd(cmd, commands):
    commands.append(cmd)
    subprocess.run(cmd, check=True)


def ffprobe_duration(path):
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    return float(out)


def require_audio(path, label):
    audio = Path(path).resolve()
    if not audio.exists():
        raise RuntimeError(f"{label} does not exist: {audio}")
    if audio.stat().st_size <= 0:
        raise RuntimeError(f"{label} is empty: {audio}")
    return audio


def optional_path(path):
    if not path:
        return None
    resolved = Path(path).resolve()
    return str(resolved) if resolved.exists() else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--opening-voice", required=True)
    parser.add_argument("--voice", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--episode-slug", default="episode")
    parser.add_argument("--opening-timeline")
    parser.add_argument("--voice-timeline")
    parser.add_argument("--silence-after-opening", type=float, default=1.2)
    parser.add_argument("--tail-silence", type=float, default=1.0)
    parser.add_argument("--bitrate", default="192k")
    args = parser.parse_args()

    require_tool("ffmpeg")
    require_tool("ffprobe")

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    episode_mp3 = out_dir / f"{args.episode_slug}.mp3"
    manifest_path = out_dir / "production_manifest.json"
    commands = []
    started = time.time()

    manifest = {
        "episode_audio_mp3": str(episode_mp3),
        "inputs": {
            "opening_voice": str(Path(args.opening_voice).resolve()),
            "voice": str(Path(args.voice).resolve()),
            "opening_voice_timeline_compact": optional_path(args.opening_timeline),
            "voice_timeline_compact": optional_path(args.voice_timeline),
        },
        "edit_scope": {
            "complete_spoken_episode": True,
            "opening_voice": True,
            "body_voice": True,
            "music": False,
            "sound_effects": False,
            "final_music_mix": False,
        },
        "commands": commands,
        "failed_reason": None,
    }

    try:
        opening_voice = require_audio(args.opening_voice, "opening voice")
        voice = require_audio(args.voice, "voice audio")

        filter_complex = (
            "[0:a]aresample=48000,pan=stereo|c0<c0+c1|c1<c0+c1[a0];"
            "[1:a]aresample=48000,pan=stereo|c0<c0+c1|c1<c0+c1[a1];"
            f"aevalsrc=0:d={args.silence_after_opening}:s=48000:c=stereo[s0];"
            f"aevalsrc=0:d={args.tail_silence}:s=48000:c=stereo[s1];"
            "[a0][s0][a1][s1]"
            "concat=n=4:v=0:a=1,"
            "loudnorm=I=-16:TP=-1.5:LRA=11,"
            "alimiter=limit=0.98[out]"
        )

        mp3_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(opening_voice),
            "-i",
            str(voice),
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-c:a",
            "libmp3lame",
            "-b:a",
            args.bitrate,
            "-ar",
            "48000",
            str(episode_mp3),
        ]
        run_cmd(mp3_cmd, commands)

        manifest.update(
            {
                "durations_sec": {
                    "opening_voice": round(ffprobe_duration(opening_voice), 3),
                    "silence_after_opening": args.silence_after_opening,
                    "voice": round(ffprobe_duration(voice), 3),
                    "tail_silence": args.tail_silence,
                    "episode_mp3": round(ffprobe_duration(episode_mp3), 3),
                },
                "edit_settings": {
                    "sequence": ["opening_voice", "silence_after_opening", "voice", "tail_silence"],
                    "loudnorm_target_lufs": -16,
                    "true_peak_limit_db": -1.5,
                    "bitrate": args.bitrate,
                },
                "elapsed_sec": round(time.time() - started, 3),
            }
        )
        write_json(manifest_path, manifest)
    except Exception as exc:
        manifest["failed_reason"] = str(exc)
        manifest["elapsed_sec"] = round(time.time() - started, 3)
        write_json(manifest_path, manifest)
        raise


if __name__ == "__main__":
    main()
