"""Tests for snapshot diffing.

The dangerous failure here is not missing a change. It is inventing one. A
partial fetch makes thousands of records vanish, and reported naively that reads
as mass deregistration.
"""

from __future__ import annotations

from rri.diff import MAX_PLAUSIBLE_SHRINK, diff, record_key
from rri.language import check
from rri.products import ProductRecord


def rec(reg="A4-1", status="Active", expiry=None, company="Acme Pharma Ltd",
        name="Testomycin", process=None, doc=None):
    extras = {}
    if expiry:
        extras["expiry"] = expiry
    if process:
        extras["process_number"] = process
    if doc:
        extras["document_url"] = doc
    return ProductRecord(
        source_id="test", country="NG", product_name=name,
        inn=("testomycin",), strength=("500mg",), form="tablet", route=(), atc=None,
        company_raw=company, company=company.lower(), registration_number=reg,
        approval_date="2020-01-01", status=status, category="Drugs",
        source_ref="snap#1", extras=extras,
    )


def run(before, after):
    return diff(before, after, "2026-08-01", "2026-09-01", "Test Register")


class TestIdentity:
    def test_registration_number_is_preferred(self):
        assert record_key(rec(reg="A4-9")).endswith(":reg:A4-9")

    def test_falls_back_to_process_number(self):
        k = record_key(rec(reg=None, process="P-123"))
        assert k and k.endswith(":proc:P-123")

    def test_falls_back_to_document_url(self):
        k = record_key(rec(reg=None, doc="http://x/y.pdf"))
        assert k and k.endswith(":doc:http://x/y.pdf")

    def test_record_with_no_identifier_cannot_be_diffed(self):
        assert record_key(rec(reg=None)) is None

    def test_unkeyed_records_are_counted_not_dropped(self):
        r = run([rec(reg=None)], [rec(reg=None)])
        assert r.unkeyed_before == 1 and r.unkeyed_after == 1
        assert r.changes == []


class TestChangeDetection:
    def test_new_record_appears(self):
        r = run([rec(reg="A-1")], [rec(reg="A-1"), rec(reg="A-2")])
        assert [c.kind for c in r.changes] == ["appeared"]
        assert r.changes[0].key.endswith("A-2")

    def test_status_flip_is_detected(self):
        r = run([rec(status="Active")], [rec(status="Inactive")])
        assert [c.kind for c in r.changes] == ["status_changed"]
        assert (r.changes[0].before, r.changes[0].after) == ("Active", "Inactive")

    def test_expiry_change_is_detected(self):
        r = run([rec(expiry="2027-01-01")], [rec(expiry="2032-01-01")])
        assert [c.kind for c in r.changes] == ["expiry_changed"]

    def test_expiry_appearing_for_the_first_time_is_not_a_change(self):
        # Absent then present is the register filling a gap, not a renewal.
        assert run([rec(expiry=None)], [rec(expiry="2030-01-01")]).changes == []

    def test_unchanged_records_produce_nothing(self):
        assert run([rec(), rec(reg="A-2")], [rec(), rec(reg="A-2")]).changes == []

    def test_disappearance_is_detected_when_the_snapshot_is_intact(self):
        before = [rec(reg=f"A-{i}") for i in range(10)]
        after = before[:9]
        r = run(before, after)
        assert [c.kind for c in r.changes] == ["disappeared"]
        assert r.disappearance_reported


class TestPartialFetchGuard:
    def test_mass_loss_suppresses_disappearance(self):
        # A fetch that lost half the register must not report 50 removals.
        before = [rec(reg=f"A-{i}") for i in range(100)]
        after = before[:50]
        r = run(before, after)
        assert not r.disappearance_reported
        assert not any(c.kind == "disappeared" for c in r.changes)
        assert "incomplete retrieval" in r.note

    def test_loss_just_under_the_threshold_is_still_reported(self):
        before = [rec(reg=f"A-{i}") for i in range(100)]
        after = before[:int(100 * (1 - MAX_PLAUSIBLE_SHRINK))]
        r = run(before, after)
        assert r.disappearance_reported

    def test_other_change_kinds_survive_the_guard(self):
        # Suppressing disappearance must not suppress real status changes.
        before = [rec(reg=f"A-{i}") for i in range(100)]
        after = [rec(reg="A-0", status="Inactive")] + before[1:40]
        r = run(before, after)
        assert not r.disappearance_reported
        assert any(c.kind == "status_changed" for c in r.changes)

    def test_growth_never_triggers_the_guard(self):
        before = [rec(reg="A-1")]
        after = [rec(reg=f"A-{i}") for i in range(50)]
        r = run(before, after)
        assert r.shrink == 0.0 and r.disappearance_reported


class TestEvidenceLanguage:
    def test_all_evidence_passes_the_output_linter(self):
        before = [rec(reg="A-1", status="Active", expiry="2027-01-01"), rec(reg="A-2")]
        after = [rec(reg="A-1", status="Inactive", expiry="2028-01-01"), rec(reg="A-3")]
        for c in run(before, after).changes:
            assert not check(c.evidence), f"{c.kind}: {c.evidence}"

    def test_evidence_names_both_snapshot_dates(self):
        c = run([rec(status="Active")], [rec(status="Inactive")]).changes[0]
        assert "2026-08-01" in c.evidence and "2026-09-01" in c.evidence

    def test_evidence_describes_the_register_not_the_company(self):
        c = run([rec(status="Active")], [rec(status="Inactive")]).changes[0]
        assert "status recorded as" in c.evidence
        for blamed in ("failed", "lost", "neglected", "withdrew"):
            assert blamed not in c.evidence.lower()


class TestEmptyInputs:
    def test_empty_before_reports_everything_as_appeared(self):
        r = run([], [rec(), rec(reg="A-2")])
        assert r.by_kind() == {"appeared": 2}

    def test_empty_after_suppresses_disappearance_entirely(self):
        # Losing every record is the clearest possible sign of a broken fetch.
        r = run([rec(), rec(reg="A-2")], [])
        assert not r.disappearance_reported
        assert r.changes == []

    def test_both_empty(self):
        r = run([], [])
        assert r.changes == [] and r.compared == 0


class TestNonUniqueRegistrationNumbers:
    """Registration numbers look like identifiers but are not.

    In the NAFDAC register 263 numbers are shared across 538 records, and at
    least one number covers two unrelated products. Keying on them merges
    records that are not the same thing.
    """

    def test_register_row_id_is_preferred_over_registration_number(self):
        r = rec(reg="A4-1205")
        r = ProductRecord(**{**r.__dict__, "extras": {"record_id": 4711}})
        assert record_key(r).endswith(":id:4711")

    def test_two_products_sharing_a_registration_number_stay_distinct(self):
        # Both carry A4-1205 in the real register.
        a = ProductRecord(**{**rec(reg="A4-1205", name="Dermovate Cream").__dict__,
                             "extras": {"record_id": 1}})
        b = ProductRecord(**{**rec(reg="A4-1205", name="EBU 200 Tablets").__dict__,
                             "extras": {"record_id": 2}})
        assert record_key(a) != record_key(b)

    def test_shared_number_without_row_ids_would_collapse(self):
        # Documents the hazard the row id exists to avoid.
        a = rec(reg="A4-1205", name="Dermovate Cream")
        b = rec(reg="A4-1205", name="EBU 200 Tablets")
        assert record_key(a) == record_key(b)

    def test_removals_reconcile_with_the_record_count_delta(self):
        # With every record keyed, disappearances must equal the shortfall
        # exactly. Anything else means keys are colliding. Every record here
        # shares one registration number, so only the row id keeps them apart.
        # The loss is kept under the partial-fetch threshold so the guard does
        # not suppress the very thing being measured.
        before = [ProductRecord(**{**rec(reg="SHARED").__dict__,
                                   "extras": {"record_id": i}}) for i in range(100)]
        after = before[:90]
        r = run(before, after)
        assert r.disappearance_reported
        assert r.by_kind() == {"disappeared": 10}
        assert len(before) - len(after) == 10
        assert r.unkeyed_before == 0 and r.unkeyed_after == 0

    def test_a_zero_row_id_is_a_valid_identifier(self):
        # 0 is falsy; a truthiness check here would silently drop the first
        # record of any zero-indexed register.
        r = ProductRecord(**{**rec(reg=None).__dict__, "extras": {"record_id": 0}})
        assert record_key(r) is not None
        assert record_key(r).endswith(":id:0")
