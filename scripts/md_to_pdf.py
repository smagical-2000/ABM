"""Render a Markdown doc to a polished PDF with the local screenshots embedded.

markdown -> styled HTML (written next to the .md so ./images/*.png resolve) -> Chromium
prints it to PDF via Playwright. No external services, no auth: everything is local.

    python3 scripts/md_to_pdf.py docs/discovery.md docs/discovery.pdf
"""
import re
import sys
from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright

CSS = """
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body { font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif; color: #18181b;
       line-height: 1.55; font-size: 12px; }
h1 { font-size: 25px; font-weight: 700; margin: 0 0 .5em; letter-spacing: -.02em; }
h2 { font-size: 18px; font-weight: 600; margin: 1.7em 0 .5em; padding-bottom: 5px;
     border-bottom: 2px solid #6366f1; page-break-after: avoid; letter-spacing: -.01em; }
h3 { font-size: 14px; font-weight: 600; margin: 1.3em 0 .4em; page-break-after: avoid; }
p { margin: .55em 0; }
a { color: #4f46e5; text-decoration: none; }
strong { font-weight: 600; }
ul, ol { margin: .5em 0; padding-left: 1.3em; }
li { margin: .25em 0; }
table { border-collapse: collapse; width: 100%; font-size: 11px; margin: 12px 0;
        page-break-inside: avoid; }
th, td { border: 1px solid #e4e4e7; padding: 7px 10px; text-align: left; vertical-align: top; }
th { background: #f4f4f5; font-weight: 600; }
tr:nth-child(even) td { background: #fafafa; }
img { max-width: 100%; border: 1px solid #e4e4e7; border-radius: 8px; margin: 12px 0;
      page-break-inside: avoid; display: block; }
pre { background: #1e1e2e; color: #e4e4e7; padding: 14px 16px; border-radius: 8px;
      overflow-x: auto; font-size: 10.5px; line-height: 1.45; page-break-inside: avoid; }
code { font-family: ui-monospace, 'SF Mono', Menlo, monospace; font-size: 11px; }
p code, li code, td code { background: #f4f4f5; padding: 1px 5px; border-radius: 4px; }
blockquote { border-left: 3px solid #6366f1; margin: 12px 0; padding: 6px 14px;
             color: #3f3f46; background: #f8f8fc; border-radius: 0 6px 6px 0; }
hr { border: none; border-top: 1px solid #e4e4e7; margin: 1.6em 0; }
em { color: #52525b; }
"""


_ITEM = re.compile(r"^\s*(?:[-*]\s|\d+\.\s)")


def _space_lists(raw: str) -> str:
    """Insert a blank line before a list that directly follows a non-blank,
    non-list line (e.g. a bold checklist header). Python-Markdown needs that
    blank line to start the list, otherwise the items render run-on inline."""
    lines = raw.split("\n")
    out: list[str] = []
    for line in lines:
        if _ITEM.match(line) and out:
            prev = out[-1]
            if prev.strip() and not _ITEM.match(prev):
                out.append("")
        out.append(line)
    return "\n".join(out)


def main():
    src = Path(sys.argv[1])
    out = Path(sys.argv[2])
    raw = src.read_text()
    # the markdown lib doesn't render GFM task lists; turn them into clean glyphs
    raw = raw.replace("- [x] ", "- ☑︎ ").replace("- [ ] ", "- ☐ ")
    raw = _space_lists(raw)
    body = markdown.markdown(raw, extensions=["tables", "fenced_code", "sane_lists", "attr_list"])
    html = f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"
    html_path = src.with_suffix(".html")          # next to the .md so ./images resolves
    html_path.write_text(html)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        page.wait_for_timeout(800)
        page.pdf(path=str(out), format="A4", print_background=True,
                 margin={"top": "16mm", "bottom": "16mm", "left": "14mm", "right": "14mm"})
        browser.close()
    print("wrote", out, "(", out.stat().st_size // 1024, "KB )")


if __name__ == "__main__":
    main()
