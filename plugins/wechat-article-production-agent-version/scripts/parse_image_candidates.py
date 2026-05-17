#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path


REQUIRED_FIELDS = {
    "id",
    "role",
    "placement",
    "visual_need",
    "source_page_url",
    "image_url",
    "source_name",
    "creator",
    "license",
    "license_status",
    "local_path",
    "attempted_sources",
    "notes",
}
ALLOWED_ROLES = {"evidence", "explanation", "spatial_orientation", "pacing", "atmosphere"}
ALLOWED_LICENSE = {
    "open_license",
    "public_domain",
    "official_source_rights_unclear",
    "stock_license",
    "ai_generated",
    "not_found",
}


def die(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def none_if_null(value):
    value = clean_text(value)
    if value.lower() in {"", "null", "none", "无", "未找到"}:
        return None
    return value


def parse_sources(value):
    value = clean_text(value)
    if value.lower() in {"", "null", "none"}:
        return []
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            die(f"attempted_sources is not valid JSON/list text: {exc}")
        if not isinstance(parsed, list):
            die("attempted_sources must be a list.")
        return [clean_text(item) for item in parsed if clean_text(item)]
    return [clean_text(item) for item in re.split(r"[,，;；]", value) if clean_text(item)]


def format_line_error(source, line_number, line, reason):
    return (
        f"{source}:{line_number} 不是 key: value 单行字段。\n"
        f"原因：{reason}\n"
        "当前解析器要求每个字段写成一行，例如：\n"
        "attempted_sources: Wikimedia Commons, NASA\n"
        "notes: 找不到更贴近主题的可靠封面，使用公开授权馆藏图作为封面。\n"
        "不要写成 Markdown 列表、字段值换行或多行说明。\n"
        f"当前行：{line}"
    )


def parse_blocks(text, source):
    blocks = []
    current = None
    last_key = None
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("## "):
                if current is not None:
                    blocks.append(current)
                current = {"_heading": clean_text(line[3:]), "_line": line_number}
                last_key = None
            continue
        if current is None:
            die(f"{source}:{line_number} field appears before a ## image block.")
        if ":" not in line and "：" not in line:
            if line.startswith(("- ", "* ")):
                reason = "检测到 Markdown 列表项；attempted_sources 必须写成单行逗号分隔。"
            elif last_key:
                reason = f"上一字段 {last_key} 后面出现了续写行；字段值不允许换行。"
            else:
                reason = "缺少字段名和冒号。"
            die(format_line_error(source, line_number, line, reason))
        key, value = re.split(r"[:：]", line, maxsplit=1)
        key = clean_text(key)
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            die(
                format_line_error(
                    source,
                    line_number,
                    line,
                    f"字段名只能使用英文字母、数字和下划线，当前字段名是 {key}。",
                )
            )
        current[key] = clean_text(value)
        last_key = key
    if current is not None:
        blocks.append(current)
    if not blocks:
        die(f"{source} must contain at least one ## image block.")
    return blocks


def normalize_block(block, article_dir):
    missing = sorted(field for field in REQUIRED_FIELDS if field not in block)
    if missing:
        die(f"image block at line {block.get('_line')} missing fields: {', '.join(missing)}")

    role = clean_text(block["role"])
    license_status = clean_text(block["license_status"])
    local_path = none_if_null(block["local_path"])
    source_page_url = none_if_null(block["source_page_url"])
    image_url = none_if_null(block["image_url"])

    if role not in ALLOWED_ROLES:
        die(f"{block['id']} invalid role: {role}")
    if license_status not in ALLOWED_LICENSE:
        die(f"{block['id']} invalid license_status: {license_status}")
    if role == "evidence" and license_status in {"stock_license", "ai_generated"}:
        die(f"{block['id']} evidence image cannot use {license_status}.")
    if license_status == "not_found":
        access_status = "not_found"
        local_path = None
        fallback_reason = "no_reliable_candidate"
    else:
        access_status = "downloaded" if local_path else "skipped"
        fallback_reason = None
    if local_path:
        path = Path(local_path)
        if not path.is_absolute():
            path = article_dir / path
        if not path.exists():
            die(f"{block['id']} local_path does not exist: {local_path}")

    placement = clean_text(block["placement"])
    return {
        "id": clean_text(block["id"]),
        "type": clean_text(block.get("type")) or ("cover" if placement == "cover" else "body"),
        "role": role,
        "local_path": local_path,
        "caption": clean_text(block.get("caption")) or clean_text(block["visual_need"]),
        "placement": placement,
        "source_page_url": source_page_url,
        "image_url": image_url,
        "source_name": none_if_null(block["source_name"]),
        "creator": none_if_null(block["creator"]) or "Unknown",
        "license": none_if_null(block["license"]) or license_status,
        "license_status": license_status,
        "access_status": access_status,
        "fallback_reason": fallback_reason,
        "attempted_sources": parse_sources(block["attempted_sources"]),
        "notes": clean_text(block["notes"]),
    }


def validate_cover_contract(manifest, article_dir):
    covers = [image for image in manifest if clean_text(image.get("placement")) == "cover"]
    if not covers:
        die(
            "image_candidates.md 缺少封面块。每篇文章必须有且只有一个 placement: cover 的图片，"
            "它同时用于 HTML 正文开头封面和微信 API thumb_media_id。"
        )
    if len(covers) > 1:
        ids = ", ".join(clean_text(image.get("id")) or "<missing id>" for image in covers)
        die(f"image_candidates.md 只能有一个 placement: cover 的封面块，当前找到 {len(covers)} 个：{ids}")

    cover = covers[0]
    label = clean_text(cover.get("id")) or "cover"
    if clean_text(cover.get("type")) != "cover":
        die(f"{label} 是 placement: cover，但 type 必须是 cover。")
    if cover.get("license_status") == "not_found":
        die(f"{label} 是封面图，不能使用 license_status: not_found；封面必须有可上传的本地图片。")
    local_path = cover.get("local_path")
    if not local_path:
        die(f"{label} 是封面图，local_path 不能是 null。")
    if cover.get("access_status") != "downloaded":
        die(f"{label} 是封面图，access_status 必须是 downloaded。")
    path = Path(local_path)
    if not path.is_absolute():
        path = article_dir / path
    if not path.exists():
        die(f"{label} 封面图 local_path 指向的文件不存在：{local_path}")


def main():
    parser = argparse.ArgumentParser(description="Parse image_candidates.md into image_manifest.json.")
    parser.add_argument("--article-dir", required=True)
    parser.add_argument("--candidates", default="image_candidates.md")
    parser.add_argument("--manifest", default="image_manifest.json")
    args = parser.parse_args()

    article_dir = Path(args.article_dir).resolve()
    candidates_path = article_dir / args.candidates
    if not candidates_path.exists():
        die(f"Missing image candidates file: {candidates_path}")

    blocks = parse_blocks(candidates_path.read_text("utf-8"), candidates_path)
    manifest = [normalize_block(block, article_dir) for block in blocks]
    validate_cover_contract(manifest, article_dir)
    manifest_path = article_dir / args.manifest
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps({"image_manifest": str(manifest_path), "images": len(manifest)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
