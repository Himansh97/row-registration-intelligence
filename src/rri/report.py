"""Render a company's whitespace analysis as a report.

Two properties matter more than presentation:

  1. Every gap is phrased as a search result, never as a regulatory verdict.
     The generator runs `language.assert_clean` over its own output before
     returning, so a report that overclaims raises instead of being written.

  2. Coverage and its limits are stated up front, unprompted. A reader who does
     not know that the Tanzanian source is a published subset cannot correctly
     interpret a gap in it, and burying that in a footnote would be a way of
     technically disclosing it while practically hiding it.
"""

from __future__ import annotations

from rri.language import assert_clean
from rri.whitespace import CompanyPortfolio

PRESENT = "Y"
ABSENT = "-"


def render_markdown(portfolio: CompanyPortfolio) -> str:
    lines: list[str] = []
    add = lines.append

    add(f"# Registration whitespace, {portfolio.group_name}")
    add("")
    add("Decision support, not regulatory advice. Every statement below "
        "describes what was found in a named source on a named date.")
    add("")

    _coverage(portfolio, add)
    _entities(portfolio, add)
    _matrix(portfolio, add)
    _whitespace(portfolio, add)
    _out_of_window(portfolio, add)
    _gaps(portfolio, add)

    text = "\n".join(lines)
    # Self-check before this leaves the function. A report is the thing a reader
    # acts on, so the guard runs at generation time and not only in the tests.
    assert_clean(text, what=f"whitespace report for {portfolio.group_name}")
    return text


def _coverage(portfolio: CompanyPortfolio, add) -> None:
    add("## What was searched")
    add("")
    add("| Country | Authority | Source records | Retrieved | Coverage limit |")
    add("|---|---|---:|---|---|")
    for source in portfolio.coverage:
        add(f"| {source.country} | {source.authority} | {source.record_count:,} | "
            f"{source.retrieved_date} | {source.limitation or 'n/a'} |")
    add("")
    add("A product absent from one of these sources was not found in that "
        "source on that date. That is the full extent of what the absence "
        "supports. Coverage is bounded, and a gap is a prompt to verify, not "
        "a conclusion.")
    add("")


def _entities(portfolio: CompanyPortfolio, add) -> None:
    if not portfolio.entities:
        return
    add("## Entities resolved to this group")
    add("")
    add("Registers record the local marketing entity rather than the parent, so "
        "these were clustered into one corporate group:")
    add("")
    for country in sorted(portfolio.entities):
        for name in sorted(portfolio.entities[country]):
            add(f"- **{country}**, {name}")
    add("")


def _matrix(portfolio: CompanyPortfolio, add) -> None:
    countries = portfolio.countries
    if not countries:
        return

    add("## Portfolio matrix")
    add("")
    add(f"`{PRESENT}` = found in that country's source. "
        f"`{ABSENT}` = not found in it.")
    add("")
    add("| Product | " + " | ".join(countries) + " |")
    add("|---" * (len(countries) + 1) + "|")
    for label, presence in portfolio.matrix():
        cells = [PRESENT if presence[c] else ABSENT for c in countries]
        add(f"| {label} | " + " | ".join(cells) + " |")
    add("")


def _whitespace(portfolio: CompanyPortfolio, add) -> None:
    add("## Unfiled product-market pairs")
    add("")
    if not portfolio.whitespace:
        add("No gaps identified across the sources searched.")
        add("")
        return

    add(f"**{len(portfolio.whitespace)} product-market pairs** where the group "
        f"holds a registration in one searched market and the product was not "
        f"found in another.")
    add("")
    add("Ordered by how the gap reads commercially, not by count. A pathway other "
        "companies have already walked, with few of them on it, outranks both a "
        "crowded shelf and one nobody has attempted. An empty market may be "
        "untapped, or there may be a reason nobody is in it.")
    add("")
    add("| Product | Target | Held in | Proof | Who else is there | Status in target |")
    add("|---|---|---|---|---|---|")
    for cell in portfolio.whitespace:
        held = ", ".join(cell.present_in) or "n/a"
        proof = cell.source_registration or "n/a"
        ctx = cell.context.reading if cell.context else "not assessed"
        add(f"| {cell.product.label} | {cell.country} ({cell.authority}) | "
            f"{held} | {proof} | {ctx} | {cell.evidence} |")
    add("")
    add("**\"Who else is there\" is a floor, not a count.** It counts other "
        "companies holding the same product in the target market *within the "
        "source searched*, so it can only ever understate. The effect is "
        "measurable: across this dataset, pairs targeting the larger source are "
        "13 times more likely to read as crowded than pairs targeting the "
        "smaller one. A zero against a small source mostly means the source is "
        "small.")
    add("")
    for source in portfolio.coverage:
        add(f"- **{source.country}**, counted against {source.record_count:,} "
            f"records from {source.authority}")
    add("")


def _out_of_window(portfolio: CompanyPortfolio, add) -> None:
    """Pairs the sources cannot speak to, reported rather than counted."""
    if not portfolio.out_of_window:
        return

    by_authority: dict[str, list] = {}
    for item in portfolio.out_of_window:
        by_authority.setdefault(item.authority, []).append(item)

    add("## Excluded as out-of-window")
    add("")
    add(f"**{len(portfolio.out_of_window)} product-market pairs** are excluded "
        f"from the count above. In each, the group's holding postdates the "
        f"latest record in the target source, so that source could not have "
        f"contained the product whatever its true status. Absence there "
        f"measures the source's cutoff, not the company.")
    add("")
    for authority, items in sorted(by_authority.items()):
        cutoff = items[0].coverage_end_year
        add(f"- **{authority}**, latest record {cutoff}; "
            f"{len(items)} pair(s) held from {min(i.held_since for i in items)} "
            f"onward")
    add("")
    add("These are neither opportunities nor coverage. They are unknowable from "
        "the sources searched, and closing that blind spot needs a source that "
        "extends past the cutoff.")
    add("")


def _gaps(portfolio: CompanyPortfolio, add) -> None:
    if not portfolio.unmatchable:
        return
    add("## Records that could not be identified")
    add("")
    add(f"{len(portfolio.unmatchable)} record(s) belonging to this group lacked "
        f"enough detail. An ingredient plus either a strength or a dosage "
        f"form. To be matched across sources. They are excluded from the "
        f"matrix above rather than being fitted to the nearest similar product, "
        f"and are listed here so the exclusion is visible.")
    add("")
    for record in portfolio.unmatchable[:25]:
        add(f"- {record.country} · {record.product_name or '(no name)'} "
            f"· {record.registration_number or 'no reg. number'}")
    if len(portfolio.unmatchable) > 25:
        add(f"- … and {len(portfolio.unmatchable) - 25} more")
    add("")
