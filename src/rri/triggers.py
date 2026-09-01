"""Trigger detection: dated, named reasons to contact a company.

Regulatory services business development has no trigger events. Software sales
has renewal dates, recruiting has job postings, this has conferences and
relationships. Public registers are full of triggers nobody reads.

Four kinds, ranked by how hard they are to ignore:

    lapsed          a registration has expired, market access already lost
    renewal_due     a registration expires on a known date, dated obligation
    newly_granted   registrations granted recently. The company is spending
    first_entry     a company's first registration in this market, expanding

The first two are about loss and deadline. The second two are about momentum.

**Language.** A trigger describes the register, never the company's conduct.
"Registration expired on 2026-03-14" is supportable from the source. "Failed to
renew" is a judgement about a real company that the register cannot support, and
`rri.language` blocks it. Companies discontinue products deliberately; a lapse is
not evidence of neglect.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from rri.products import ProductRecord
from rri.whitespace import SourceCoverage

# Severity drives ranking. A loss already taken outranks a future obligation,
# which outranks a sign of momentum.
SEVERITY = {
    "lapsed": 0,
    "renewal_due": 1,
    "newly_granted": 2,
    "first_entry": 3,
}

LABEL = {
    "lapsed": "Registration expired",
    "renewal_due": "Renewal due",
    "newly_granted": "Recently granted",
    "first_entry": "First registration in this market",
}


@dataclass(frozen=True)
class Trigger:
    """One dated reason to contact one company about one product."""

    kind: str
    company: str
    company_key: str | None
    country: str
    authority: str
    product: str
    registration_number: str | None
    date: str  # the date that makes this a trigger
    evidence: str  # phrased so it is supportable from the source alone
    source_ref: str

    @property
    def severity(self) -> int:
        return SEVERITY.get(self.kind, 9)

    @property
    def label(self) -> str:
        return LABEL.get(self.kind, self.kind)


@dataclass
class AccountSignal:
    """Every trigger for one company in one market, aggregated."""

    company: str
    country: str
    authority: str
    triggers: list[Trigger]

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for t in self.triggers:
            out[t.kind] += 1
        return dict(out)

    @property
    def top_kind(self) -> str:
        return min(self.triggers, key=lambda t: t.severity).kind

    @property
    def earliest_date(self) -> str:
        """Soonest deadline among renewals, else the most recent event."""
        due = [t.date for t in self.triggers if t.kind == "renewal_due"]
        return min(due) if due else max(t.date for t in self.triggers)

    @property
    def rank(self) -> tuple:
        """Worst-first: severity, then volume, then soonest date."""
        return (min(t.severity for t in self.triggers),
                -len(self.triggers),
                self.earliest_date)


def _expiry(record: ProductRecord) -> str | None:
    """Expiry date, whatever key the adapter used.

    Nigeria writes `expiry_date` as YYYY-MM-DD; Brazil writes `expiry` as
    YYYY-MM because the day is not published. Both sort and compare correctly
    as strings against a YYYY-MM prefix.
    """
    for key in ("expiry", "expiry_date"):
        value = record.extras.get(key)
        if value:
            return str(value)[:10]
    return None


def _today() -> str:
    return date.today().isoformat()


def _months_ahead(months: int) -> str:
    today = date.today()
    year, month = today.year, today.month + months
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return f"{year:04d}-{month:02d}-{today.day:02d}"


def _months_back(months: int) -> str:
    today = date.today()
    year, month = today.year, today.month - months
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return f"{year:04d}-{month:02d}-{today.day:02d}"


def detect(
    records: list[ProductRecord],
    coverage: SourceCoverage,
    renewal_horizon_months: int = 12,
    lookback_months: int = 12,
) -> list[Trigger]:
    """All triggers for one source.

    Runs on a single snapshot. No history required, so there is no cold start.
    History-based triggers (status flips, withdrawals) come from snapshot
    diffing and are additive to these.
    """
    triggers: list[Trigger] = []
    today = _today()
    horizon = _months_ahead(renewal_horizon_months)
    since = _months_back(lookback_months)
    retrieved = coverage.retrieved_date
    authority = coverage.authority

    # Earliest approval per company, for first-entry detection.
    first_seen: dict[str, str] = {}
    for r in records:
        if r.company_raw and r.approval_date:
            d = str(r.approval_date)[:10]
            if r.company_raw not in first_seen or d < first_seen[r.company_raw]:
                first_seen[r.company_raw] = d

    reported_first_entry: set[str] = set()

    for r in records:
        if not r.company_raw:
            continue

        common = dict(
            company=r.company_raw,
            company_key=r.company,
            country=r.country,
            authority=authority,
            product=r.product_name or " + ".join(r.inn).title() or "(unnamed)",
            registration_number=r.registration_number,
            source_ref=r.source_ref,
        )

        expiry = _expiry(r)

        if not r.is_active:
            # Lapsed. Only counted when the register gives a date, so the claim
            # stays anchored to something a reader can check.
            if expiry and since <= expiry <= today:
                triggers.append(Trigger(
                    kind="lapsed", date=expiry,
                    evidence=(f"registration expired {expiry}; recorded inactive in "
                              f"{authority}, retrieved {retrieved}"),
                    **common))
            continue

        if expiry and today <= expiry <= horizon:
            triggers.append(Trigger(
                kind="renewal_due", date=expiry,
                evidence=(f"registration expires {expiry} per {authority}, "
                          f"retrieved {retrieved}"),
                **common))

        if r.approval_date and str(r.approval_date)[:10] >= since:
            granted = str(r.approval_date)[:10]
            triggers.append(Trigger(
                kind="newly_granted", date=granted,
                evidence=(f"registration granted {granted} per {authority}, "
                          f"retrieved {retrieved}"),
                **common))

            # One first-entry trigger per company, not per product.
            if (first_seen.get(r.company_raw) == granted
                    and r.company_raw not in reported_first_entry):
                reported_first_entry.add(r.company_raw)
                triggers.append(Trigger(
                    kind="first_entry", date=granted,
                    evidence=(f"earliest registration found in {authority} is "
                              f"{granted}, retrieved {retrieved}"),
                    **common))

    triggers.sort(key=lambda t: (t.severity, t.date))
    return triggers


def by_account(triggers: list[Trigger]) -> list[AccountSignal]:
    """Group triggers into one signal per company per market."""
    grouped: dict[tuple[str, str], list[Trigger]] = defaultdict(list)
    for t in triggers:
        grouped[(t.company, t.country)].append(t)

    signals = [
        AccountSignal(company=company, country=country,
                      authority=items[0].authority, triggers=items)
        for (company, country), items in grouped.items()
    ]
    signals.sort(key=lambda s: s.rank)
    return signals


def summarise(triggers: list[Trigger]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for t in triggers:
        counts[t.kind] += 1
    return dict(counts)
