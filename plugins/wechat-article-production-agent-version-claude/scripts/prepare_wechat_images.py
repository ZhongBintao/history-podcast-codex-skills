#!/usr/bin/env python3
import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


ONE_MB = 1024 * 1024

# WeChat Official Account API references:
# - draft/add:
#   https://developers.weixin.qq.com/doc/offiaccount/Draft_Box/Add_draft.html
# - media/uploadimg for article body images:
#   https://developers.weixin.qq.com/doc/offiaccount/Asset_Management/Adding_Permanent_Assets.html
# - material/add_material?type=image for cover images:
#   https://developers.weixin.qq.com/doc/offiaccount/Asset_Management/Adding_Permanent_Assets.html
BODY_IMAGE_LIMIT = 950 * 1024
COVER_IMAGE_LIMIT = 9_500 * 1024
BODY_STEPS = [(1800, 82), (1600, 78), (1400, 74), (1200, 70), (1000, 66), (850, 62)]
COVER_STEPS = [(3000, 86), (2600, 84), (2400, 82), (2000, 80), (1800, 76), (1600, 72)]
RASTER_ALLOWED = {"jpeg", "png", "gif"}


def die(message, code=1):
    print(message)
    raise SystemExit(code)


def load_json(path):
    try:
        return json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        die(f"Invalid JSON in {path}: {exc}")


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


def rel_path(path, article_dir):
    return path.resolve().relative_to(article_dir.resolve()).as_posix()


def safe_stem(image):
    raw = image.get("id") or Path(str(image.get("local_path") or "image")).stem
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(raw)).strip("-") or "image"


def append_note(image, note):
    current = str(image.get("notes") or "").strip()
    if note in current:
        return
    image["notes"] = f"{current} {note}".strip() if current else note


def detect_kind(path):
    head = path.read_bytes()[:512]
    stripped = head.lstrip().lower()
    if stripped.startswith(b"<!doctype html") or stripped.startswith(b"<html"):
        return "html"
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<error") or stripped.startswith(b"<errors"):
        return "xml"
    if stripped.startswith(b"<svg") or b"<svg" in stripped[:200]:
        return "svg"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    return "unknown"


def run_sips(src, dst, max_edge, quality):
    if not shutil.which("sips"):
        die("Missing macOS sips command; cannot prepare WeChat-compatible images.")
    subprocess.run(
        [
            "sips",
            "-s",
            "format",
            "jpeg",
            "-s",
            "formatOptions",
            str(quality),
            "-Z",
            str(max_edge),
            str(src),
            "--out",
            str(dst),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def rasterize_svg(src, tmp_dir):
    if not shutil.which("qlmanage"):
        die(f"SVG requires qlmanage for rasterization on this workflow: {src}")
    tmp_dir = Path(tmp_dir)
    subprocess.run(
        ["qlmanage", "-t", "-s", "2400", "-o", str(tmp_dir), str(src)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    candidates = sorted(tmp_dir.glob(f"{src.name}*.png")) + sorted(tmp_dir.glob("*.png"))
    if not candidates:
        die(f"qlmanage did not create a PNG thumbnail for SVG: {src}")
    return candidates[0]


def convert_to_limit(src, dst, steps, limit):
    for max_edge, quality in steps:
        run_sips(src, dst, max_edge, quality)
        if dst.stat().st_size <= limit:
            return dst
    die(f"Image could not be compressed below WeChat limit {limit} bytes: {src}")


def prepare_image(image, article_dir, prepared_dir):
    local_path = image.get("local_path")
    if not local_path:
        return False

    src = Path(local_path)
    if not src.is_absolute():
        src = article_dir / src
    if not src.exists():
        die(f"Manifest image does not exist: {local_path}")

    kind = detect_kind(src)
    if kind in {"html", "xml"}:
        die(f"Downloaded file is not an image ({kind} response): {src}")
    if kind == "unknown":
        die(f"Unsupported or unrecognized image file: {src}")

    placement = str(image.get("placement") or "")
    is_cover = placement == "cover" or str(image.get("type") or "") == "cover"
    limit = COVER_IMAGE_LIMIT if is_cover else BODY_IMAGE_LIMIT
    steps = COVER_STEPS if is_cover else BODY_STEPS

    if kind in RASTER_ALLOWED and src.stat().st_size <= limit:
        append_note(image, "微信图片预处理：原图格式和大小合规，保留原文件。")
        return False

    prepared_dir.mkdir(parents=True, exist_ok=True)
    dst = prepared_dir / f"{safe_stem(image)}.jpg"

    if kind == "svg":
        with tempfile.TemporaryDirectory() as tmp:
            raster = rasterize_svg(src, tmp)
            convert_to_limit(raster, dst, steps, limit)
        append_note(image, f"微信图片预处理：SVG 已转换为 JPG，限制 {limit} bytes。")
    else:
        convert_to_limit(src, dst, steps, limit)
        append_note(image, f"微信图片预处理：已转换/压缩为 JPG，限制 {limit} bytes。")

    image["local_path"] = rel_path(dst, article_dir)
    image["access_status"] = "downloaded"
    return True


def main():
    parser = argparse.ArgumentParser(description="Prepare local images for WeChat article upload.")
    parser.add_argument("--article-dir", required=True)
    parser.add_argument("--manifest", default="image_manifest.json")
    args = parser.parse_args()

    article_dir = Path(args.article_dir).resolve()
    manifest_path = article_dir / args.manifest
    if not manifest_path.exists():
        die(f"Missing manifest: {manifest_path}")

    manifest = load_json(manifest_path)
    if not isinstance(manifest, list):
        die("image_manifest.json must be a JSON array.")

    prepared_dir = article_dir / "images" / "prepared"
    changed = False
    processed = 0
    for image in manifest:
        if not isinstance(image, dict):
            die("Every image_manifest item must be a JSON object.")
        if image.get("license_status") == "not_found" or image.get("access_status") == "not_found":
            continue
        if image.get("local_path"):
            processed += 1
            changed = prepare_image(image, article_dir, prepared_dir) or changed

    save_json(manifest_path, manifest)
    print(json.dumps({"processed": processed, "changed": changed, "manifest": str(manifest_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
