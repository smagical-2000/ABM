"""Tests for normalize.py — the dedup keystone.

If these break, dedup silently fails across the whole pipeline, so they
guard the highest-leverage code in the module.
"""

from auto_search.normalize import (
    normalize_company_name,
    parse_int_loose,
    slugify,
)


class TestNormalizeCompanyName:
    def test_strips_punctuation_and_case(self):
        assert normalize_company_name("OrthoIndy") == "orthoindy"
        assert normalize_company_name("Advanced Specialty Hospitals of Toledo") == \
            "advancedspecialtyhospitalsoftoledo"

    def test_legal_suffixes_collapse_to_same_key(self):
        # The whole point: these are the same company for dedup.
        assert normalize_company_name("Acme Health, LLC") == \
            normalize_company_name("Acme Health Inc.") == \
            normalize_company_name("Acme Health Corporation") == \
            "acmehealth"

    def test_empty_and_whitespace(self):
        assert normalize_company_name("") == ""
        assert normalize_company_name("   ") == ""

    def test_only_suffix_words(self):
        # Don't crash / don't return junk if a name is all suffixes.
        assert normalize_company_name("LLC Inc") == ""

    def test_unicode_and_symbols(self):
        assert normalize_company_name("Café & Co.") == "caf"  # non-ascii dropped


class TestSlugify:
    def test_word_boundaries_become_underscores(self):
        assert slugify("Advanced Specialty Hospitals") == "advanced_specialty_hospitals"

    def test_truncation(self):
        assert len(slugify("x" * 200, max_len=20)) == 20


class TestParseIntLoose:
    def test_plain_int_and_float(self):
        assert parse_int_loose(2400) == 2400
        assert parse_int_loose(2400.0) == 2400

    def test_messy_strings(self):
        assert parse_int_loose("2,400") == 2400
        assert parse_int_loose("~2400 employees") == 2400
        assert parse_int_loose("approx. 500") == 500

    def test_non_numeric_returns_none(self):
        assert parse_int_loose("no digits") is None
        assert parse_int_loose(None) is None
        assert parse_int_loose("") is None

    def test_bool_rejected(self):
        # bool is an int subclass — must not become 1/0.
        assert parse_int_loose(True) is None
        assert parse_int_loose(False) is None
