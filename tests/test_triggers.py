"""Tests for trigger detection.

A trigger is a claim about a real company, sent to someone who will act on it.
Two failure modes matter equally: inventing a trigger that isn't there, and
phrasing a real one as a judgement the register cannot support.
"""

from __future__ import annotations

from datetime import date

import pytest

from rri.language import check
from rri.products import ProductRecord
from rri.triggers import by_account, detect, summarise
from rri.whitespace import SourceCoverage

COV = SourceCoverage("test", "NG", "Test Register", 100, "2026-08-31",
                     "test source", coverage_end_year=2026)


def months(n: int) -> str:
    today = date.today()
    year, month = today.year, today.month + n
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return f"{year:04d}-{month:02d}-{today.day:02d}"


def rec(company="Acme Pharma Ltd", status="Active", expiry=None, approval=None,
        name="Testomycin 500mg", reg="A4-1"):
    return ProductRecord(
        source_id="test", country="NG", product_name=name,
        inn=("testomycin",), strength=("500mg",), form="tablet", route=(), atc=None,
        company_raw=company, company=company.lower(), registration_number=reg,
        approval_date=approval, status=status, category="Drugs",
        source_ref="snap#1", extras={"expiry": expiry} if expiry else {},
    )


class TestRenewalDue:
    def test_expiry_inside_horizon_is_a_trigger(self):
        t = detect([rec(expiry=months(3))], COV)
        assert [x.kind for x in t] == ["renewal_due"]
        assert t[0].date == months(3)

    def test_expiry_beyond_horizon_is_not(self):
        assert detect([rec(expiry=months(24))], COV) == []

    def test_expiry_in_the_past_on_an_active_record_is_not_a_renewal(self):
        # Already expired but still marked active. A data inconsistency, not a
        # future obligation. Reporting it as "renewal due" would put a past date
        # in front of a salesperson as a deadline.
        assert detect([rec(expiry=months(-3))], COV) == []

    def test_no_expiry_means_no_trigger(self):
        assert detect([rec(expiry=None)], COV) == []


class TestLapsed:
    def test_recently_expired_inactive_record_is_lapsed(self):
        t = detect([rec(status="Inactive", expiry=months(-2))], COV)
        assert [x.kind for x in t] == ["lapsed"]

    def test_long_expired_is_outside_the_lookback(self):
        assert detect([rec(status="Inactive", expiry=months(-30))], COV) == []

    def test_inactive_without_a_date_is_not_reported(self):
        # Without a date there is nothing for a reader to verify, and no way to
        # know whether it lapsed last month or a decade ago.
        assert detect([rec(status="Inactive", expiry=None)], COV) == []

    def test_inactive_records_never_produce_forward_triggers(self):
        t = detect([rec(status="Inactive", expiry=months(3), approval=months(-1))], COV)
        assert all(x.kind == "lapsed" or x.kind not in {"renewal_due", "newly_granted"}
                   for x in t)


class TestMomentum:
    def test_recent_approval_is_newly_granted(self):
        kinds = {x.kind for x in detect([rec(approval=months(-2))], COV)}
        assert "newly_granted" in kinds

    def test_old_approval_is_not(self):
        assert detect([rec(approval=months(-30))], COV) == []

    def test_first_entry_fires_once_per_company_not_per_product(self):
        recs = [rec(approval=months(-2), name=f"Product {i}", reg=f"A4-{i}")
                for i in range(5)]
        t = detect(recs, COV)
        assert sum(1 for x in t if x.kind == "first_entry") == 1

    def test_established_company_is_not_a_first_entrant(self):
        recs = [rec(approval=months(-40), reg="A4-old"),
                rec(approval=months(-2), reg="A4-new")]
        t = detect(recs, COV)
        assert not any(x.kind == "first_entry" for x in t)


class TestEvidenceLanguage:
    def test_every_evidence_string_passes_the_output_linter(self):
        recs = [rec(status="Inactive", expiry=months(-2)),
                rec(expiry=months(4), reg="A4-2"),
                rec(approval=months(-1), reg="A4-3")]
        for t in detect(recs, COV):
            assert not check(t.evidence), f"{t.kind}: {t.evidence}"

    def test_evidence_names_the_source_and_retrieval_date(self):
        t = detect([rec(expiry=months(3))], COV)[0]
        assert "Test Register" in t.evidence
        assert COV.retrieved_date in t.evidence

    def test_evidence_states_the_register_not_the_companys_conduct(self):
        t = detect([rec(status="Inactive", expiry=months(-2))], COV)[0]
        low = t.evidence.lower()
        assert "expired" in low
        for blamed in ("failed", "neglect", "did not renew", "lapsed to"):
            assert blamed not in low


class TestRankingAndGrouping:
    def test_lapsed_outranks_renewal_which_outranks_momentum(self):
        recs = [rec(approval=months(-1), reg="A4-3"),
                rec(expiry=months(4), reg="A4-2"),
                rec(status="Inactive", expiry=months(-2), reg="A4-1")]
        kinds = [t.kind for t in detect(recs, COV)]
        assert kinds[0] == "lapsed"
        assert kinds.index("lapsed") < kinds.index("renewal_due")

    def test_accounts_group_by_company_and_market(self):
        recs = [rec(company="Acme Pharma Ltd", expiry=months(3), reg="A4-1"),
                rec(company="Acme Pharma Ltd", expiry=months(5), reg="A4-2"),
                rec(company="Beta Labs Ltd", expiry=months(4), reg="B-1")]
        accounts = by_account(detect(recs, COV))
        assert len(accounts) == 2
        acme = next(a for a in accounts if a.company == "Acme Pharma Ltd")
        assert len(acme.triggers) == 2

    def test_account_earliest_date_is_the_soonest_deadline(self):
        recs = [rec(expiry=months(9), reg="A4-1"), rec(expiry=months(2), reg="A4-2")]
        assert by_account(detect(recs, COV))[0].earliest_date == months(2)

    def test_worst_account_sorts_first(self):
        recs = [rec(company="Clean Co", expiry=months(6), reg="C-1"),
                rec(company="Losing Co", status="Inactive", expiry=months(-1), reg="L-1")]
        assert by_account(detect(recs, COV))[0].company == "Losing Co"


class TestSummary:
    def test_counts_by_kind(self):
        recs = [rec(status="Inactive", expiry=months(-2), reg="A-1"),
                rec(expiry=months(3), reg="A-2")]
        s = summarise(detect(recs, COV))
        assert s["lapsed"] == 1 and s["renewal_due"] == 1

    def test_no_records_yields_no_triggers(self):
        assert detect([], COV) == []
        assert summarise([]) == {}


class TestSourcesWithoutTheData:
    def test_a_source_lacking_dates_produces_nothing_rather_than_guesses(self):
        # Tanzania publishes neither expiry nor reliable approval dates. The
        # right behaviour is silence, not inferred triggers.
        recs = [rec(expiry=None, approval=None, reg=None)]
        assert detect(recs, COV) == []
