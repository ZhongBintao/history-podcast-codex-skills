import json
import time
from pathlib import Path


WATCHED_RELATIVE_PATHS = [
    "article_draft.md",
    "image_candidates.md",
    ".wechat-work/article.json",
    "image_manifest.json",
]


def latest_mtime(paths):
    latest = 0.0
    for path in paths:
        if path.exists():
            latest = max(latest, path.stat().st_mtime)
    return latest


def watched_paths(article_dir):
    article_dir = Path(article_dir)
    return [article_dir / rel for rel in WATCHED_RELATIVE_PATHS]


def assert_preflight_current(article_dir, work_dir=".wechat-work"):
    article_dir = Path(article_dir).resolve()
    preflight_path = article_dir / work_dir / "preflight.json"
    if not preflight_path.exists():
        raise SystemExit(
            f"Missing preflight: {preflight_path}. Run run_wechat_article_package.py --article-dir {article_dir}"
        )
    try:
        preflight = json.loads(preflight_path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid preflight JSON: {exc}") from exc
    completed_at = float(preflight.get("completed_at_epoch") or 0)
    latest = latest_mtime(watched_paths(article_dir))
    if latest > completed_at + 1e-6:
        raise SystemExit(
            "Article package changed after preflight. "
            f"Run run_wechat_article_package.py --article-dir {article_dir} again."
        )
    return preflight


def write_preflight(article_dir, work_dir=".wechat-work", author="知识的小世界"):
    article_dir = Path(article_dir).resolve()
    work_path = article_dir / work_dir
    work_path.mkdir(parents=True, exist_ok=True)
    article_path = work_path / "article.json"
    manifest_path = article_dir / "image_manifest.json"
    html_path = article_dir / "article.html"
    article = json.loads(article_path.read_text("utf-8"))
    manifest = json.loads(manifest_path.read_text("utf-8"))
    now = time.time()
    payload = {
        "status": "ok",
        "article_dir": str(article_dir),
        "completed_at_epoch": now,
        "completed_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "author": author,
        "summary_chars": len(str(article.get("summary") or "")),
        "summary_limit": 110,
        "files": {
            rel: (article_dir / rel).stat().st_mtime if (article_dir / rel).exists() else None
            for rel in WATCHED_RELATIVE_PATHS
        },
        "html": str(html_path) if html_path.exists() else None,
        "image_count": len(manifest) if isinstance(manifest, list) else None,
        "downloaded_images": sum(
            1
            for item in manifest
            if isinstance(item, dict) and item.get("access_status") == "downloaded"
        )
        if isinstance(manifest, list)
        else None,
    }
    preflight_path = work_path / "preflight.json"
    preflight_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return preflight_path
