"""Claude account classifier — the confidence gate. Mocked (no network): llm.call_plain
+ llm.extract_text are patched so we test only the parse/gate, never the model."""

import pytest

from auto_search.engagement import classify


def _text(js):
    return lambda _r: js


@pytest.mark.asyncio
async def test_high_confidence_bucket_passes_through(monkeypatch):
    async def fake_call(**_k):
        return object()
    monkeypatch.setattr(classify.llm, "call_plain", fake_call)
    monkeypatch.setattr(classify.llm, "extract_text",
                        _text('{"framework":"specialty","confidence":"high","reason":"PT clinic"}'))
    r = await classify.classify_account("CORA Physical Therapy", "cora.com")
    assert r == {"framework": "specialty", "confidence": "high", "reason": "PT clinic"}


@pytest.mark.asyncio
async def test_unrecognized_framework_degrades_to_low(monkeypatch):
    async def fake_call(**_k):
        return object()
    monkeypatch.setattr(classify.llm, "call_plain", fake_call)
    monkeypatch.setattr(classify.llm, "extract_text",
                        _text('{"framework":"hospital","confidence":"high"}'))  # not a bucket
    r = await classify.classify_account("Somewhere", None)
    assert r["framework"] == "non_icp" and r["confidence"] == "low"


@pytest.mark.asyncio
async def test_error_never_yields_a_confident_label(monkeypatch):
    async def boom(**_k):
        raise RuntimeError("claude down")
    monkeypatch.setattr(classify.llm, "call_plain", boom)
    r = await classify.classify_account("Somewhere", "x.com")
    assert r["confidence"] == "low"          # gate drops it → stays Unclassified


@pytest.mark.asyncio
async def test_empty_name_is_low():
    r = await classify.classify_account("", None)
    assert r["confidence"] == "low"
