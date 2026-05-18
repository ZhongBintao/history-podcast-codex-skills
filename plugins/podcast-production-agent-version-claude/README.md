# Podcast Production Agent Version (Claude Code 版本)

`podcast-production-agent-version` 是中文知识播客系列生产工作流，覆盖历史、科学、人文、旅行、商业、文化和自定义话题。

唯一对外入口是：

```text
podcast-series-showrunner
```

确认系列方案、season plan 和固定开场白后，你按 episode 生产 narration.txt、TTS 音频和 voice-only episode.mp3。

完整口播序列：

```text
固定 opening_voice.wav -> narration.txt 中的单集问候 -> 单集正文 -> 下集预告/系列告别 -> 再见
```

## Key Behavior

- `podcast-series-showrunner` 是唯一核心 skill。
- 你负责从策划、写作、TTS 到校验的完整流程。
- 真实 DashScope 凭证只能存在于 `DASHSCOPE_API_KEY` 环境变量；task packet 和 manifest 只能引用环境变量名。
- Episode 结构参考是创作提示，不是模板。你可以在核心问题和事实边界内调整叙事顺序、开场方式、节奏和解释风格。
- 每集 `narration.txt` 从 `opening_voice.wav` 之后、单集正文之前的自然问候开始。
- 非最后一集以轻量下集预告和告别收尾；最后一集或单集系列以系列告别和再见收尾。
- 外文词口语化处理：优先通用中文译名，无译名时自然音译并轻说明，音译别扭时用中文解释或绕开，保留 AI、DNA 等通用英文缩写。

## Production Readiness

开始生产前确认：

- `series_dir`
- 目标集数或范围
- 是否生成音频，默认 `true`
- `DASHSCOPE_API_KEY` 是否在环境变量中
- `opening_voice.wav` 是否已存在
- 是否强制生成 `fact_check.md`

## Included Scripts

从本插件根目录运行：

```text
scripts/cosyvoice_ws_tts.py
scripts/robust_episode_tts.py
scripts/build_episode.py
scripts/run_episode_pipeline.py
scripts/validate_production.py
```

TTS:

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

Build:

```bash
python3 scripts/build_episode.py \
  --opening-voice /absolute/path/to/series/opening_voice.wav \
  --voice /absolute/path/to/episode/voice.wav \
  --out-dir /absolute/path/to/episode \
  --episode-slug episode
```

Validate:

```bash
python3 scripts/validate_production.py --episode-dir /absolute/path/to/episode
```

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

`fact_check.md` 可选。

## Requirements

- Python 3.10+
- `ffmpeg` 和 `ffprobe`
- Python package `websockets`
- `DASHSCOPE_API_KEY` 用于音频生成