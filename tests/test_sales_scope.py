"""Tests for grounding, gap detection and the scope sheet.

The two things that must hold: a scope line never appears without client words
behind it, and a filing the client already holds is surfaced rather than quoted
for a second time.
"""

from __future__ import annotations

from rri.language import check
from rri.products import ProductRecord
from rri.sales import gaps as gapmod
from rri.sales.extract import RuleExtractor
from rri.sales.ground import Corpus, build_lines, ground_line, resolve_facts
from rri.sales.scope import build_scope, render


def record(inn, country, company, status="Active"):
    return ProductRecord(
        source_id="test", country=country, product_name=f"{inn.title()} tablet",
        inn=(inn,), strength=("10mg",), form="tablet", route=(), atc=None,
        company_raw=company, company=company.lower(), registration_number="R1",
        approval_date="2022-01-01", status=status, category="Drugs",
        source_ref="snap#1", extras={},
    )


CORPUS = Corpus(
    records=[
        record("amlodipine", "NG", "Acme Pharma Nigeria Limited"),
        record("amlodipine", "NG", "Other Pharma Limited"),
        record("amlodipine", "NG", "Third Pharma Limited"),
        record("atorvastatin", "NG", "Other Pharma Limited"),
        record("amlodipine", "TZ", "Other Pharma Limited"),
    ],
    coverage={"NG": "NAFDAC Greenbook", "TZ": "TMDA"},
    retrieved={"NG": "2026-09-01", "TZ": "2026-08-30"},
)


class TestCorpus:
    def test_markets_come_from_the_records(self):
        assert CORPUS.markets == {"NG", "TZ"}

    def test_ingredient_vocabulary_is_built_from_the_registers(self):
        assert {"amlodipine", "atorvastatin"} <= CORPUS.ingredients

    def test_no_timeline_is_claimed_without_a_submission_date(self):
        # Approval dates give when a registration was granted, not how long
        # review took. Returning a duration here would be a fabricated number.
        assert CORPUS.observed_timeline("NG") is None


class TestResolution:
    def _resolve(self, text, known=None):
        from rri.sales.extract import extract
        facts, _ = extract(text, RuleExtractor(known or CORPUS.ingredients))
        return resolve_facts(facts, CORPUS)

    def test_a_covered_market_resolves(self):
        r = self._resolve("We want Nigeria.")
        market = next(x for x in r if x.fact.kind == "market")
        assert market.is_resolved and market.entity == "NG"

    def test_an_uncovered_market_does_not_resolve(self):
        r = self._resolve("We want Brazil.")
        market = next(x for x in r if x.fact.kind == "market")
        assert not market.is_resolved
        assert "outside the markets" in market.note

    def test_a_region_does_not_resolve_to_a_market(self):
        r = self._resolve("We want Latin America.")
        market = next(x for x in r if x.fact.kind == "market")
        assert not market.is_resolved
        assert "region" in market.note

    def test_an_unknown_molecule_does_not_resolve(self):
        from rri.sales.extract import extract
        from rri.sales.schema import Fact, Span
        text = "We need rivaroxaban registered."
        facts = [Fact(kind="product", value="rivaroxaban",
                      span=Span("rivaroxaban", 8, 19), confidence=0.9, method="test")]
        r = resolve_facts(facts, CORPUS)
        assert not r[0].is_resolved


class TestGrounding:
    def test_competitors_are_counted_from_the_register(self):
        g = ground_line("amlodipine", "NG", CORPUS)
        assert g.competitors == 3

    def test_the_client_is_excluded_from_its_own_competitor_count(self):
        g = ground_line("amlodipine", "NG", CORPUS, client_company="Acme Pharma")
        assert g.already_held is True
        assert g.competitors == 2

    def test_a_product_the_client_does_not_hold(self):
        g = ground_line("atorvastatin", "NG", CORPUS, client_company="Acme Pharma")
        assert g.already_held is False

    def test_nothing_found_is_stated_as_a_search_result(self):
        # Absence is reported as what a search returned, naming the source and
        # the date, never as a statement about the product's status.
        g = ground_line("ibuprofen", "NG", CORPUS)
        assert g.competitors == 0
        assert "found in" in g.evidence
        assert "NAFDAC Greenbook" in g.evidence
        assert "2026-09-01" in g.evidence
        assert not check(g.evidence)

    def test_holding_is_unknown_without_a_client_entity(self):
        # Without the entity name the question cannot be answered, and guessing
        # would be worse than saying so.
        assert ground_line("amlodipine", "NG", CORPUS).already_held is None


class TestScopeLines:
    def test_every_line_carries_client_spans(self):
        scope = build_scope(
            "We need amlodipine registration in Nigeria.", CORPUS,
            extractor=RuleExtractor(CORPUS.ingredients))
        assert scope.lines
        for line in scope.lines:
            assert line.is_supported

    def test_no_line_is_produced_for_a_market_never_mentioned(self):
        scope = build_scope(
            "We need amlodipine registration in Nigeria.", CORPUS,
            extractor=RuleExtractor(CORPUS.ingredients))
        assert {line.market for line in scope.lines} == {"NG"}

    def test_no_lines_without_a_service(self):
        # Products and markets alone do not describe a unit of work.
        scope = build_scope("We sell amlodipine in Nigeria.", CORPUS,
                            extractor=RuleExtractor(CORPUS.ingredients))
        assert scope.lines == []


class TestGapDetection:
    def test_a_missing_product_type_is_a_high_impact_gap(self):
        scope = build_scope("We need registration in Nigeria.", CORPUS,
                            extractor=RuleExtractor(CORPUS.ingredients))
        assert any(g.kind == "product_type" and g.impact == "high"
                   for g in scope.gaps)

    def test_a_region_without_countries_is_flagged(self):
        scope = build_scope("We want to cover Latin America.", CORPUS,
                            extractor=RuleExtractor(CORPUS.ingredients))
        assert any("countries within" in g.question for g in scope.gaps)

    def test_missing_client_entity_blocks_quoting(self):
        scope = build_scope(
            "We need amlodipine registration in Nigeria for our generics.",
            CORPUS, extractor=RuleExtractor(CORPUS.ingredients))
        assert any(g.kind == "client" for g in scope.gaps)
        assert not scope.can_be_quoted

    def test_gaps_are_ranked_worst_first(self):
        scope = build_scope("We need registration in Nigeria.", CORPUS,
                            extractor=RuleExtractor(CORPUS.ingredients))
        ranks = [g.rank for g in scope.gaps]
        assert ranks == sorted(ranks)

    def test_an_answered_question_is_not_asked_again(self):
        scope = build_scope(
            "We need our generic amlodipine registered in Nigeria, 14 products, "
            "by Q3 2027.", CORPUS,
            client_company="Acme Pharma",
            extractor=RuleExtractor(CORPUS.ingredients))
        assert not any(g.kind == "product_type" for g in scope.gaps)
        assert not any(g.kind == "volume" for g in scope.gaps)


class TestAlreadyHeld:
    def test_a_filing_the_client_already_has_is_surfaced(self):
        scope = build_scope(
            "We want amlodipine registration in Nigeria.", CORPUS,
            client_company="Acme Pharma",
            extractor=RuleExtractor(CORPUS.ingredients))
        held = gapmod.already_held(scope.lines)
        assert len(held) == 1
        assert held[0].product == "amlodipine"

    def test_nothing_is_surfaced_when_the_client_holds_nothing(self):
        scope = build_scope(
            "We want atorvastatin registration in Nigeria.", CORPUS,
            client_company="Acme Pharma",
            extractor=RuleExtractor(CORPUS.ingredients))
        assert gapmod.already_held(scope.lines) == []


class TestScopeSheet:
    def test_the_sheet_passes_the_output_linter(self):
        scope = build_scope(
            "We want amlodipine and atorvastatin registration in Nigeria, "
            "generics, 14 products, by Q3 2027.", CORPUS,
            client_company="Acme Pharma",
            extractor=RuleExtractor(CORPUS.ingredients))
        sheet = render(scope, "Acme Pharma")
        assert not check(sheet)

    def test_the_sheet_shows_the_clients_own_words(self):
        scope = build_scope("We want amlodipine registration in Nigeria.", CORPUS,
                            extractor=RuleExtractor(CORPUS.ingredients))
        sheet = render(scope)
        assert "amlodipine" in sheet
        assert "What the client said" in sheet

    def test_an_unquotable_scope_says_so(self):
        scope = build_scope("We want to expand into Africa.", CORPUS,
                            extractor=RuleExtractor(CORPUS.ingredients))
        assert "Not ready to quote" in render(scope)

    def test_an_empty_conversation_produces_a_sheet_not_a_crash(self):
        scope = build_scope("Thanks, speak next week.", CORPUS,
                            extractor=RuleExtractor(CORPUS.ingredients))
        sheet = render(scope)
        assert "None." in sheet
