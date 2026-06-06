"""ABM target-list cross-verification.

Match companies surfaced by discovery against the sales team's uploaded ABM
target list (the Q2 accounts workbook). When a company we found showing live
buying signals is already a named target account, that's the highest-value
lead there is - and this package flags it.

Everything here is deterministic and zero-cost: no LLM, no network. Parsing the
workbook is a one-off on upload; matching is O(1) dict lookups against an
in-memory index.

    parse_workbook(bytes)  -> list[TargetAccount]   (parse.py)
    AbmIndex(targets)      -> .match(name, ...)      (matcher.py)
"""

from __future__ import annotations

from auto_search.abm.matcher import AbmIndex
from auto_search.abm.models import AbmMatch, TargetAccount
from auto_search.abm.parse import parse_workbook

__all__ = ["AbmIndex", "AbmMatch", "TargetAccount", "parse_workbook"]
