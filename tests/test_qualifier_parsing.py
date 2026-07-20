"""Tests for the shared LLM JSON extraction — the parser that turns Claude's
text into structured data. Brittleness here means valid results get thrown away.
"""

import pytest

from auto_search.llm import _first_balanced_object
from auto_search.llm import parse_json_object as _parse_json_strict


class TestParseJsonStrict:
    def test_plain_json(self):
        assert _parse_json_strict('{"qualified": true}') == {"qualified": True}

    def test_fenced_json(self):
        text = '```json\n{"qualified": false}\n```'
        assert _parse_json_strict(text) == {"qualified": False}

    def test_nested_objects_survive(self):
        # A regex like \{.*?\} would stop at the first '}' and corrupt this.
        text = '```json\n{"a": {"b": {"c": 1}}}\n```'
        assert _parse_json_strict(text) == {"a": {"b": {"c": 1}}}

    def test_json_embedded_in_prose(self):
        text = 'Sure! {"x": 1, "y": {"z": 2}}. Hope that helps.'
        assert _parse_json_strict(text) == {"x": 1, "y": {"z": 2}}

    def test_braces_inside_string_values_dont_break_depth(self):
        text = '{"reasoning": "uses } and { chars", "ok": true}'
        assert _parse_json_strict(text) == {
            "reasoning": "uses } and { chars", "ok": True}

    def test_escaped_quotes_in_string(self):
        text = r'{"reasoning": "she said \"hi\"", "ok": true}'
        assert _parse_json_strict(text)["ok"] is True

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            _parse_json_strict("")

    def test_no_object_raises(self):
        with pytest.raises(ValueError):
            _parse_json_strict("no json here at all")


class TestFirstBalancedObject:
    def test_returns_none_when_no_brace(self):
        assert _first_balanced_object("nothing") is None

    def test_stops_at_balanced_close(self):
        assert _first_balanced_object('{"a": 1} trailing') == '{"a": 1}'
