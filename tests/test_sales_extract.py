"""Tests for conversation extraction.

The load bearing property is not recall. It is that nothing reaches a scope
sheet unless the client's own words support it. A model that invents a product
must be stopped by the span check, not by trusting the model.
"""

from __future__ import annotations

import pytest

from rri.sales.extract import RuleExtractor, extract, verify_span
from rri.sales.schema import Fact, Span

NOTE = """Call with Meridian Pharma.

They have 14 generic products registered in Nigeria and want to expand into
Brazil. They named amlodipine and atorvastatin. Asked about dossier preparation
in CTD format. Brazil filings needed by Q3 2027 for a tender."""


class FakeExtractor:
    """Returns whatever it is told to, so verification can be tested directly."""

    name = "fake"

    def __init__(self, proposals):
        self._proposals = proposals

    def propose(self, text):
        return self._proposals


class TestSpanVerification:
    def test_exact_quote_is_located(self):
        span = verify_span(NOTE, "amlodipine")
        assert span is not None
        assert NOTE[span.start:span.end] == "amlodipine"

    def test_quote_spanning_a_line_break_is_located(self):
        # "expand into\nBrazil" in the note. A quote copied without the newline
        # is still honest and must not be rejected.
        span = verify_span(NOTE, "expand into Brazil")
        assert span is not None

    def test_absent_quote_returns_none(self):
        assert verify_span(NOTE, "rivaroxaban") is None

    def test_empty_quote_returns_none(self):
        assert verify_span(NOTE, "") is None
        assert verify_span(NOTE, "   ") is None

    def test_verification_never_reorders_characters(self):
        # A quote with the right words in the wrong order is not in the text.
        assert verify_span(NOTE, "Brazil into expand") is None


class TestFabricationIsBlocked:
    """The guarantee that makes a language model safe to use here."""

    def test_a_fact_whose_quote_is_absent_is_dropped(self):
        facts, rejected = extract(NOTE, FakeExtractor([
            {"kind": "product", "value": "rivaroxaban",
             "quote": "they mentioned rivaroxaban", "confidence": 0.99},
        ]))
        assert facts == []
        assert len(rejected) == 1
        assert "not found" in rejected[0]["reason"]

    def test_a_fact_with_no_quote_at_all_is_dropped(self):
        facts, rejected = extract(NOTE, FakeExtractor([
            {"kind": "market", "value": "IN", "confidence": 0.99},
        ]))
        assert facts == []
        assert rejected[0]["reason"] == "no supporting quote"

    def test_high_confidence_does_not_rescue_an_unsupported_fact(self):
        facts, _ = extract(NOTE, FakeExtractor([
            {"kind": "product", "value": "invented", "quote": "not in the note",
             "confidence": 1.0},
        ]))
        assert facts == []

    def test_rejections_are_returned_not_silently_discarded(self):
        # The rate at which an extractor proposes unsupported facts is a quality
        # signal. Hiding it would hide a degrading model.
        _, rejected = extract(NOTE, FakeExtractor([
            {"kind": "product", "value": "a", "quote": "absent one", "confidence": 0.9},
            {"kind": "product", "value": "b", "quote": "absent two", "confidence": 0.9},
        ]))
        assert len(rejected) == 2

    def test_a_supported_fact_survives(self):
        facts, rejected = extract(NOTE, FakeExtractor([
            {"kind": "product", "value": "amlodipine", "quote": "amlodipine",
             "confidence": 0.9},
        ]))
        assert len(facts) == 1 and rejected == []
        assert facts[0].value == "amlodipine"


class TestRuleExtractor:
    def test_markets_are_recognised(self):
        facts, _ = extract(NOTE, RuleExtractor())
        markets = {f.value for f in facts if f.kind == "market"}
        assert {"NG", "BR"} <= markets

    def test_ingredients_come_from_the_register_vocabulary(self):
        facts, _ = extract(NOTE, RuleExtractor({"amlodipine", "atorvastatin"}))
        products = {f.value for f in facts if f.kind == "product"}
        assert products == {"amlodipine", "atorvastatin"}

    def test_an_unknown_molecule_is_not_invented(self):
        facts, _ = extract(NOTE, RuleExtractor(set()))
        assert not any(f.kind == "product" for f in facts)

    def test_volume_survives_an_adjective(self):
        facts, _ = extract("They have 14 generic products.", RuleExtractor())
        assert [f.value for f in facts if f.kind == "volume"] == ["14"]

    def test_a_vague_quantity_produces_no_number(self):
        facts, _ = extract("They have a few products.", RuleExtractor())
        assert not any(f.kind == "volume" for f in facts)

    def test_regions_are_kept_separate_from_countries(self):
        facts, _ = extract("We want to cover Latin America.", RuleExtractor())
        values = {f.value for f in facts if f.kind == "market"}
        assert "region:latam" in values

    def test_every_fact_points_at_real_text(self):
        facts, _ = extract(NOTE, RuleExtractor({"amlodipine", "atorvastatin"}))
        assert facts
        for f in facts:
            assert NOTE[f.span.start:f.span.end] == f.span.text


class TestFactValidation:
    def test_unknown_kind_is_refused(self):
        with pytest.raises(ValueError, match="unknown fact kind"):
            Fact(kind="vibes", value="x", span=Span("x", 0, 1),
                 confidence=0.5, method="test")

    def test_confidence_outside_range_is_refused(self):
        for bad in (0.0, -1.0, 1.5):
            with pytest.raises(ValueError, match="confidence"):
                Fact(kind="product", value="x", span=Span("x", 0, 1),
                     confidence=bad, method="test")


class TestEmptyInput:
    def test_empty_text_yields_nothing(self):
        facts, rejected = extract("", RuleExtractor())
        assert facts == [] and rejected == []

    def test_text_with_no_regulatory_content_yields_nothing(self):
        facts, _ = extract("Thanks for your time, speak next week.", RuleExtractor())
        assert facts == []
