"""Whitespace: products a company has registered somewhere but not everywhere.

The question this answers is deliberately narrow and defensible:

    "This company holds an active registration for product P in Nigeria.
     Product P does not appear in the Tanzanian source we searched."

That is an expansion opportunity. A filing the company could make and has not.
It is NOT a claim that the product is unregistered in Tanzania, and the language
in this module is chosen so that distinction survives into the output.

Absence of evidence is not evidence of absence. Every source has bounded
coverage: TMDA publishes SmPCs for selected products only, and any scrape can
miss rows. A gap therefore reports what was searched and when, never a verdict
about the company's regulatory status.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from rri.match import compare_products, group_companies
from rri.products import ProductRecord


@dataclass(frozen=True)
class SourceCoverage:
    """What one source actually contained, so gaps can be stated honestly."""

    source_id: str
    country: str
    authority: str
    record_count: int
    retrieved_date: str
    limitation: str = ""
    # Latest registration year actually observed in this source. A source that
    # stops in 2023 cannot evidence anything about a product first registered
    # elsewhere in 2025, and comparing across that boundary manufactures
    # whitespace out of a coverage cutoff.
    coverage_end_year: int | None = None

    def not_found_phrase(self) -> str:
        """How a gap in this source must be described in any output.

        Never "not registered". The strongest supportable statement is that the
        product did not appear in a specific source retrieved on a specific date.
        """
        return f"not found in {self.authority} source, retrieved {self.retrieved_date}"


@dataclass
class ProductPresence:
    """One product identity and where it was found."""

    inn: tuple[str, ...]
    strength: tuple[str, ...]
    form: str | None
    records: dict[str, ProductRecord] = field(default_factory=dict)  # country -> record

    @property
    def label(self) -> str:
        parts = [" + ".join(self.inn).title()]
        if self.strength:
            parts.append(" + ".join(self.strength))
        if self.form:
            parts.append(self.form)
        return " ".join(parts)


@dataclass(frozen=True)
class MarketContext:
    """Who else already holds this product in the target market.

    A gap on its own says nothing about whether filing is sensible. The same
    absence means opposite things depending on the company it keeps:

      many holders  the pathway is well trodden and the shelf is crowded
      few holders   a proven route with room left
      no holders    either untapped, or there is a reason nobody is
                    there - which is a question worth asking before filing

    Counted from the same registers, so it inherits their coverage limits and
    carries no more authority than they do.
    """

    competitors: int
    examples: tuple[str, ...]

    @property
    def has_precedent(self) -> bool:
        """Whether anyone at all holds this product in the target market."""
        return self.competitors > 0

    @property
    def reading(self) -> str:
        if self.competitors == 0:
            return "no holder found in this source"
        if self.competitors <= 2:
            return f"{self.competitors} other holder(s) found"
        return f"{self.competitors} other holders found"


@dataclass
class WhitespaceCell:
    """One product-market pair the company has not filed in."""

    product: ProductPresence
    country: str
    authority: str
    present_in: tuple[str, ...]  # countries where it WAS found
    evidence: str  # the not-found phrasing, with source and date
    source_registration: str | None  # a registration number proving they hold it
    context: MarketContext | None = None  # who else is already there


@dataclass
class OutOfWindow:
    """A pair excluded because the target source predates the holding.

    Not whitespace and not coverage, simply unknowable from these sources.
    Counted and reported rather than silently dropped, because the size of this
    set is the honest measure of how much the comparison cannot see.
    """

    product: ProductPresence
    country: str
    authority: str
    held_since: str
    coverage_end_year: int


@dataclass
class CompanyPortfolio:
    """Everything known about one corporate group across the sources searched."""

    group_name: str
    entities: dict[str, list[str]]  # country -> local entity names
    products: list[ProductPresence]
    coverage: list[SourceCoverage]
    whitespace: list[WhitespaceCell]
    unmatchable: list[ProductRecord]
    out_of_window: list[OutOfWindow]

    @property
    def countries(self) -> list[str]:
        return sorted({c.country for c in self.coverage})

    def matrix(self) -> list[tuple[str, dict[str, bool]]]:
        """Product x country presence, for rendering the map."""
        rows = []
        for product in sorted(self.products, key=lambda p: p.label):
            rows.append((product.label,
                         {c: c in product.records for c in self.countries}))
        return rows


def find_company_records(
    records: list[ProductRecord], query: str
) -> tuple[list[ProductRecord], dict[str, list[str]]]:
    """All records belonging to the corporate group matching `query`.

    Grouping runs over the observed company names rather than matching each name
    against the query directly, so local entities cluster with their parent even
    when the query names neither exactly.
    """
    from rri.match import compare_companies

    names = [r.company_raw for r in records if r.company_raw]
    groups = group_companies(names)

    target_names: set[str] = set()
    for representative, members in groups.items():
        if compare_companies(query, representative).verdict == "match" or any(
            compare_companies(query, m).verdict == "match" for m in members
        ):
            target_names.update(members)

    matched = [r for r in records if r.company_raw in target_names]

    entities: dict[str, list[str]] = defaultdict(list)
    for record in matched:
        if record.company_raw not in entities[record.country]:
            entities[record.country].append(record.company_raw)

    return matched, dict(entities)


def build_presences(records: list[ProductRecord]) -> tuple[list[ProductPresence],
                                                           list[ProductRecord]]:
    """Cluster a company's records into distinct products across countries.

    Records too thin to identify are returned separately rather than being
    folded into whatever product looks closest. A wrong merge here would erase
    a real whitespace cell.
    """
    presences: list[ProductPresence] = []
    unmatchable: list[ProductRecord] = []

    for record in records:
        if not record.has_usable_identity:
            unmatchable.append(record)
            continue

        for presence in presences:
            probe = ProductRecord(
                source_id=record.source_id, country=record.country,
                product_name="", inn=presence.inn, strength=presence.strength,
                form=presence.form, route=(), atc=None, company_raw=None,
                company=None, registration_number=None, approval_date=None,
                status="Active", category=None, source_ref="",
            )
            if compare_products(probe, record).verdict == "match":
                # Keep the first record seen per country; duplicates within a
                # country are the same registration in different pack sizes.
                presence.records.setdefault(record.country, record)
                break
        else:
            presences.append(ProductPresence(
                inn=record.inn, strength=record.strength, form=record.form,
                records={record.country: record},
            ))

    return presences, unmatchable


def _year(value) -> int | None:
    try:
        return int(str(value)[:4])
    except (TypeError, ValueError):
        return None


def build_market_index(records: list[ProductRecord]) -> dict[tuple, list[ProductRecord]]:
    """Index every active medicine by country and ingredient.

    INN is the blocking key: two records cannot be the same product unless their
    ingredients agree, so comparing only within an ingredient bucket gives the
    same answer as comparing everything, at a fraction of the cost.
    """
    index: dict[tuple, list[ProductRecord]] = defaultdict(list)
    for record in records:
        if record.inn and record.is_active and record.is_medicine:
            index[(record.country, record.inn)].append(record)
    return index


def market_context(
    presence: ProductPresence,
    country: str,
    index: dict[tuple, list[ProductRecord]],
    exclude_companies: set[str],
) -> MarketContext:
    """How many other companies already hold this product in `country`.

    The company under analysis is excluded, so the count answers "who would I be
    joining", not "who is there including me".
    """
    probe = ProductRecord(
        source_id="", country=country, product_name="", inn=presence.inn,
        strength=presence.strength, form=presence.form, route=(), atc=None,
        company_raw=None, company=None, registration_number=None,
        approval_date=None, status="Active", category=None, source_ref="",
    )

    holders: dict[str, None] = {}
    for candidate in index.get((country, presence.inn), ()):
        if candidate.company in exclude_companies:
            continue
        if compare_products(probe, candidate).verdict == "match":
            if candidate.company_raw:
                holders.setdefault(candidate.company_raw, None)

    names = tuple(sorted(holders))
    return MarketContext(competitors=len(names), examples=names[:4])


def compute_whitespace(
    presences: list[ProductPresence],
    coverage: list[SourceCoverage],
    index: dict[tuple, list[ProductRecord]] | None = None,
    exclude_companies: set[str] | None = None,
) -> tuple[list[WhitespaceCell], list[OutOfWindow]]:
    """For each product, the countries it was not found in.

    A gap only counts when the target source could plausibly have contained the
    product. If the company first registered something in 2025 and the target
    source holds nothing after 2023, its absence there is a fact about the
    source's cutoff, not about the company, so it is set aside as out-of-window
    rather than counted as an opportunity.

    Whitespace is ranked by how many markets already carry the product: held in
    several and missing in one is a more obvious next filing than held once.
    """
    by_country = {c.country: c for c in coverage}
    cells: list[WhitespaceCell] = []
    excluded: list[OutOfWindow] = []

    for presence in presences:
        present_in = tuple(sorted(presence.records))
        for country, source in by_country.items():
            if country in presence.records:
                continue

            source_record = presence.records[present_in[0]] if present_in else None
            held_year = _year(source_record.approval_date) if source_record else None

            if (source.coverage_end_year is not None and held_year is not None
                    and held_year > source.coverage_end_year):
                excluded.append(OutOfWindow(
                    product=presence,
                    country=country,
                    authority=source.authority,
                    held_since=str(source_record.approval_date)[:10],
                    coverage_end_year=source.coverage_end_year,
                ))
                continue

            context = None
            if index is not None:
                context = market_context(presence, country, index,
                                         exclude_companies or set())

            cells.append(WhitespaceCell(
                product=presence,
                country=country,
                authority=source.authority,
                present_in=present_in,
                evidence=source.not_found_phrase(),
                source_registration=(
                    source_record.registration_number if source_record else None
                ),
                context=context,
            ))

    # Ranking encodes a commercial judgement, so it is written out plainly:
    # a proven pathway with few incumbents outranks both a crowded shelf and an
    # empty one. Zero holders is not automatically best - it may mean nobody has
    # found it worth doing.
    def rank(cell: WhitespaceCell):
        n = cell.context.competitors if cell.context else -1
        if n < 0:
            tier = 2           # unknown context
        elif n == 0:
            tier = 1           # no precedent - worth asking why
        elif n <= 3:
            tier = 0           # proven route, room left
        else:
            tier = 3           # crowded
        return (tier, -len(cell.present_in), n, cell.product.label)

    cells.sort(key=rank)
    excluded.sort(key=lambda e: e.product.label)
    return cells, excluded


def build_portfolio(
    records: list[ProductRecord],
    query: str,
    coverage: list[SourceCoverage],
    active_only: bool = True,
) -> CompanyPortfolio:
    """Assemble the full picture for one company across the sources searched."""
    company_records, entities = find_company_records(records, query)

    if active_only:
        # A lapsed registration is not market access. Counting it as coverage
        # would hide a real opportunity.
        company_records = [r for r in company_records if r.is_active]

    company_records = [r for r in company_records if r.is_medicine]

    presences, unmatchable = build_presences(company_records)

    # Context is measured against every company in the sources, not just this
    # one, so the full record set is needed here.
    index = build_market_index(records)
    own = {r.company for r in company_records if r.company}
    whitespace, out_of_window = compute_whitespace(
        presences, coverage, index=index, exclude_companies=own)

    return CompanyPortfolio(
        group_name=query,
        entities=entities,
        products=presences,
        coverage=coverage,
        whitespace=whitespace,
        unmatchable=unmatchable,
        out_of_window=out_of_window,
    )
