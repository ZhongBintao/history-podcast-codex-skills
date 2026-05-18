#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path


VALID_STATUSES = {"planned", "brief_done", "narration_done", "tts_done", "mp3_done", "failed"}
BODY_TTS_MODE = "single_task"
LEGACY_BODY_TTS_MODE = "chunked_external_orchestration"
VALID_BODY_TTS_MODES = {BODY_TTS_MODE, LEGACY_BODY_TTS_MODE}


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.passes = []

    def ok(self, message):
        self.passes.append(message)

    def warn(self, message):
        self.warnings.append(message)

    def fail(self, message):
        self.errors.append(message)

    def print(self):
        for message in self.passes:
            print(f"PASS {message}")
        for message in self.warnings:
            print(f"WARN {message}")
        for message in self.errors:
            print(f"FAIL {message}")

    @property
    def success(self):
        return not self.errors


def load_json(path, report, required=True):
    path = Path(path)
    if not path.exists():
        if required:
            report.fail(f"missing JSON: {path}")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        report.fail(f"invalid JSON: {path} ({exc})")
        return None
    report.ok(f"valid JSON: {path}")
    return data


def require_file(path, report, label):
    path = Path(path)
    if not path.exists():
        report.fail(f"missing {label}: {path}")
        return False
    if path.stat().st_size <= 0:
        report.fail(f"empty {label}: {path}")
        return False
    report.ok(f"{label} exists: {path}")
    return True


def is_suspicious_secret(value):
    if not isinstance(value, str):
        return False
    if value in {"DASHSCOPE_API_KEY", "PODCAST_AUDIO_PYTHON"}:
        return False
    if "/" in value or "\\" in value:
        return False
    lowered = value.lower()
    if "dashscope_api_key" in lowered:
        return False
    secret_prefixes = ("sk-", "sk_", "d1.", "ak-")
    return len(value) >= 20 and value.startswith(secret_prefixes)


def scan_for_secret_values(obj, report, path):
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_lower = str(key).lower()
            if key_lower in {"api_key", "apikey", "secret", "token", "authorization"} and value:
                report.fail(f"manifest appears to contain a secret field {key!r}: {path}")
            scan_for_secret_values(value, report, path)
    elif isinstance(obj, list):
        for value in obj:
            scan_for_secret_values(value, report, path)
    elif is_suspicious_secret(obj):
        report.fail(f"manifest appears to contain a secret value: {path}")


def validate_narration(episode_dir, report):
    narration = episode_dir / "narration.txt"
    if not require_file(narration, report, "narration.txt"):
        return False
    actual = narration.read_text(encoding="utf-8").strip()
    if not actual:
        report.fail(f"narration.txt has no speakable text: {episode_dir}")
        return False
    forbidden = [
        (r"(?m)^\s{0,3}#{1,6}\s+", "Markdown heading"),
        (r"(?m)^\s*[-*+]\s+", "Markdown list"),
        (r"\[[^\]]+\]\([^)]+\)", "Markdown link"),
        (r"<[^>\n]+>", "TTS or markup tag"),
        (r"\[[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?\]", "timestamp"),
        (r"(?i)\b(fact[-_ ]?check|production note|制作备注|事实核查)\b", "production note"),
    ]
    for pattern, label in forbidden:
        if re.search(pattern, actual):
            report.fail(f"narration.txt contains {label}: {episode_dir}")
            return False
    report.ok(f"narration.txt is clean for TTS: {episode_dir}")
    return True


def validate_tts_manifest(episode_dir, report, required):
    manifest_path = episode_dir / "tts_manifest.json"
    manifest = load_json(manifest_path, report, required=required)
    if not manifest:
        return False
    scan_for_secret_values(manifest, report, manifest_path)
    if manifest.get("generation_mode") not in VALID_BODY_TTS_MODES:
        report.fail(f"tts_manifest generation_mode must be one of {sorted(VALID_BODY_TTS_MODES)}: {manifest_path}")
    if manifest.get("api_key_source") != "DASHSCOPE_API_KEY":
        report.fail(f"tts_manifest api_key_source must be DASHSCOPE_API_KEY: {manifest_path}")
    if manifest.get("failed_reason"):
        report.fail(f"tts_manifest has failed_reason: {manifest_path}")
    if not require_file(episode_dir / "voice.wav", report, "voice.wav"):
        return False
    for name in ["voice_timeline_raw.json", "voice_timeline_compact.json"]:
        load_json(episode_dir / name, report, required=True)
    return True


def validate_production_manifest(episode_dir, report, required):
    manifest_path = episode_dir / "production_manifest.json"
    manifest = load_json(manifest_path, report, required=required)
    if not manifest:
        return False
    if manifest.get("failed_reason"):
        report.fail(f"production_manifest has failed_reason: {manifest_path}")
    scope = manifest.get("edit_scope") or {}
    if scope.get("music") is not False:
        report.fail(f"production_manifest edit_scope.music must be false: {manifest_path}")
    if scope.get("sound_effects") is not False:
        report.fail(f"production_manifest edit_scope.sound_effects must be false: {manifest_path}")
    return require_file(episode_dir / "episode.mp3", report, "episode.mp3")


def infer_series_dir(args):
    if args.series_dir:
        return Path(args.series_dir).resolve()
    if args.episode_dir:
        episode_dir = Path(args.episode_dir).resolve()
        if episode_dir.parent.name == "episodes":
            return episode_dir.parent.parent
    return None


def episode_dirs_from_state(series_dir, state, args):
    if args.episode_dir:
        return [Path(args.episode_dir).resolve()]
    dirs = []
    for episode in (state or {}).get("episodes", []):
        episode_dir = episode.get("episode_dir")
        if episode_dir:
            dirs.append(Path(episode_dir).resolve())
    if dirs:
        return dirs
    episodes_root = series_dir / "episodes"
    return sorted(p.resolve() for p in episodes_root.glob("ep*") if p.is_dir())


def state_episode_for_dir(state, episode_dir):
    resolved = str(episode_dir.resolve())
    for episode in (state or {}).get("episodes", []):
        if episode.get("episode_dir") and str(Path(episode["episode_dir"]).resolve()) == resolved:
            return episode
    return None


def validate_episode(series_dir, episode_dir, state, strict, report):
    if not episode_dir.exists():
        report.fail(f"missing episode directory: {episode_dir}")
        return
    report.ok(f"episode directory exists: {episode_dir}")
    brief = load_json(episode_dir / "episode_brief.json", report, required=True)
    if brief:
        scan_for_secret_values(brief, report, episode_dir / "episode_brief.json")
    validate_narration(episode_dir, report)

    state_episode = state_episode_for_dir(state, episode_dir)
    status = state_episode.get("status") if state_episode else None
    if state_episode:
        if status not in VALID_STATUSES:
            report.fail(f"invalid production_state status {status!r}: {episode_dir}")
        else:
            report.ok(f"production_state status is valid: {status}")
        if status == "failed" and not state_episode.get("failed_reason"):
            report.fail(f"failed episode must record failed_reason: {episode_dir}")
    elif state is not None:
        report.fail(f"episode is missing from production_state.json: {episode_dir}")

    should_have_tts = strict or status in {"tts_done", "mp3_done"}
    should_have_mp3 = strict or status == "mp3_done"
    if should_have_tts:
        validate_tts_manifest(episode_dir, report, required=True)
    if should_have_mp3:
        validate_production_manifest(episode_dir, report, required=True)

    if state_episode and status == "mp3_done":
        for key in ["episode_mp3", "production_manifest", "voice", "tts_manifest"]:
            value = state_episode.get(key)
            if not value:
                report.fail(f"mp3_done state missing {key}: {episode_dir}")
            elif not Path(value).exists():
                report.fail(f"production_state path for {key} does not exist: {value}")


def main():
    parser = argparse.ArgumentParser(description="Validate podcast production handoff files.")
    parser.add_argument("--series-dir")
    parser.add_argument("--episode-dir")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    if not args.series_dir and not args.episode_dir:
        parser.error("provide --series-dir or --episode-dir")

    report = Report()
    series_dir = infer_series_dir(args)
    state = None
    if series_dir:
        series_plan = load_json(series_dir / "series_plan.json", report, required=bool(args.series_dir))
        if series_plan:
            scan_for_secret_values(series_plan, report, series_dir / "series_plan.json")
        state = load_json(series_dir / "production_state.json", report, required=bool(args.series_dir))
        if state:
            scan_for_secret_values(state, report, series_dir / "production_state.json")
        opening = series_dir / "opening_voice.wav"
        if args.series_dir and opening.exists():
            require_file(opening, report, "opening_voice.wav")
    else:
        report.warn("could not infer series directory; skipping series-level validation")

    episode_dirs = episode_dirs_from_state(series_dir, state, args) if series_dir else [Path(args.episode_dir).resolve()]
    if not episode_dirs:
        report.warn("no episode directories found")
    for episode_dir in episode_dirs:
        validate_episode(series_dir, episode_dir, state, args.strict, report)

    report.print()
    return 0 if report.success else 1


if __name__ == "__main__":
    sys.exit(main())
