"""Tests for whitespace computation.

The load-bearing property is not that gaps are found. It is that a gap is
described as the result of a search and never as a claim about a company's
regulatory standing.
"""

from __future__ import annotations

import pytest

from rri.language import check
from rri.products import ProductRecord
from rri.report import render_markdown
from rri.whitespace import SourceCoverage, build_portfolio


def record(inn, strength, form, country, company, status="Active",
           category="Drugs", reg="R1", approval="2020-01-01"):
    return ProductRecord(
        source_id=f"src_{country}", country=country,
        product_name=f"{inn[0].title()} brand" if inn else "unnamed product",
        inn=inn, strength=strength, form=form, route=(), atc=None,
        company_raw=company, company=company.lower(),
        registration_number=reg, approval_date=approval,
        status=status, category=category, source_ref=f"snap#{country}",
    )


NG = SourceCoverage("nafdac", "NG", "NAFDAC Greenbook", 9008, "2026-08-30",
                    "Full published register", coverage_end_year=2026)
TZ = SourceCoverage("tmda", "TZ", "TMDA", 1027, "2026-08-30",
                    "Published SmPC subset, not the full register",
                    coverage_end_year=2023)
COVERAGE = [NG, TZ]


class TestCoverageLanguage:
    def test_not_found_phrase_names_source_and_date(self):
        phrase = TZ.not_found_phrase()
        assert "TMDA" in phrase and "2026-08-30" in phrase

    def test_not_found_phrase_is_not_a_regulatory_claim(self):
        # The phrase itself must survive the output linter.
        assert not check(TZ.not_found_phrase())
        assert not check(NG.not_found_phrase())


class TestWhitespaceComputation:
    def test_product_in_one_market_only_is_whitespace_in_the_other(self):
        records = [record(("amoxicillin",), ("500mg",), "capsule", "NG", "Cipla")]
        portfolio = build_portfolio(records, "Cipla", COVERAGE)

        assert len(portfolio.products) == 1
        assert len(portfolio.whitespace) == 1
        cell = portfolio.whitespace[0]
        assert cell.country == "TZ"
        assert cell.present_in == ("NG",)

    def test_product_in_both_markets_is_not_whitespace(self):
        records = [
            record(("amoxicillin",), ("500mg",), "capsule", "NG", "Cipla"),
            record(("amoxicillin",), ("500mg",), "capsule", "TZ", "Cipla"),
        ]
        portfolio = build_portfolio(records, "Cipla", COVERAGE)
        assert portfolio.whitespace == []

    def test_whitespace_cell_carries_proof_of_holding(self):
        # A gap is only interesting if the company demonstrably holds the
        # product somewhere, so the registration proving it must travel with it.
        records = [record(("amoxicillin",), ("500mg",), "capsule", "NG", "Cipla",
                          reg="A4-100160")]
        portfolio = build_portfolio(records, "Cipla", COVERAGE)
        assert portfolio.whitespace[0].source_registration == "A4-100160"

    def test_different_strengths_are_separate_opportunities(self):
        records = [
            record(("amoxicillin",), ("500mg",), "capsule", "NG", "Cipla"),
            record(("amoxicillin",), ("250mg",), "capsule", "NG", "Cipla"),
        ]
        portfolio = build_portfolio(records, "Cipla", COVERAGE)
        assert len(portfolio.products) == 2
        assert len(portfolio.whitespace) == 2

    def test_ranking_favours_products_held_in_more_markets(self):
        records = [
            record(("amoxicillin",), ("500mg",), "capsule", "NG", "Cipla"),
            record(("amoxicillin",), ("500mg",), "capsule", "TZ", "Cipla"),
            record(("atenolol",), ("100mg",), "tablet", "NG", "Cipla"),
        ]
        # Add a third source so a product in two markets can still have a gap.
        zm = SourceCoverage("zamra", "ZM", "ZAMRA", 100, "2026-08-30", "")
        portfolio = build_portfolio(records, "Cipla", [NG, TZ, zm])
        assert portfolio.whitespace[0].product.inn == ("amoxicillin",)


class TestStatusAndCategoryFiltering:
    def test_inactive_registration_is_not_coverage(self):
        # A lapsed registration is not market access; counting it would hide a
        # real opportunity.
        records = [
            record(("amoxicillin",), ("500mg",), "capsule", "NG", "Cipla"),
            record(("amoxicillin",), ("500mg",), "capsule", "TZ", "Cipla",
                   status="Inactive"),
        ]
        portfolio = build_portfolio(records, "Cipla", COVERAGE)
        assert len(portfolio.whitespace) == 1
        assert portfolio.whitespace[0].country == "TZ"

    def test_inactive_can_be_included_explicitly(self):
        records = [
            record(("amoxicillin",), ("500mg",), "capsule", "NG", "Cipla"),
            record(("amoxicillin",), ("500mg",), "capsule", "TZ", "Cipla",
                   status="Inactive"),
        ]
        portfolio = build_portfolio(records, "Cipla", COVERAGE, active_only=False)
        assert portfolio.whitespace == []

    def test_medical_devices_are_excluded(self):
        # Devices follow a different registration pathway entirely, so they
        # cannot be compared against medicines requirements.
        records = [record(("nappy diaper pants",), (), None, "NG", "Cipla",
                          category="Medical devices")]
        portfolio = build_portfolio(records, "Cipla", COVERAGE)
        assert portfolio.products == []


class TestUnmatchableRecordsAreVisible:
    def test_thin_record_is_reported_not_silently_dropped(self):
        records = [record((), (), None, "NG", "Cipla")]
        portfolio = build_portfolio(records, "Cipla", COVERAGE)
        assert portfolio.products == []
        assert len(portfolio.unmatchable) == 1


class TestReportLanguage:
    def test_generated_report_passes_the_output_linter(self):
        records = [
            record(("amoxicillin",), ("500mg",), "capsule", "NG", "Cipla"),
            record(("atenolol",), ("100mg",), "tablet", "NG", "Cipla"),
            record(("amoxicillin",), ("500mg",), "capsule", "TZ", "Cipla"),
        ]
        portfolio = build_portfolio(records, "Cipla", COVERAGE)
        text = render_markdown(portfolio)  # raises if it overclaims
        assert not check(text)

    def test_report_states_coverage_limits_prominently(self):
        records = [record(("amoxicillin",), ("500mg",), "capsule", "NG", "Cipla")]
        portfolio = build_portfolio(records, "Cipla", COVERAGE)
        text = render_markdown(portfolio)
        assert "not the full register" in text
        assert "What was searched" in text

    def test_report_refuses_to_render_an_overclaim(self):
        records = [record(("amoxicillin",), ("500mg",), "capsule", "NG", "Cipla")]
        bad_source = SourceCoverage(
            "bad", "TZ", "TMDA", 10, "2026-08-30",
            limitation="product is not registered in Tanzania",  # planted
        )
        portfolio = build_portfolio(records, "Cipla", [NG, bad_source])
        with pytest.raises(ValueError, match="unsupportable claim"):
            render_markdown(portfolio)


class TestCoverageWindow:
    """A source that stops in 2023 cannot evidence a 2025 registration."""

    def test_holding_after_the_target_cutoff_is_not_whitespace(self):
        # Registered in Nigeria in 2025; TMDA's published subset ends 2023.
        # Its absence there is a fact about the cutoff, not about the company.
        records = [record(("rivaroxaban",), ("10mg",), "tablet", "NG", "Bayer",
                          approval="2025-06-01")]
        portfolio = build_portfolio(records, "Bayer", COVERAGE)
        assert portfolio.whitespace == []
        assert len(portfolio.out_of_window) == 1
        assert portfolio.out_of_window[0].coverage_end_year == 2023

    def test_holding_within_the_window_is_still_whitespace(self):
        records = [record(("rivaroxaban",), ("10mg",), "tablet", "NG", "Bayer",
                          approval="2021-06-01")]
        portfolio = build_portfolio(records, "Bayer", COVERAGE)
        assert len(portfolio.whitespace) == 1
        assert portfolio.out_of_window == []

    def test_out_of_window_pairs_are_reported_not_dropped(self):
        records = [record(("rivaroxaban",), ("10mg",), "tablet", "NG", "Bayer",
                          approval="2025-06-01")]
        portfolio = build_portfolio(records, "Bayer", COVERAGE)
        text = render_markdown(portfolio)
        assert "out-of-window" in text.lower()
        assert "2023" in text

    def test_no_cutoff_means_no_exclusion(self):
        # A source with an unknown window must not silently drop pairs.
        tz_unknown = SourceCoverage("tmda", "TZ", "TMDA", 10, "2026-08-30", "")
        records = [record(("rivaroxaban",), ("10mg",), "tablet", "NG", "Bayer",
                          approval="2025-06-01")]
        portfolio = build_portfolio(records, "Bayer", [NG, tz_unknown])
        assert len(portfolio.whitespace) == 1
        assert portfolio.out_of_window == []
