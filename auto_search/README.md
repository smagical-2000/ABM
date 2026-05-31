# Auto Search Module

System A — **pre-account discovery**. Ingests intent signals from external
sources, applies a two-stage ICP gate, and surfaces qualified candidate
companies for Galyna's review.

> Auto Search **never** auto-creates accounts. Galyna promotes from a
> review queue. This is the only bridge to the main platform.

---

## Layout

```
auto_search/
├── __init__.py
├── models.py                   # Pydantic: RawSignal, QualificationResult
├── qualifier.py                # ICP gate: rules → LLM → calibrated verdict
└── connectors/
    ├── base.py                 # SignalConnector protocol
    └── layoffs_fyi.py          # First connector
```

Adding a new source = **one new file in `connectors/`** that satisfies the
`SignalConnector` protocol. No other file changes.

---

## Pipeline

```
   ┌──────────────┐    ┌──────────────────┐    ┌──────────────────┐
   │ Connector    │───►│ Stage 1: Rules   │───►│ Stage 2: LLM     │
   │ (CSV / API)  │    │ (free, instant)  │    │ (~$0.01/call)    │
   └──────────────┘    └──────────────────┘    └──────────────────┘
        │                       │                       │
        ▼                       ▼                       ▼
  RawSignal yielded      ~80% noise killed      QualificationResult
                         here for $0            with confidence + reasoning
```

### Stage 1 — Rules

Hard, deterministic disqualifiers:

| Rule | Reason |
|---|---|
| Industry must contain healthcare keyword | Pure tech / non-healthcare filtered |
| Industry must NOT contain `biotech`, `pharma`, `dental`, `veterinary` | Out-of-ICP segments |
| Country empty or US | Geo filter |
| Laid off ≥ 10 (when known) | Scale floor |

### Stage 2 — LLM (Claude)

Runs only for survivors of Stage 1. The ICP prompt is in `qualifier.py`
under `ICP_SYSTEM_PROMPT`. Returns structured JSON:

```jsonc
{
  "qualified": true,
  "segment": "health_system",
  "sub_segment": "community_hospital",
  "confidence": 0.86,
  "reasoning": "Mid-size community hospital, 1200 employees, US-based...",
  "needs_human_review": false
}
```

**Calibration rule:** if `confidence < 0.7`, the result is forced into
`needs_human_review`. LLM confidence is not blindly trusted.

---

## Dedup Strategy

Each `RawSignal` has a `source_external_id` that is **deterministic**
across runs — same input row → same ID. Downstream this maps to a Postgres
`UNIQUE (source, source_external_id)` constraint, so re-running the
connector over the same window is a no-op.

For Layoffs.fyi (no native IDs), we compose the ID:

```
source_external_id = f"{slug(company)}::{observed_date}"
```

---

## Adding a New Connector

1. Create `auto_search/connectors/<source>.py`
2. Implement the `SignalConnector` protocol from `connectors/base.py`
3. Yield `RawSignal` objects
4. Apply cheap pre-filters at extract-time to reduce downstream LLM cost
5. Add a `test_<source>_pipeline.py` to `scripts/` that mirrors the
   layoffs test pattern

---

## Testing

```bash
# 1. Drop CSV in data/layoffs.csv (downloaded from layoffs.fyi)
# 2. Add LAYOFFS_CSV_PATH and ANTHROPIC_API_KEY to .env
# 3. Run:
python scripts/test_layoffs_pipeline.py --rules-only     # free
python scripts/test_layoffs_pipeline.py --limit 20 -v    # ~$0.20 in LLM
```

Output prints rule-killed rows separately from LLM-evaluated rows, and
dumps qualified candidates to `data/test_qualified_layoffs.json` for
sharing with Galyna.
