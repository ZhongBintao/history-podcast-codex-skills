#!/usr/bin/env python3
import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from preflight_guard import assert_preflight_current


API_BASE = "https://api.weixin.qq.com"
ONE_MB = 1024 * 1024
UPLOAD_IMAGE_LIMIT = 950 * 1024
COVER_IMAGE_LIMIT = 9_500 * 1024
MAX_DIGEST_CHARS = 110
DEFAULT_ENV_FILE = Path.home() / ".claude" / "wechat.env"


def die(message, code=1):
    print(message, file=sys.stderr)
    raise SystemExit(code)


def load_env_file(path):
    path = Path(path).expanduser()
    if not path.exists():
        return
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def env_int(name, default):
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        die(f"{name} must be an integer, got: {raw}")


def request_json(url, *, method="GET", data=None, headers=None):
    body = None
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json; charset=utf-8", **(headers or {})}
        else:
            body = data
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        die(f"微信接口返回的不是 JSON: {raw[:500]}")
    if parsed.get("errcode") not in (None, 0):
        die(f"微信接口报错: {json.dumps(parsed, ensure_ascii=False)}")
    return parsed


def multipart_upload(url, field_name, file_path):
    file_path = Path(file_path)
    boundary = f"----claudewechat{int(time.time() * 1000)}"
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    data = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{file_path.name}"\r\n'
            ).encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            file_path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    return request_json(
        url,
        method="POST",
        data=data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )


def get_access_token(appid, appsecret):
    params = urllib.parse.urlencode(
        {"grant_type": "client_credential", "appid": appid, "secret": appsecret}
    )
    result = request_json(f"{API_BASE}/cgi-bin/token?{params}")
    token = result.get("access_token")
    if not token:
        die(f"没有拿到 access_token: {json.dumps(result, ensure_ascii=False)}")
    return token


def extract_main_inner(html):
    match = re.search(r"<main\b[^>]*>(.*)</main>", html, flags=re.S | re.I)
    return match.group(1).strip() if match else html


def compact_html_fragment(html):
    html = re.sub(r">\s+<", "><", html)
    html = re.sub(r"\n\s*\n+", "\n", html)
    return html.strip()


def find_image_sources(html):
    return re.findall(r'<img\b[^>]*\bsrc="([^"]+)"', html, flags=re.I)


def run_package_validator(article_dir, work_dir_arg, author):
    assert_preflight_current(article_dir, work_dir_arg)
    validator = Path(__file__).resolve().with_name("validate_wechat_article_package.py")
    subprocess.run(
        [
            sys.executable,
            str(validator),
            "--article-dir",
            str(article_dir),
            "--work-dir",
            work_dir_arg,
            "--author",
            author,
            "--require-html",
        ],
        check=True,
    )


def run_sips(src, dst, max_edge, quality):
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


def make_upload_safe_image(src, dst_dir):
    src = Path(src)
    dst_dir.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png"} and src.stat().st_size < UPLOAD_IMAGE_LIMIT:
        dst = dst_dir / src.name
        shutil.copy2(src, dst)
        return dst

    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", src.stem).strip("-") or "image"
    dst = dst_dir / f"{stem}.jpg"
    for max_edge, quality in [(1800, 82), (1600, 78), (1400, 74), (1200, 70), (1000, 66), (850, 62)]:
        run_sips(src, dst, max_edge, quality)
        if dst.stat().st_size < UPLOAD_IMAGE_LIMIT:
            return dst
    if dst.stat().st_size >= ONE_MB:
        die(f"图片压缩后仍超过微信正文图片 1MB 限制: {src}")
    return dst


def main():
    parser = argparse.ArgumentParser(description="Upload generated article HTML to WeChat draft box.")
    parser.add_argument("--article-dir", required=True)
    parser.add_argument("--work-dir", default=".wechat-work")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--author", default=os.environ.get("WECHAT_AUTHOR", "知识的小世界"))
    parser.add_argument("--content-source-url", default=os.environ.get("WECHAT_CONTENT_SOURCE_URL", ""))
    parser.add_argument("--need-open-comment", type=int, default=env_int("WECHAT_NEED_OPEN_COMMENT", 1))
    parser.add_argument("--only-fans-can-comment", type=int, default=env_int("WECHAT_ONLY_FANS_CAN_COMMENT", 0))
    args = parser.parse_args()

    load_env_file(args.env_file)
    if args.author == "知识的小世界":
        args.author = os.environ.get("WECHAT_AUTHOR", args.author)
    if not args.content_source_url:
        args.content_source_url = os.environ.get("WECHAT_CONTENT_SOURCE_URL", "")
    args.need_open_comment = env_int("WECHAT_NEED_OPEN_COMMENT", args.need_open_comment)
    args.only_fans_can_comment = env_int("WECHAT_ONLY_FANS_CAN_COMMENT", args.only_fans_can_comment)

    article_dir = Path(args.article_dir).resolve()
    work_dir = article_dir / args.work_dir
    run_package_validator(article_dir, args.work_dir, args.author)

    appid = os.environ.get("WECHAT_APPID")
    appsecret = os.environ.get("WECHAT_APPSECRET")
    if not appid or not appsecret:
        die("请通过环境变量提供 WECHAT_APPID 和 WECHAT_APPSECRET。")

    html_path = article_dir / "article.html"
    article_path = work_dir / "article.json"
    meta_path = work_dir / "meta.json"
    manifest_path = article_dir / "image_manifest.json"
    if not html_path.exists():
        die("article-dir 下必须存在 article.html。")
    if not article_path.exists():
        die(f"缺少结构化文章文件: {article_path}")
    if not manifest_path.exists():
        die(f"缺少图片清单文件: {manifest_path}")

    html = html_path.read_text("utf-8")
    article_data = json.loads(article_path.read_text("utf-8"))
    manifest = json.loads(manifest_path.read_text("utf-8"))
    meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else {}
    content_html = compact_html_fragment(extract_main_inner(html))
    title = meta.get("title") or article_data["title"]
    digest = re.sub(r"\s+", " ", str(meta.get("summary") or article_data["summary"])).strip()
    if len(digest) > MAX_DIGEST_CHARS:
        die(f"摘要超过微信安全长度 {MAX_DIGEST_CHARS} 字，请先缩短 summary: {len(digest)}")
    cover = meta.get("cover") or next((image for image in manifest if image.get("placement") == "cover"), {})
    cover_path = Path(cover.get("src") or cover.get("local_path") or "images/cover.jpg")
    if not cover_path.is_absolute():
        cover_path = article_dir / cover_path
    if not cover_path.exists():
        die(f"缺少可上传的本地封面图片: {cover_path}")
    if cover_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".gif"}:
        die(f"封面图片格式不适合微信上传，请先运行 prepare_wechat_images.py: {cover_path}")
    if cover_path.stat().st_size > COVER_IMAGE_LIMIT:
        die(f"封面图片超过微信安全大小 {COVER_IMAGE_LIMIT} bytes，请先运行 prepare_wechat_images.py: {cover_path}")

    if len(title) > 32:
        die(f"标题超过微信 32 字限制: {title}")
    if len(args.author) > 16:
        die(f"作者超过微信 16 字限制: {args.author}")

    upload_work_dir = article_dir / ".wechat-upload"
    upload_images_dir = upload_work_dir / "images"
    upload_images_dir.mkdir(parents=True, exist_ok=True)

    image_map = {}
    prepared_images = {}
    for src in sorted(set(find_image_sources(content_html))):
        if re.match(r"https?://", src):
            die(f"HTML 中不能引用远程图片，请先上传或改成本地图片: {src}")
        local = article_dir / src
        if not local.exists():
            die(f"HTML 中引用的图片不存在: {src}")
        prepared_images[src] = make_upload_safe_image(local, upload_images_dir)

    token = get_access_token(appid, appsecret)

    for src, prepared in prepared_images.items():
        url = f"{API_BASE}/cgi-bin/media/uploadimg?access_token={urllib.parse.quote(token)}"
        result = multipart_upload(url, "media", prepared)
        image_url = result.get("url")
        if not image_url:
            die(f"上传正文图片没有返回 url: {json.dumps(result, ensure_ascii=False)}")
        image_map[src] = {
            "wechat_url": image_url,
            "uploaded_bytes": prepared.stat().st_size,
        }
        content_html = content_html.replace(f'src="{src}"', f'src="{image_url}"')

    fragment_path = upload_work_dir / "article_wechat_fragment.html"
    fragment_path.write_text(content_html, "utf-8")
    if len(content_html) >= 20000:
        print(f"警告：HTML 片段字符数为 {len(content_html)}，接近或超过微信文档里的 2 万字符限制。", file=sys.stderr)

    if cover_path.stat().st_size > 10 * ONE_MB:
        die(f"封面永久素材超过 10MB: {cover_path}")
    thumb_url = f"{API_BASE}/cgi-bin/material/add_material?access_token={urllib.parse.quote(token)}&type=image"
    thumb_result = multipart_upload(thumb_url, "media", cover_path)
    thumb_media_id = thumb_result.get("media_id")
    if not thumb_media_id:
        die(f"上传封面永久素材没有返回 media_id: {json.dumps(thumb_result, ensure_ascii=False)}")

    article = {
        "article_type": "news",
        "title": title,
        "author": args.author,
        "digest": digest,
        "content": content_html,
        "thumb_media_id": thumb_media_id,
        "need_open_comment": args.need_open_comment,
        "only_fans_can_comment": args.only_fans_can_comment,
    }
    if args.content_source_url:
        article["content_source_url"] = args.content_source_url

    draft_url = f"{API_BASE}/cgi-bin/draft/add?access_token={urllib.parse.quote(token)}"
    draft_result = request_json(draft_url, method="POST", data={"articles": [article]})

    output = {
        "title": title,
        "author": args.author,
        "digest": digest,
        "article_dir": str(article_dir),
        "content_chars": len(content_html),
        "content_bytes": len(content_html.encode("utf-8")),
        "image_map": image_map,
        "thumb_media_id": thumb_media_id,
        "thumb_response": {k: v for k, v in thumb_result.items() if k != "url"},
        "draft_media_id": draft_result.get("media_id"),
        "draft_response": draft_result,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    result_path = article_dir / "wechat_upload_result.json"
    result_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), "utf-8")
    shutil.rmtree(upload_work_dir, ignore_errors=True)
    shutil.rmtree(work_dir, ignore_errors=True)
    print(json.dumps({"draft_media_id": output["draft_media_id"], "result_path": str(result_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
