# Podcast Production Agent Version (Codex 版本)

`podcast-production-agent-version` is a subagent-first workflow for producing Chinese knowledge podcast series across history, science, humanities, travel, business, culture, and custom topics.

The only user-facing skill is:

```text
podcast-series-showrunner
```

After the user confirms the series concept, season plan, and fixed opening voice, the showrunner collects production readiness information and creates one standard task packet per target episode. Each episode subagent owns only its own `episode_dir`; the main agent owns validation, status reporting, and `production_state.json`.

The complete spoken sequence is:

```text
fixed opening_voice.wav -> episode greeting in narration.txt -> episode body -> preview/farewell closing -> goodbye
```

## Key Behavior

- `podcast-series-showrunner` is the only core skill.
- One episode is produced by one episode subagent.
- Multiple episodes may be produced by multiple episode subagents in parallel.
- Subagents must not modify `production_state.json`.
- The main agent validates outputs and updates `production_state.json`.
- Real DashScope credentials must stay in `DASHSCOPE_API_KEY`; task packets and manifests may reference only the environment variable name.
- Episode structure references are creative prompts, not templates. Subagents may adjust narrative order, opening approach, pacing, and explanation style within the confirmed core question and factual boundaries.
- Each episode's `narration.txt` starts with a natural host greeting after `opening_voice.wav` and before the episode body.
- Non-final episodes close with a light next-episode preview and goodbye; final or single-episode series close with a series farewell and goodbye.
- Episode subagents own foreign-term spoken-form choices: common Chinese translation first, natural transliteration with a light explanation when needed, Chinese explanation or omission when transliteration is awkward, and common English abbreviations such as `AI` and `DNA` may remain.

## Foreign-Term Spoken Forms

Before writing `narration.txt`, each episode subagent identifies foreign names, places, terms, titles, and institutions that may affect pronunciation, comprehension, or accuracy.

The spoken script should prioritize listener comfort and TTS stability:

- Use common Chinese translations when they exist, such as `亚里士多德` for `Aristotle`.
- Use natural transliteration when there is no common Chinese translation, and lightly explain that it is a transliteration or temporary translation when useful.
- Use a Chinese explanation, descriptive phrase, or avoid the original term when transliteration sounds awkward or interrupts comprehension.
- Keep familiar English abbreviations and naturalized English terms when they are common in Chinese speech, such as `AI`, `DNA`, `CEO`, `IP`, `App`, and `CPU`.
- Keep phonetic symbols, spelling instructions, SSML, TTS tags, and production notes out of `narration.txt`.

The subagent records only important choices in `episode_brief.json.foreign_terms_to_review`; it is not a full foreign-term list. Source spelling and detailed notes can also live in `fact_check.md` when needed.

## Production Readiness

Before dispatching episode subagents, the showrunner confirms:

- `series_dir`
- episode number(s) or range
- whether to generate audio, default `true`
- whether `DASHSCOPE_API_KEY` is available in the environment
- whether `opening_voice.wav` exists
- whether to force `fact_check.md`

## Included Scripts

Run from this plugin root:

```text
scripts/cosyvoice_ws_tts.py
scripts/robust_episode_tts.py
scripts/build_episode.py
scripts/run_episode_pipeline.py
scripts/validate_production.py
```

Episode subagents normally use:

```bash
DASHSCOPE_API_KEY="$DASHSCOPE_API_KEY" python3 scripts/cosyvoice_ws_tts.py \
  --narration /absolute/path/to/episode/narration.txt \
  --out-dir /absolute/path/to/episode \
  --output-prefix voice \
  --manifest-name tts_manifest.json \
  --model cosyvoice-v3-flash \
  --voice longsanshu_v3 \
  --send-mode combined \
  --max-chars-per-task 10000 \
  --chunk-silence-ms 0 \
  --tail-silence-ms 3500
```

Then:

```bash
python3 scripts/build_episode.py \
  --opening-voice /absolute/path/to/series/opening_voice.wav \
  --voice /absolute/path/to/episode/voice.wav \
  --out-dir /absolute/path/to/episode \
  --episode-slug episode
```

Finally:

```bash
python3 scripts/validate_production.py --episode-dir /absolute/path/to/episode
```

`scripts/run_episode_pipeline.py` is retained for main-agent controlled deterministic runs. It updates `production_state.json`, so it is not the default subagent command.

## Output Shape

```text
<series-folder>/
├── series_plan.json
├── series_opening_voice.md
├── series_opening_voice.json
├── opening_voice_narration.txt
├── opening_voice.wav
├── opening_voice_tts_manifest.json
├── production_state.json
└── episodes/
    └── ep01-<slug>/
        ├── episode_brief.json
        ├── narration.txt
        ├── fact_check.md
        ├── voice.wav
        ├── voice_timeline_raw.json
        ├── voice_timeline_compact.json
        ├── tts_manifest.json
        ├── episode.mp3
        └── production_manifest.json
```

`fact_check.md` is optional.

## Requirements

- Python 3.10+
- `ffmpeg` and `ffprobe`
- Python package `websockets`
- `DASHSCOPE_API_KEY` for audio generation
