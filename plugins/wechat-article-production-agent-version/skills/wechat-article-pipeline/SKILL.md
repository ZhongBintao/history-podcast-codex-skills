---
name: wechat-article-pipeline
description: Primary and only external entrypoint for converting a completed Chinese podcast narration file, especially narration.txt, into a WeChat Official Account article draft. This agent-version workflow uses a main showrunner agent, one article subagent per article, fixed Markdown delivery contracts, deterministic JSON conversion, package validation, rendering, and optional draft upload.
metadata:
  short-description: 口播稿到公众号草稿，总控 + subagent + Markdown 契约
---

# WeChat Article Pipeline

## Purpose

Use this as the only user-facing entrypoint for turning completed Chinese podcast narration into WeChat Official Account article drafts.

This is a showrunner workflow. The main agent owns user communication, input/output directories, subagent assignment, package acceptance, deterministic scripts, credential checks, upload decisions, and final reporting. Each article is drafted by one subagent in its own article directory.

JSON remains an internal script interface only. Agents must not directly hand-write final `article.json` or `image_manifest.json`.

## Default Workflow

Single article:

```text
narration.txt
→ main agent creates article_dir
→ one subagent writes article_draft.md + image_candidates.md + images/
→ main agent runs run_wechat_article_package.py --article-dir ...
→ scripts parse Markdown into JSON
→ scripts prepare images, validate, render article.html, write preflight
→ main agent uploads only when user requests and credentials are available
```

Batch articles:

```text
wechat-batch/
├── batch_state.json
├── article-001/
│   ├── input/narration.txt
│   ├── article_draft.md
│   ├── image_candidates.md
│   ├── .wechat-work/article.json
│   ├── .wechat-work/preflight.json
│   ├── images/
│   ├── image_manifest.json
│   ├── article.html
│   └── wechat_upload_result.json
└── article-002/
```

`batch_state.json` is maintained only by the main agent. Allowed statuses are `assigned`, `draft_ready`, `validated`, `rendered`, `uploaded`, and `failed`.

## Subagent Contract

For each article, the subagent may read the narration, craft the article, search and judge image value, and download reliable local images. The subagent must deliver exactly these Markdown contract files:

```text
article_draft.md
image_candidates.md
images/
```

The subagent must not upload to WeChat, read AppSecret, write global state, update `batch_state.json`, delete shared files, or directly create final JSON files.

The subagent must not invoke `wechat-article-pipeline` again. It follows the task packet from the main agent and delivers only Markdown contract files plus local images.

## Article Markdown Contract

`article_draft.md` must use this shape:

```markdown
# 标题
摘要：100-110 个中文字符以内的摘要。

## 小标题
自然段正文。

另一个自然段。
```

Rules:

- Use exactly one `#` title.
- Use one `摘要：...` line.
- Keep summary at or below 110 characters.
- The summary is canonical: it is rendered again at the top of the HTML body and is also sent to the WeChat API as `digest`.
- Use one or more `##` sections.
- Section bodies are natural paragraphs, not bullet lists.
- Do not include title options, writing notes, source lists, internal comments, or JSON.
- Preserve the narration's facts, logic, viewpoint, tone, uncertainty markers, and necessary personal expression.
- Remove口播-only repetition, filler transitions, recording/TTS artifacts, and excessive rhythm words.
- Do not add facts, numbers, people, concepts, sources, quotes, scenes, psychology, or causal claims absent from the narration.

The main agent converts it with:

```bash
python3 scripts/parse_article_draft.py --article-dir /absolute/path/to/article-dir
```

## Image Markdown Contract

`image_candidates.md` contains one `##` block per image need or final image. Every block must include these fields, one field per line:

```markdown
## img-001
id: img-001
type: cover
role: evidence
placement: cover
visual_need: 需要什么画面或图表
source_page_url: https://...
image_url: https://...
source_name: Wikimedia Commons
creator: Unknown
license: CC BY-SA 4.0
license_status: open_license
local_path: images/img-001.jpg
attempted_sources: Wikimedia Commons, NASA
notes: 用于说明...
```

Every article must contain exactly one canonical cover block:

- `type: cover`
- `placement: cover`
- `local_path` must point to an existing local image file
- `license_status` must not be `not_found`

The cover is canonical: the same local image is rendered at the top of the HTML body and uploaded to WeChat as the draft cover/`thumb_media_id`.

When no reliable image is found, still write the block:

```markdown
license_status: not_found
local_path: null
source_page_url: null
image_url: null
```

This not-found placeholder is allowed for body images only. The cover image cannot be `not_found`.

The Markdown contract is intentionally strict:

- Every field value must fit on the same line as `key: value`.
- Do not use Markdown bullet lists.
- Do not continue `notes` or any other field on the next line.
- `attempted_sources` must be a comma-separated single line or a JSON array string.

Correct:

```text
attempted_sources: Wikimedia Commons, NASA, The Met
notes: 找不到更贴近主题的可靠封面，使用公开授权馆藏图作为封面。
```

Wrong:

```text
attempted_sources:
- Wikimedia Commons
- NASA
```

Allowed roles are `evidence`, `explanation`, `spatial_orientation`, `pacing`, and `atmosphere`.

Allowed `license_status` values are `open_license`, `public_domain`, `official_source_rights_unclear`, `stock_license`, `ai_generated`, and `not_found`.

Evidence images must never use `stock_license` or `ai_generated`.

The main agent converts it with:

```bash
python3 scripts/parse_image_candidates.py --article-dir /absolute/path/to/article-dir
```

## Image Strategy

Use sources in this priority order:

- Foreign authoritative open sources: Wikimedia Commons, NASA, NOAA, Smithsonian Open Access, The Met Open Access, Cleveland Museum of Art Open Access, museums, universities, libraries, archives, research institutions, government open data, and open galleries.
- Domestic official or institutional sources as fallback or Chinese-topic supplement, with copyright uncertainty labeled honestly.
- Open stock galleries only for `atmosphere` or `pacing`.
- AI-generated images only when the user explicitly asks, marked as `ai_generated`, and never represented as evidence.

For each image need, try at most three high-quality candidate sources and the same URL at most once. If a source is blocked, times out, has unclear rights, or lacks a reliable image, move on. If no reliable image remains, write `license_status: not_found`.

Finding no reliable body image is acceptable. Faking source metadata is a workflow failure. Finding no cover means the package must fail before upload instead of using an unrelated substitute.

## Main Script

The main agent should run one command per article:

```bash
python3 scripts/run_wechat_article_package.py \
  --article-dir /absolute/path/to/article-dir
```

This enforces the order:

1. Require `article_draft.md` and `image_candidates.md`.
2. Parse Markdown to `.wechat-work/article.json` and `image_manifest.json`.
3. Run `prepare_wechat_images.py`.
4. Run `validate_wechat_article_package.py`.
5. Render `article.html`.
6. Validate again with HTML checks.
7. Write `.wechat-work/preflight.json`.

Use `--upload` only when the user asked for upload and credentials are available.

## Preflight

Successful package runs write `.wechat-work/preflight.json`. It records validation time, summary limit, article/manifest file timestamps, HTML output, and image counts.

Standalone `render_wechat_html.py` and `upload_wechat_draft.py` refuse to continue when `article_draft.md`, `image_candidates.md`, `.wechat-work/article.json`, or `image_manifest.json` changed after preflight. Rerun `run_wechat_article_package.py` after any manual edit.

## WeChat Credentials

At upload time, read existing environment variables first, then `~/.codex/wechat.env`:

```env
WECHAT_APPID=
WECHAT_APPSECRET=
WECHAT_AUTHOR=知识的小世界
WECHAT_CONTENT_SOURCE_URL=
WECHAT_NEED_OPEN_COMMENT=1
WECHAT_ONLY_FANS_CAN_COMMENT=0
```

If `WECHAT_APPID` or `WECHAT_APPSECRET` is missing, ask the user for the missing value before upload. Do not write AppSecret into the repository, generated article files, JSON outputs, logs, task packets, or final responses.

The workflow creates a draft only. Never publish, mass-send, delete, or modify existing WeChat assets.

## Validation Checklist

Before final reporting, verify:

- `wechat-article-pipeline` remains the only user-facing skill in this plugin.
- Subagents delivered only Markdown contracts and local images.
- JSON files were generated by scripts with `json.dumps(..., ensure_ascii=False, indent=2)`.
- `run_wechat_article_package.py --article-dir ...` completed successfully.
- `article.html` exists and has no remote image URLs.
- Not-found image needs are represented with `license_status: not_found`.
- Evidence images do not use open stock galleries or AI-generated images.
- Upload, when requested, was performed only by the main agent.

## Completion Report

After success, report only the relevant outputs:

- `article.html`
- `image_manifest.json`
- `images/`
- `.wechat-work/preflight.json`
- `wechat_upload_result.json` and draft media id when upload happened
