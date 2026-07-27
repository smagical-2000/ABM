"""A library import must never replace the operator's environment.

2026-07-27: three catch-up discovery runs silently wrote to a LOCAL Postgres
instead of production. Cause: auto_search/qualifier.py called
`load_dotenv(override=True)` at module level, so importing it re-read .env
(DATABASE_URL=postgresql://localhost/abm_discovery) over the DATABASE_URL the
operator had exported. The runs looked healthy and reported success — the
worst shape of failure this codebase has.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _module_level_override_calls(path: Path) -> list[int]:
    """Line numbers of MODULE-LEVEL load_dotenv(override=True) calls."""
    tree = ast.parse(path.read_text())
    out = []
    for node in tree.body:                     # module level only
        call = node.value if isinstance(node, ast.Expr) else None
        if not isinstance(call, ast.Call):
            continue
        name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
        if name != "load_dotenv":
            continue
        if any(k.arg == "override" and getattr(k.value, "value", False) is True
               for k in call.keywords):
            out.append(node.lineno)
    return out


def test_no_library_module_overrides_the_process_env():
    offenders = []
    for py in (REPO / "auto_search").rglob("*.py"):
        for line in _module_level_override_calls(py):
            offenders.append(f"{py.relative_to(REPO)}:{line}")
    assert not offenders, (
        "load_dotenv(override=True) at module level replaces the operator's "
        f"environment on import: {offenders}")


def test_exported_database_url_survives_importing_the_qualifier():
    """End-to-end: the exact failure. An exported DATABASE_URL must still be
    the one in os.environ after importing the qualifier."""
    code = (
        "import os; os.environ['DATABASE_URL'] = 'postgresql://sentinel/db';"
        "import auto_search.qualifier;"
        "print(os.environ['DATABASE_URL'])"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=REPO,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-500:]
    assert "postgresql://sentinel/db" in r.stdout, (
        f"the qualifier import replaced DATABASE_URL: {r.stdout.strip()}")
