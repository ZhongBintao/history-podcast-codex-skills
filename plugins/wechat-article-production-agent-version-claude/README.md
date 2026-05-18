# WeChat Article Production Agent Version (Claude Code 版本)

`wechat-article-production-agent-version` converts completed Chinese podcast narration into WeChat Official Account article drafts.

The only user-facing skill is:

```text
wechat-article-pipeline
```

The workflow is agent-first: you coordinate with the user, create one directory per article, write fixed Markdown contract files (article_draft.md + image_candidates.md), download local images, and delegate final JSON generation, validation, rendering, and optional upload to deterministic scripts.

Do not hand-write final `article.json` or `image_manifest.json`. Those files are script products.

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
→ optional WeChat draft upload when credentials are ready
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

When no reliable image is found, keep a block with `license_status: not_found` and `local_path: null`.

## Image Strategy

The image workflow prioritizes truthfulness, license transparency, local downloads, and WeChat-hosted final delivery.

- Authoritative open sources come first: Wikimedia Commons, NASA, NOAA, Smithsonian Open Access, The Met Open Access, Cleveland Museum of Art Open Access, museums, universities, libraries, archives, research institutions, government open data, and open galleries.
- Domestic official or institutional sources may be used as fallback or Chinese-topic supplement, but copyright uncertainty must be labeled honestly.
- Open stock galleries may only support `atmosphere` or `pacing`.
- AI-generated images are off by default, allowed only when explicitly requested, and must be marked `ai_generated`.
- Evidence images must never use `stock_license` or `ai_generated`.

Finding no reliable image is acceptable. The manifest records a `not_found` placeholder instead of invented metadata.

## Included Scripts

Run from this plugin root:

```bash
python3 scripts/parse_article_draft.py --article-dir /absolute/path/to/article-dir
python3 scripts/parse_image_candidates.py --article-dir /absolute/path/to/article-dir
python3 scripts/run_wechat_article_package.py --article-dir /absolute/path/to/article-dir
python3 scripts/upload_wechat_draft.py --article-dir /absolute/path/to/article-dir
```

`run_wechat_article_package.py` is the normal production entrypoint. It writes `.wechat-work/preflight.json` after the package validates and renders.

Standalone render/upload commands refuse to continue when Markdown, generated JSON, or manifest files are newer than preflight. Rerun the main package script after edits.

## WeChat Credentials

Credentials are read from environment variables first, then from:

```text
~/.claude/wechat.env
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
