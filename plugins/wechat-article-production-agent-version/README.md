# WeChat Article Production Agent Version

`wechat-article-production-agent-version` converts completed Chinese podcast narration into WeChat Official Account article drafts.

The only user-facing skill is:

```text
wechat-article-pipeline
```

The workflow is now showrunner-first: the main agent coordinates the user, creates one directory per article, assigns one subagent per article, accepts only fixed Markdown contract files, and delegates final JSON generation, validation, rendering, and optional upload to deterministic scripts.

Agents do not hand-write final `article.json` or `image_manifest.json`. Those files are script products.

Subagents do not call this plugin again. They follow the main agent's task packet and deliver only `article_draft.md`, `image_candidates.md`, and local images.

## Default Flow

```text
narration.txt
→ article_draft.md + image_candidates.md + images/
→ parse_article_draft.py
→ parse_image_candidates.py
→ prepare_wechat_images.py
→ validate_wechat_article_package.py
→ render_wechat_html.py
→ validate_wechat_article_package.py --require-html
→ .wechat-work/preflight.json
→ optional WeChat draft upload by main agent only
```

The main command is:

```bash
python3 scripts/run_wechat_article_package.py --article-dir /absolute/path/to/article-dir
```

Use `--upload` only after the user requests upload and WeChat credentials are available.

## Markdown Contracts

`article_draft.md` must contain:

```markdown
# 标题
摘要：100-110 个中文字符以内的摘要。

## 小标题
自然段正文。
```

The `摘要：...` line is canonical. The same summary is rendered at the top of `article.html` and sent to WeChat as the draft `digest`.

`image_candidates.md` must contain one `##` block per image item with:

```text
id
role
placement
visual_need
source_page_url
image_url
source_name
creator
license
license_status
local_path
attempted_sources
notes
```

Every article must contain exactly one canonical cover block with `type: cover`, `placement: cover`, and a non-null `local_path` pointing to an existing local image. That same cover is rendered at the top of the HTML body and uploaded to WeChat as the draft cover/`thumb_media_id`.

When no reliable body image is found, keep a block with `license_status: not_found` and `local_path: null`. The cover image cannot use `not_found`.

Every image field must be written as one `key: value` line. Do not use Markdown lists or multi-line field values. Write sources like:

```text
attempted_sources: Wikimedia Commons, NASA, The Met
notes: 找不到更贴近主题的可靠封面，使用公开授权馆藏图作为封面。
```

Do not write:

```text
attempted_sources:
- Wikimedia Commons
- NASA
```

## Image Strategy

The image workflow prioritizes truthfulness, license transparency, local downloads, and WeChat-hosted final delivery.

- Authoritative open sources come first: Wikimedia Commons, NASA, NOAA, Smithsonian Open Access, The Met Open Access, Cleveland Museum of Art Open Access, museums, universities, libraries, archives, research institutions, government open data, and open galleries.
- Domestic official or institutional sources may be used as fallback or Chinese-topic supplement, but copyright uncertainty must be labeled honestly.
- Open stock galleries may only support `atmosphere` or `pacing`.
- AI-generated images are off by default, allowed only when explicitly requested, and must be marked `ai_generated`.
- Evidence images must never use `stock_license` or `ai_generated`.

Finding no reliable body image is acceptable. The manifest records a `not_found` placeholder instead of invented metadata. Finding no reliable cover must stop the package before upload.

## Included Scripts

Run from this plugin root:

```bash
python3 scripts/parse_article_draft.py --article-dir /absolute/path/to/article-dir
python3 scripts/parse_image_candidates.py --article-dir /absolute/path/to/article-dir
python3 scripts/run_wechat_article_package.py --article-dir /absolute/path/to/article-dir
python3 scripts/upload_wechat_draft.py --article-dir /absolute/path/to/article-dir
```

`run_wechat_article_package.py` is the normal production entrypoint for the main agent. It writes `.wechat-work/preflight.json` after the package validates and renders.

Standalone render/upload commands refuse to continue when Markdown, generated JSON, or manifest files are newer than preflight. Rerun the main package script after edits.

## WeChat Credentials

Credentials are read from environment variables first, then from:

```text
~/.codex/wechat.env
```

Required:

```env
WECHAT_APPID=
WECHAT_APPSECRET=
```

Defaults:

```env
WECHAT_AUTHOR=知识的小世界
WECHAT_CONTENT_SOURCE_URL=
WECHAT_NEED_OPEN_COMMENT=1
WECHAT_ONLY_FANS_CAN_COMMENT=0
```

The workflow creates a draft only. It must never publish, mass-send, delete, or modify existing WeChat assets.
