"""Build a whitespace report for a company.

Run:  python -m rri.analyze "Cipla"
      python -m rri.analyze "Cipla" --include-inactive
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from rri.products import ProductRecord, from_nafdac, from_tmda, parse_generic_name
from rri.provenance import REPO_ROOT, latest_snapshot, load_records
from rri.report import render_markdown
from rri.whitespace import SourceCoverage, build_portfolio

REPORT_DIR = REPO_ROOT / "data" / "reports"


def _latest_year(records: list[ProductRecord]) -> int | None:
    """Latest registration year actually present in a set of records."""
    years = []
    for r in records:
        try:
            years.append(int(str(r.approval_date)[:4]))
        except (TypeError, ValueError):
            continue
    return max(years) if years else None


def load_nafdac() -> tuple[list[ProductRecord], SourceCoverage | None]:
    snap = latest_snapshot("nafdac_greenbook")
    if snap is None:
        return [], None
    rows = load_records(snap)
    records = [from_nafdac(row, snap.path) for row in rows]
    return records, SourceCoverage(
        source_id=snap.source_id,
        country="NG",
        authority="NAFDAC Greenbook",
        record_count=len(rows),
        retrieved_date=snap.retrieved_date,
        limitation="Full published register",
        coverage_end_year=_latest_year(records),
    )


def load_tmda() -> tuple[list[ProductRecord], SourceCoverage | None]:
    """TMDA records, joined to the marketing authorisation holders extracted
    from their SmPCs.

    A listing row whose document yielded no holder cannot be attributed to a
    company, so it is dropped from company-level analysis. That is a coverage
    reduction, and it is reported in the source limitation rather than being
    absorbed silently.
    """
    listing = latest_snapshot("tmda_approved_products")
    extraction = latest_snapshot("tmda_extractions")
    if listing is None:
        return [], None

    rows = load_records(listing)
    holders: dict[str, dict] = {}
    if extraction is not None:
        for item in load_records(extraction):
            if item.get("local_path"):
                holders[Path(item["local_path"]).name] = item

    records: list[ProductRecord] = []
    attributed = 0
    for row in rows:
        local_path = row.get("local_path")
        extracted = holders.get(Path(local_path).name) if local_path else None

        mah = (extracted or {}).get("marketing_authorisation_holder")
        reg = (extracted or {}).get("registration_number")
        if not mah:
            continue  # no holder -> cannot attribute to a company
        attributed += 1

        enriched = dict(row)
        enriched["marketing_authorisation_holder"] = mah["value"]
        enriched["registration_number"] = reg["value"] if reg else None
        enriched["parsed"] = parse_generic_name(
            row.get("generic_name") or row.get("product_name") or ""
        )
        records.append(from_tmda(enriched, listing.path))

    # TMDA publishes no approval date, but its registration numbers encode the
    # year of registration: "TZ 19 H 0248" -> 2019, "TAN 22 HM 0147" -> 2022.
    # That is the only available signal for how current this source is, and it
    # matters: the published subset stops years before the Nigerian register,
    # so comparing across that boundary would turn a coverage cutoff into
    # phantom whitespace.
    years = []
    for r in records:
        m = re.search(r"\b(?:TAN|TZ)\s?(\d{2})\b", r.registration_number or "", re.I)
        if m:
            years.append(2000 + int(m.group(1)))
    coverage_end = max(years) if years else None

    limitation = (
        f"Published SmPC/TPAR subset, not the full register; "
        f"{attributed} of {len(rows)} listed products had an extractable "
        f"marketing authorisation holder"
    )
    if coverage_end:
        limitation += f"; latest registration observed {coverage_end}"

    return records, SourceCoverage(
        source_id=listing.source_id,
        country="TZ",
        authority="TMDA",
        record_count=attributed,
        retrieved_date=listing.retrieved_date,
        limitation=limitation,
        coverage_end_year=coverage_end,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("company", help="company or corporate group to analyse")
    parser.add_argument("--include-inactive", action="store_true",
                        help="count lapsed registrations as coverage (off by default)")
    parser.add_argument("--out", type=Path, default=None,
                        help="where to write the report")
    args = parser.parse_args()

    nafdac, nafdac_coverage = load_nafdac()
    tmda, tmda_coverage = load_tmda()

    coverage = [c for c in (nafdac_coverage, tmda_coverage) if c]
    if len(coverage) < 2:
        print("ERROR: whitespace needs at least two sources. Run "
              "`python -m rri.ingest_nafdac`, `python -m rri.ingest_tmda`, and "
              "`python -m rri.extract_tmda` first.", file=sys.stderr)
        return 1

    records = nafdac + tmda
    portfolio = build_portfolio(records, args.company, coverage,
                                active_only=not args.include_inactive)

    if not portfolio.products and not portfolio.unmatchable:
        print(f"No records found for {args.company!r} in the sources searched.")
        print("Sources: " + ", ".join(f"{c.authority} ({c.record_count:,})"
                                      for c in coverage))
        return 0

    text = render_markdown(portfolio)

    out_path = args.out or (
        REPORT_DIR / f"{_slug(args.company)}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")

    print(f"company          {args.company}")
    print(f"entities         {sum(len(v) for v in portfolio.entities.values())}")
    print(f"products         {len(portfolio.products)}")
    print(f"whitespace       {len(portfolio.whitespace)}")
    print(f"out-of-window    {len(portfolio.out_of_window)}")
    print(f"unidentifiable   {len(portfolio.unmatchable)}")
    print(f"report           {out_path.relative_to(REPO_ROOT)}")
    return 0


def _slug(value: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "report"


if __name__ == "__main__":
    raise SystemExit(main())
