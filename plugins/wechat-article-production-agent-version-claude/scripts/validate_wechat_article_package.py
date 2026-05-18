#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path


MAX_TITLE_CHARS = 32
MAX_AUTHOR_CHARS = 16
MAX_DIGEST_CHARS = 110
REQUIRED_IMAGE_FIELDS = {
    "id",
    "type",
    "role",
    "local_path",
    "caption",
    "placement",
    "source_page_url",
    "image_url",
    "source_name",
    "creator",
    "license",
    "license_status",
    "access_status",
    "fallback_reason",
    "attempted_sources",
    "notes",
}
ALLOWED_ROLES = {"evidence", "explanation", "spatial_orientation", "pacing", "atmosphere"}
ALLOWED_ACCESS = {"downloaded", "timeout", "blocked", "not_found", "skipped"}
ALLOWED_LICENSE = {
    "open_license",
    "public_domain",
    "official_source_rights_unclear",
    "stock_license",
    "ai_generated",
    "not_found",
}
ALLOWED_FALLBACK = {
    None,
    "preferred_source_timeout",
    "preferred_source_blocked",
    "no_reliable_candidate",
    "license_unclear",
    "not_needed",
}


def clean_text(value):
    return re.sub(r"\s+", " ", "" if value is None else str(value)).strip()


def fail(errors, message):
    errors.append(message)


def load_json(path, errors):
    if not path.exists():
        fail(errors, f"Missing file: {path}")
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        fail(errors, f"Invalid JSON in {path}: {exc}")
        return None


def validate_article(article, article_path, author, errors):
    if not isinstance(article, dict):
        fail(errors, f"{article_path} must be a JSON object.")
        return
    for key in ("title", "summary", "sections"):
        if key not in article:
            fail(errors, f"article.json missing required field: {key}")
    title = clean_text(article.get("title"))
    summary = clean_text(article.get("summary"))
    if not title:
        fail(errors, "article.json title is empty.")
    if len(title) > MAX_TITLE_CHARS:
        fail(errors, f"title exceeds {MAX_TITLE_CHARS} characters: {len(title)}")
    if not summary:
        fail(errors, "article.json summary is empty.")
    if len(summary) > MAX_DIGEST_CHARS:
        fail(errors, f"summary exceeds safe WeChat digest limit {MAX_DIGEST_CHARS}: {len(summary)}")
    if len(clean_text(author)) > MAX_AUTHOR_CHARS:
        fail(errors, f"author exceeds {MAX_AUTHOR_CHARS} characters: {author}")

    sections = article.get("sections")
    if not isinstance(sections, list) or not sections:
        fail(errors, "article.json sections must be a non-empty array.")
        return
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            fail(errors, f"section {index} must be an object.")
            continue
        if not clean_text(section.get("heading")):
            fail(errors, f"section {index} heading is empty.")
        paragraphs = section.get("paragraphs")
        if not isinstance(paragraphs, list) or not paragraphs:
            fail(errors, f"section {index} paragraphs must be a non-empty array.")


def validate_manifest(manifest, manifest_path, article_dir, errors):
    if not isinstance(manifest, list):
        fail(errors, f"{manifest_path} must be a JSON array.")
        return
    for index, image in enumerate(manifest):
        label = f"image_manifest[{index}]"
        if not isinstance(image, dict):
            fail(errors, f"{label} must be an object.")
            continue
        missing = sorted(field for field in REQUIRED_IMAGE_FIELDS if field not in image)
        if missing:
            fail(errors, f"{label} missing fields: {', '.join(missing)}")

        role = image.get("role")
        access_status = image.get("access_status")
        license_status = image.get("license_status")
        fallback_reason = image.get("fallback_reason")
        if role not in ALLOWED_ROLES:
            fail(errors, f"{label} invalid role: {role}")
        if access_status not in ALLOWED_ACCESS:
            fail(errors, f"{label} invalid access_status: {access_status}")
        if license_status not in ALLOWED_LICENSE:
            fail(errors, f"{label} invalid license_status: {license_status}")
        if fallback_reason not in ALLOWED_FALLBACK:
            fail(errors, f"{label} invalid fallback_reason: {fallback_reason}")
        if not isinstance(image.get("attempted_sources"), list):
            fail(errors, f"{label} attempted_sources must be an array.")
        if role == "evidence" and license_status in {"stock_license", "ai_generated"}:
            fail(errors, f"{label} evidence image cannot use {license_status}.")

        local_path = image.get("local_path")
        if access_status == "downloaded":
            if not local_path:
                fail(errors, f"{label} is downloaded but local_path is empty.")
            else:
                path = Path(local_path)
                if not path.is_absolute():
                    path = article_dir / path
                if not path.exists():
                    fail(errors, f"{label} local_path does not exist: {local_path}")


def validate_html(html_path, article_dir, errors):
    if not html_path.exists():
        fail(errors, f"Missing HTML file: {html_path}")
        return
    html = html_path.read_text("utf-8")
    for src in re.findall(r'<img\b[^>]*\bsrc="([^"]+)"', html, flags=re.I):
        if re.match(r"https?://", src):
            fail(errors, f"HTML must not reference remote image URL: {src}")
            continue
        path = article_dir / src
        if not path.exists():
            fail(errors, f"HTML references missing image: {src}")


def main():
    parser = argparse.ArgumentParser(description="Validate a generated WeChat article package before render/upload.")
    parser.add_argument("--article-dir", required=True)
    parser.add_argument("--work-dir", default=".wechat-work")
    parser.add_argument("--author", default="知识的小世界")
    parser.add_argument("--require-html", action="store_true")
    args = parser.parse_args()

    article_dir = Path(args.article_dir).resolve()
    article_path = article_dir / args.work_dir / "article.json"
    manifest_path = article_dir / "image_manifest.json"
    html_path = article_dir / "article.html"
    errors = []

    article = load_json(article_path, errors)
    manifest = load_json(manifest_path, errors)
    if article is not None:
        validate_article(article, article_path, args.author, errors)
    if manifest is not None:
        validate_manifest(manifest, manifest_path, article_dir, errors)
    if args.require_html:
        validate_html(html_path, article_dir, errors)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)

    print(json.dumps({"status": "ok", "article_dir": str(article_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
