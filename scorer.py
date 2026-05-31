"""
ABM Account Scorer — CLI tool that runs Galyna's exact scoring frameworks.

Usage:
    python scorer.py "OrthoIndy" --segment specialties
    python scorer.py "Beacon Health System" --segment hs
    python scorer.py "Centene" --segment payer

Output:
    - Prints the scored report to terminal
    - Saves a markdown file to outputs/<segment>_<company>_<timestamp>.md
"""
import argparse
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# Load .env from project root (no-op if already set in environment)
load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL = "claude-opus-4-7"
PROMPTS_DIR = Path(__file__).parent / "prompts"
OUTPUTS_DIR = Path(__file__).parent / "outputs"

SEGMENT_TO_PROMPT = {
    "specialties": "specialties.txt",   # Ortho / Behavioral Health (30-pt)
    "payer":       "payers.txt",        # Payers (30-pt)
    "hs":          "health_systems.txt" # Health Systems (27-pt)
}

# System prompt is frozen — keep this stable for cache hits across runs
SYSTEM_PROMPT = """You are an ABM analyst at Magical, a healthcare AI / revenue cycle automation company.

You score target accounts using publicly available information found via web search. You always:
- Use the EXACT scoring framework provided in the user message — do not invent your own dimensions
- Label inferred information as *Likely* or *Unknown* explicitly
- Cite sources (URLs or publication names) for every fact you state
- Produce output in clean markdown with clear section headers
- Be honest when you can't find information — say "Unknown" rather than guess

Your output will be reviewed by the GTM team and used for outbound campaign planning."""


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("abm-scorer")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_prompt(segment: str, company: str) -> str:
    """Load the segment-specific prompt and inject the company name."""
    if segment not in SEGMENT_TO_PROMPT:
        raise ValueError(
            f"Unknown segment '{segment}'. Choose from: {list(SEGMENT_TO_PROMPT)}"
        )

    prompt_path = PROMPTS_DIR / SEGMENT_TO_PROMPT[segment]
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    template = prompt_path.read_text()
    return template.replace("{{COMPANY_NAME}}", company)


def safe_filename(company: str) -> str:
    """Strip characters that don't belong in filenames."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", company).strip("_")


def normalize_table_cells(text: str) -> str:
    """Collapse multi-line markdown table cells onto single lines.

    Claude's web_search tool sometimes injects cited snippets with embedded
    newlines, which breaks markdown table rendering (Notion treats each
    fragment as a separate row). This joins continuation lines back onto
    the row above so every table cell stays single-line.

    A row is "complete" only when the accumulated text ends with `|`. While
    the row hasn't closed yet, every following non-blank line — including a
    line that is just `|` — is treated as a continuation of the current row.
    Once the row is closed, the next line starting with `|` is a new row.
    """
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("|"):
            row = line.rstrip()
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                nxt_stripped = nxt.strip()
                if not nxt_stripped:
                    break  # blank line ends the row
                # If the row is already closed AND the next line starts a new
                # row, stop accumulating.
                if row.rstrip().endswith("|") and nxt.lstrip().startswith("|"):
                    break
                # Otherwise the next line is a continuation of the current row
                # (either inline text, or just the trailing `|` on its own line)
                row += " " + nxt_stripped
                j += 1
            # Collapse any double-spaces introduced by the join
            row = re.sub(r"\s{2,}", " ", row)
            # Tidy up " ;" / " ," / " ." that come from joined citation snippets
            row = re.sub(r"\s+([;,.])", r"\1", row)
            result.append(row)
            i = j
        else:
            result.append(line)
            i += 1
    return "\n".join(result)


def save_output(segment: str, company: str, content: str) -> Path:
    """Write the scored report to outputs/ and return the path."""
    OUTPUTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{segment}_{safe_filename(company)}_{timestamp}.md"
    path = OUTPUTS_DIR / filename
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_account(company: str, segment: str, dry_run: bool = False) -> str:
    """
    Run the scoring framework against a single company.

    Uses:
    - Claude Opus 4.7 (most capable model)
    - Adaptive thinking (model decides depth)
    - Effort: xhigh (best for intelligence-sensitive work)
    - Server-side web search (the prompts require public info lookup)
    - Streaming (large max_tokens needs it)
    """
    user_prompt = load_prompt(segment, company)

    if dry_run:
        log.info("DRY RUN — not calling API. Prompt that would be sent:")
        print("\n---SYSTEM---\n" + SYSTEM_PROMPT)
        print("\n---USER---\n" + user_prompt)
        return ""

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    log.info(f"Scoring '{company}' as segment='{segment}' using {MODEL}")
    log.info("Calling Claude API with web search enabled (this may take 1-3 min)…")

    # Stream because max_tokens is high and web search adds latency.
    # SDK auto-retries 429/5xx with exponential backoff.
    final_text_parts: list[str] = []

    with client.messages.stream(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "xhigh"},
        system=SYSTEM_PROMPT,
        tools=[
            {"type": "web_search_20260209", "name": "web_search"},
        ],
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            final_text_parts.append(text)

        final_message = stream.get_final_message()

    print()  # newline after streaming
    log.info(
        f"Done. stop_reason={final_message.stop_reason} | "
        f"input_tokens={final_message.usage.input_tokens} | "
        f"output_tokens={final_message.usage.output_tokens}"
    )

    if final_message.stop_reason == "refusal":
        log.warning(
            f"Claude refused. Category: {final_message.stop_details.category}, "
            f"Explanation: {final_message.stop_details.explanation}"
        )

    # Reconstruct the full text from the response (more reliable than stream parts)
    full_text = "\n".join(
        block.text for block in final_message.content if block.type == "text"
    )

    # Post-process: collapse multi-line table cells caused by web_search citation newlines.
    # Without this, Notion renders broken/empty rows for every cited cell.
    full_text = normalize_table_cells(full_text)
    return full_text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score a target account using Magical's ABM framework.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scorer.py "OrthoIndy" --segment specialties
  python scorer.py "Beacon Health System" --segment hs
  python scorer.py "Centene" --segment payer
  python scorer.py "OrthoIndy" --segment specialties --dry-run
        """,
    )
    parser.add_argument("company", help="Company name to score (use quotes if multi-word)")
    parser.add_argument(
        "--segment",
        choices=list(SEGMENT_TO_PROMPT),
        required=True,
        help="Scoring framework to apply",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the prompt that would be sent without calling the API",
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY") and not args.dry_run:
        log.error("ANTHROPIC_API_KEY not set. Export it or put it in .env")
        return 1

    try:
        content = score_account(args.company, args.segment, dry_run=args.dry_run)
    except anthropic.APIError as e:
        log.error(f"Claude API error: {e}")
        return 1
    except (FileNotFoundError, ValueError) as e:
        log.error(str(e))
        return 1

    if args.dry_run:
        return 0

    output_path = save_output(args.segment, args.company, content)
    log.info(f"Report saved to: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
