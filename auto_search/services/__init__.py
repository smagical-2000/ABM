"""Application services — the API the UI / FastAPI layer calls.

The UI must depend on this layer, NOT on the repository dicts or the CLI
scripts. Services return typed DTOs and own the review workflow (promote /
reject / defer), so storage can move from JSON to Postgres underneath without
the UI changing.
"""

from auto_search.services.review import (
    DiscoveryStats,
    PanelCompany,
    PanelSignal,
    ReviewService,
)

__all__ = ["ReviewService", "PanelCompany", "PanelSignal", "DiscoveryStats"]
