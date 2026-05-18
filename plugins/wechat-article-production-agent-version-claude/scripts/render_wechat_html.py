#!/usr/bin/env python3
import argparse
import html
import json
import re
from pathlib import Path

from preflight_guard import assert_preflight_current


def clean_text(value):
    text = "" if value is None else str(value)
    text = text.replace("封面图候选：", "封面图：")
    text = text.replace("封面图候选:", "封面图：")
    text = text.replace("封面图候选", "封面图")
    return re.sub(r"\s+", " ", text).strip()


def esc(value):
    return html.escape(clean_text(value), quote=True)


def inline(value):
    return esc(value)


def paragraph_html(text):
    return (
        '<p style="margin: 0 0 1.15em; color: #2d2a26; font-size: 16px; '
        'line-height: 1.9; text-align: justify; word-break: break-word;">'
        f"{inline(text)}</p>"
    )


def h2_html(text):
    return (
        '<h2 style="margin: 2.25em 0 1em; padding-left: 0.75em; '
        'border-left: 4px solid #9b3d2e; color: #171513; font-size: 20px; '
        f'line-height: 1.45; font-weight: 700; letter-spacing: 0;">{inline(text)}</h2>'
    )


def image_src(image, article_dir):
    local_path = image.get("local_path")
    if not local_path:
        return ""
    path = Path(local_path)
    if path.is_absolute():
        path = path.resolve()
        article_dir = article_dir.resolve()
        try:
            return path.relative_to(article_dir).as_posix()
        except ValueError:
            return path.as_posix()
    return path.as_posix()


def figure_html(image, article_dir, cover=False):
    if not image or not image.get("local_path"):
        return ""
    src = image_src(image, article_dir)
    if not src:
        return ""
    fig_style = "margin: 1.3em 0 1.45em;" if cover else "margin: 2.15em 0 2.05em;"
    img_style = (
        "display: block; width: 100%; height: auto; border-radius: 4px;"
        if cover
        else "display: block; width: 100%; height: auto; border-radius: 3px;"
    )
    caption = clean_text(image.get("caption") or image.get("id") or "")
    alt = caption or clean_text(image.get("type") or "article image")
    return (
        f'<figure style="{fig_style}">'
        f'<img src="{esc(src)}" alt="{esc(alt)}" style="{img_style}"/>'
        '<figcaption style="margin: 0.72em 0 0; color: #7a736a; font-size: 13px; '
        f'line-height: 1.65; text-align: center;">{inline(caption)}</figcaption></figure>'
    )


def source_item(image):
    label = clean_text(image.get("caption") or image.get("id") or "图片")
    source_name = clean_text(image.get("source_name") or "来源未标明")
    creator = clean_text(image.get("creator") or "Unknown")
    license_text = clean_text(image.get("license") or image.get("license_status") or "unknown")
    return f"{label} 来源：{source_name}，作者/机构：{creator}，授权：{license_text}。"


def validate_article(article):
    if not isinstance(article, dict):
        raise SystemExit("article.json must be a JSON object.")
    for key in ("title", "summary", "sections"):
        if key not in article:
            raise SystemExit(f"article.json is missing required field: {key}")
    if not clean_text(article["title"]):
        raise SystemExit("article.json title is empty.")
    if not clean_text(article["summary"]):
        raise SystemExit("article.json summary is empty.")
    if not isinstance(article["sections"], list) or not article["sections"]:
        raise SystemExit("article.json sections must be a non-empty array.")
    for index, section in enumerate(article["sections"]):
        if not isinstance(section, dict):
            raise SystemExit(f"section {index} must be an object.")
        if not clean_text(section.get("heading")):
            raise SystemExit(f"section {index} heading is empty.")
        paragraphs = section.get("paragraphs")
        if not isinstance(paragraphs, list) or not paragraphs:
            raise SystemExit(f"section {index} paragraphs must be a non-empty array.")


def images_by_placement(manifest):
    grouped = {}
    for image in manifest:
        placement = clean_text(image.get("placement") or "")
        if not placement:
            continue
        grouped.setdefault(placement, []).append(image)
    return grouped


def pop_figures(grouped, placement, article_dir, cover=False):
    images = grouped.pop(placement, [])
    return [figure_html(image, article_dir, cover=cover) for image in images if image.get("local_path")]


def build_body(article, manifest, article_dir):
    grouped = images_by_placement(manifest)
    body = []
    cover = None
    cover_images = grouped.pop("cover", [])
    if cover_images:
        cover = cover_images[0]

    body.extend(pop_figures(grouped, "after_summary", article_dir))

    for section in article["sections"]:
        heading = clean_text(section["heading"])
        body.extend(pop_figures(grouped, f"before_section:{heading}", article_dir))
        body.append(h2_html(heading))
        paragraphs = section["paragraphs"]
        for index, paragraph in enumerate(paragraphs):
            body.append(paragraph_html(paragraph))
            body.extend(pop_figures(grouped, f"after_paragraph:{heading}:{index}", article_dir))
        body.extend(pop_figures(grouped, f"after_section:{heading}", article_dir))

    # Render any valid but unmatched non-cover images at the end of the body instead of dropping them.
    for placement in sorted(grouped):
        if placement == "cover":
            continue
        body.extend(pop_figures(grouped, placement, article_dir))

    return cover, [item for item in body if item]


def render(article, manifest, article_dir):
    cover, body_items = build_body(article, manifest, article_dir)
    body = "\n      ".join(body_items)
    source_items = "\n".join(
        '<li style="margin: 0 0 0.75em; padding-left: 0.1em; color: #6f675f; '
        f'font-size: 13px; line-height: 1.75;">{inline(source_item(image))}</li>'
        for image in manifest
        if image.get("local_path") or image.get("license_status") == "not_found"
    )
    sources_section = ""
    if source_items:
        sources_section = f"""
      <section style="margin: 2.7em 0 0; padding: 1.15em 1em 0.35em; border-top: 1px solid #ded6ca;">
        <h2 style="margin: 0 0 0.85em; color: #4b453f; font-size: 16px; line-height: 1.45; font-weight: 700;">图片来源</h2>
        <ol style="margin: 0; padding-left: 1.35em;">{source_items}</ol>
      </section>"""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{esc(article["title"])}</title>
</head>
<body style="margin: 0; padding: 0; background: #f6f2ea; font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', Arial, sans-serif; color: #2d2a26;">
  <main style="max-width: 680px; margin: 0 auto; padding: 0; background: #fffdf8;">
    <section style="padding: 18px 18px 14px;">
      {figure_html(cover, article_dir, True)}
      <p style="margin: 0 0 1.5em; padding: 1.05em 1.1em; border: 1px solid #d8d2c8; border-radius: 4px; background: #ffffff; box-shadow: 0 6px 18px rgba(31, 28, 24, 0.06); color: #4f5961; font-size: 15px; line-height: 1.85; text-align: justify;">{inline(article["summary"])}</p>
    </section>
    <section style="padding: 0 18px 20px;">
      {body}{sources_section}
    </section>
  </main>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Render WeChat-compatible HTML from article.json and image_manifest.json.")
    parser.add_argument("--article-dir", required=True)
    parser.add_argument("--article-json")
    parser.add_argument("--work-dir", default=".wechat-work")
    parser.add_argument("--skip-preflight-check", action="store_true")
    args = parser.parse_args()

    article_dir = Path(args.article_dir).resolve()
    work_dir = article_dir / args.work_dir
    if not args.skip_preflight_check:
        assert_preflight_current(article_dir, args.work_dir)
    article_path = Path(args.article_json).resolve() if args.article_json else work_dir / "article.json"
    manifest_path = article_dir / "image_manifest.json"
    if not article_path.exists():
        raise SystemExit(f"Missing {article_path}")
    if not manifest_path.exists():
        raise SystemExit(f"Missing {manifest_path}")

    article = json.loads(article_path.read_text("utf-8"))
    manifest = json.loads(manifest_path.read_text("utf-8"))
    if not isinstance(manifest, list):
        raise SystemExit("image_manifest.json must be a JSON array.")
    validate_article(article)

    html_text = re.sub(r"\n\s*\n+", "\n", render(article, manifest, article_dir)).strip() + "\n"
    html_path = article_dir / "article.html"
    meta_path = work_dir / "meta.json"
    work_dir.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, "utf-8")

    cover = next(
        (
            image
            for image in manifest
            if clean_text(image.get("placement")) == "cover" and image.get("local_path")
        ),
        None,
    )
    cover_meta = None
    if cover:
        cover_meta = {
            "src": image_src(cover, article_dir),
            "local_path": cover.get("local_path"),
            "caption": clean_text(cover.get("caption")),
            "source_page_url": cover.get("source_page_url"),
            "license": cover.get("license"),
            "license_status": cover.get("license_status"),
        }

    meta = {
        "title": clean_text(article["title"]),
        "summary": clean_text(article["summary"]),
        "cover": cover_meta,
        "source_article": str(article_path),
        "image_manifest": str(manifest_path),
        "html_file": str(html_path),
        "generated_for": "wechat_html_preview",
        "images_are_local": True,
        "note": "正文图片仍为本地路径，正式创建微信草稿前需要上传到微信并替换 URL。",
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"html": str(html_path), "meta": str(meta_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
