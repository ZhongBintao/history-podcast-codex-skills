#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path


MAX_SUMMARY_CHARS = 110


def die(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def compact_summary(value):
    return re.sub(r"\s+", "", str(value or "")).strip()


def parse_markdown(text, source):
    lines = text.splitlines()
    title = None
    summary = None
    sections = []
    current = None

    for line_number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("###"):
            die(f"{source}:{line_number} only level-1 title and level-2 section headings are allowed.")
        if line.startswith("# "):
            if title is not None:
                die(f"{source}:{line_number} contains more than one # title.")
            title = clean_text(line[2:])
            continue
        if line.startswith("## "):
            heading = clean_text(line[3:])
            if not heading:
                die(f"{source}:{line_number} section heading is empty.")
            current = {"heading": heading, "paragraphs": []}
            sections.append(current)
            continue
        if line.startswith("摘要：") or line.startswith("摘要:"):
            if summary is not None:
                die(f"{source}:{line_number} contains more than one summary line.")
            summary = compact_summary(line.split("：", 1)[1] if "：" in line else line.split(":", 1)[1])
            continue
        if line.startswith("- ") or line.startswith("* ") or re.match(r"^\d+[.)]\s+", line):
            die(f"{source}:{line_number} must not contain option lists, source lists, or notes.")
        if title is None:
            die(f"{source}:{line_number} content appears before # title.")
        if summary is None:
            die(f"{source}:{line_number} content appears before 摘要.")
        if current is None:
            die(f"{source}:{line_number} paragraph appears before the first ## section.")
        current["paragraphs"].append(clean_text(line))

    if not title:
        die(f"{source} is missing # 标题.")
    if not summary:
        die(f"{source} is missing 摘要：...")
    if len(summary) > MAX_SUMMARY_CHARS:
        die(f"summary exceeds {MAX_SUMMARY_CHARS} characters: {len(summary)}")
    if not sections:
        die(f"{source} must contain at least one ## section.")
    for index, section in enumerate(sections, 1):
        if not section["paragraphs"]:
            die(f"section {index} has no body paragraphs.")

    return {"title": title, "summary": summary, "sections": sections}


def main():
    parser = argparse.ArgumentParser(description="Parse article_draft.md into .wechat-work/article.json.")
    parser.add_argument("--article-dir", required=True)
    parser.add_argument("--draft", default="article_draft.md")
    parser.add_argument("--work-dir", default=".wechat-work")
    args = parser.parse_args()

    article_dir = Path(args.article_dir).resolve()
    draft_path = article_dir / args.draft
    if not draft_path.exists():
        die(f"Missing draft file: {draft_path}")

    article = parse_markdown(draft_path.read_text("utf-8"), draft_path)
    work_dir = article_dir / args.work_dir
    work_dir.mkdir(parents=True, exist_ok=True)
    output_path = work_dir / "article.json"
    output_path.write_text(json.dumps(article, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps({"article_json": str(output_path), "title": article["title"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
