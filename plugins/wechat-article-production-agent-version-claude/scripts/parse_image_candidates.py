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


def parse_blocks(text, source):
    blocks = []
    current = None
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("## "):
                if current is not None:
                    blocks.append(current)
                current = {"_heading": clean_text(line[3:]), "_line": line_number}
            continue
        if current is None:
            die(f"{source}:{line_number} field appears before a ## image block.")
        if ":" not in line and "：" not in line:
            die(f"{source}:{line_number} field must use key: value syntax.")
        key, value = re.split(r"[:：]", line, maxsplit=1)
        key = clean_text(key)
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            die(f"{source}:{line_number} invalid field name: {key}")
        current[key] = clean_text(value)
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
    manifest_path = article_dir / args.manifest
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps({"image_manifest": str(manifest_path), "images": len(manifest)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
