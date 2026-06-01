"""External API clients — thin transport wrappers, no business logic.

  signalbase.py  — SignalBase real-time job-change feed (via an Apify actor)

Connectors depend on these clients; the clients only know how to talk to an
API and return typed rows. Keeping transport separate from the connector's
domain mapping keeps both testable in isolation.
"""
