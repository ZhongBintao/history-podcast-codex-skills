#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

from preflight_guard import write_preflight


def run_step(args):
    subprocess.run([sys.executable, *[str(item) for item in args]], check=True)


def main():
    parser = argparse.ArgumentParser(description="Build, validate, render, and optionally upload one WeChat article package.")
    parser.add_argument("--article-dir", required=True)
    parser.add_argument("--work-dir", default=".wechat-work")
    parser.add_argument("--author", default="知识的小世界")
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()

    article_dir = Path(args.article_dir).resolve()
    script_dir = Path(__file__).resolve().parent
    draft = article_dir / "article_draft.md"
    candidates = article_dir / "image_candidates.md"
    if not draft.exists():
        raise SystemExit(f"Missing required Markdown contract file: {draft}")
    if not candidates.exists():
        raise SystemExit(f"Missing required Markdown contract file: {candidates}")

    run_step([script_dir / "parse_article_draft.py", "--article-dir", article_dir, "--work-dir", args.work_dir])
    run_step([script_dir / "parse_image_candidates.py", "--article-dir", article_dir])
    run_step([script_dir / "prepare_wechat_images.py", "--article-dir", article_dir])
    run_step(
        [
            script_dir / "validate_wechat_article_package.py",
            "--article-dir",
            article_dir,
            "--work-dir",
            args.work_dir,
            "--author",
            args.author,
        ]
    )
    run_step(
        [
            script_dir / "render_wechat_html.py",
            "--article-dir",
            article_dir,
            "--work-dir",
            args.work_dir,
            "--skip-preflight-check",
        ]
    )
    run_step(
        [
            script_dir / "validate_wechat_article_package.py",
            "--article-dir",
            article_dir,
            "--work-dir",
            args.work_dir,
            "--author",
            args.author,
            "--require-html",
        ]
    )
    preflight_path = write_preflight(article_dir, args.work_dir, args.author)
    result = {"status": "ok", "article_dir": str(article_dir), "preflight": str(preflight_path)}

    if args.upload:
        run_step(
            [
                script_dir / "upload_wechat_draft.py",
                "--article-dir",
                article_dir,
                "--work-dir",
                args.work_dir,
                "--author",
                args.author,
            ]
        )
        result["uploaded"] = True

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
