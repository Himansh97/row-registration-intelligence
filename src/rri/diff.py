"""Snapshot diffing: what changed in a register between two retrievals.

Single-snapshot triggers answer "what does the register say today". Diffing
answers "what moved", which is both more reliable and more urgent:

    a status that flipped to inactive last week is a fresher signal than an
    expiry date that has been sitting in the file for a year

It is also the only part of this system a competitor cannot buy. Scrapers are
copyable in an afternoon; a year of dated snapshots is not. That argues for
starting the clock before the product is sellable.

**The failure mode this module exists to prevent.** If a fetch partially fails,
thousands of records vanish from the new snapshot. Naively diffed, that reads as
mass deregistration. A catastrophic false signal to hand a salesperson. So
disappearance is only reported when the new snapshot is materially complete
relative to the old one, and the check is not optional.
"""

from __future__ import annotations

from dataclasses import dataclass

from rri.products import ProductRecord

#: If a new snapshot has lost more than this share of records, treat the whole
#: comparison as unreliable for disappearance rather than reporting the loss.
#: Registers do prune, but not by a fifth between retrievals.
MAX_PLAUSIBLE_SHRINK = 0.20


@dataclass(frozen=True)
class Change:
    """One observed difference between two snapshots of the same source."""

    kind: str  # "appeared" | "status_changed" | "expiry_changed" | "disappeared"
    key: str
    company: str | None
    product: str
    country: str
    before: str | None
    after: str | None
    evidence: str


@dataclass(frozen=True)
class DiffResult:
    changes: list[Change]
    compared: int
    unkeyed_before: int
    unkeyed_after: int
    shrink: float
    disappearance_reported: bool
    note: str

    def by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.changes:
            out[c.kind] = out.get(c.kind, 0) + 1
        return out


def record_key(record: ProductRecord) -> str | None:
    """A stable identity for a record across snapshots.

    Row position is emphatically not stable. A register that inserts one row
    shifts every index after it, which would read as every record changing at
    once. Only a real identifier works, and a record without one simply cannot
    be diffed. Those are counted, not silently discarded.
    """
    # A row identifier assigned by the register itself is the only fully
    # trustworthy key. Registration numbers look like identifiers but are not:
    # in NAFDAC, 263 numbers are shared across 538 records, and in at least one
    # case the same number covers two unrelated products. Keying on them merges
    # records that are not the same thing, which both hides changes and invents
    # disappearances.
    record_id = record.extras.get("record_id")
    if record_id not in (None, ""):
        return f"{record.source_id}:id:{record_id}"
    if record.registration_number:
        return f"{record.source_id}:reg:{record.registration_number}"
    process = record.extras.get("process_number")
    if process:
        return f"{record.source_id}:proc:{process}"
    document = record.extras.get("document_url")
    if document:
        return f"{record.source_id}:doc:{document}"
    return None


def _expiry(record: ProductRecord) -> str | None:
    for key in ("expiry", "expiry_date"):
        value = record.extras.get(key)
        if value:
            return str(value)[:10]
    return None


def diff(
    before: list[ProductRecord],
    after: list[ProductRecord],
    before_date: str,
    after_date: str,
    authority: str,
) -> DiffResult:
    """Compare two snapshots of one source."""
    old = {}
    unkeyed_before = 0
    for r in before:
        k = record_key(r)
        if k is None:
            unkeyed_before += 1
        else:
            old[k] = r

    new = {}
    unkeyed_after = 0
    for r in after:
        k = record_key(r)
        if k is None:
            unkeyed_after += 1
        else:
            new[k] = r

    shrink = 0.0 if not old else max(0.0, (len(old) - len(new)) / len(old))
    report_gone = shrink <= MAX_PLAUSIBLE_SHRINK

    changes: list[Change] = []

    for key, record in new.items():
        previous = old.get(key)

        if previous is None:
            changes.append(Change(
                kind="appeared", key=key, company=record.company_raw,
                product=record.product_name or " + ".join(record.inn).title(),
                country=record.country, before=None, after=record.status,
                evidence=(f"not present in the {authority} snapshot of {before_date}; "
                          f"present in {after_date}"),
            ))
            continue

        if (previous.status or "") != (record.status or ""):
            changes.append(Change(
                kind="status_changed", key=key, company=record.company_raw,
                product=record.product_name or " + ".join(record.inn).title(),
                country=record.country, before=previous.status, after=record.status,
                evidence=(f"status recorded as {previous.status} in the {authority} "
                          f"snapshot of {before_date} and {record.status} in "
                          f"{after_date}"),
            ))

        old_expiry, new_expiry = _expiry(previous), _expiry(record)
        if old_expiry and new_expiry and old_expiry != new_expiry:
            changes.append(Change(
                kind="expiry_changed", key=key, company=record.company_raw,
                product=record.product_name or " + ".join(record.inn).title(),
                country=record.country, before=old_expiry, after=new_expiry,
                evidence=(f"expiry recorded as {old_expiry} in the {authority} "
                          f"snapshot of {before_date} and {new_expiry} in {after_date}"),
            ))

    if report_gone:
        for key, record in old.items():
            if key not in new:
                changes.append(Change(
                    kind="disappeared", key=key, company=record.company_raw,
                    product=record.product_name or " + ".join(record.inn).title(),
                    country=record.country, before=record.status, after=None,
                    evidence=(f"present in the {authority} snapshot of {before_date}; "
                              f"not found in {after_date}"),
                ))
        note = ""
    else:
        note = (
            f"Disappearances suppressed: the {after_date} snapshot holds "
            f"{shrink:.0%} fewer keyed records than {before_date}, which is more "
            f"consistent with an incomplete retrieval than with removals. "
            f"Re-fetch before drawing any conclusion from the gap."
        )

    changes.sort(key=lambda c: (c.kind, c.company or "", c.product))

    return DiffResult(
        changes=changes,
        compared=len(set(old) | set(new)),
        unkeyed_before=unkeyed_before,
        unkeyed_after=unkeyed_after,
        shrink=shrink,
        disappearance_reported=report_gone,
        note=note,
    )
