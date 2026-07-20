"""HTTP API for the Discovery Panel.

A thin FastAPI layer over ReviewService — it adds transport (routes, JSON,
CORS) and nothing else. All logic lives in the service; all storage behind the
repository protocol. Swapping JSON ↔ Postgres never touches this layer.
"""

from auto_search.api.app import app, create_app

__all__ = ["app", "create_app"]
