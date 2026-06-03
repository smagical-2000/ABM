"""Account scoring — the phase after discovery.

A promoted (or CSV-imported) account is scored on a segment-specific rubric by
Claude, independently QA'd by a second Claude pass, and persisted for the
Scored dashboard. One concern per module:

    frameworks.py  rubric definitions + tier resolution (pure config/logic)
    models.py      typed score/QA/account contracts (match the UI shape)
    engine.py      the scorer (Claude, framework-aware, known-facts injection)
    qa.py          the independent QA pass (Claude, no scorer reasoning)
    imports.py     CSV (Definitive Healthcare) -> Account + known facts
    service.py     orchestration the API/runner call (score + QA + persist)

All models are Sonnet; the qualifier-grade web_search lifts the heavy research.
"""
