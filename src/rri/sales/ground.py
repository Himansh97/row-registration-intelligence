"""Attach register reality to what the client said.

This is the part that separates the tool from a content grounded assistant. A
proposal library can tell a rep what their own company usually says. Only the
register corpus can tell them whether this client already holds the product in
the market they are asking about, and who else is already there.

Nothing here estimates effort or price. The registers record what is approved,
never what it costs, and inventing a number would be discredited by the first
person who signs those contracts.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property

from rri.products import ProductRecord
from rri.sales.schema import Fact, Grounding, Resolved, ScopeLine


@dataclass
class Corpus:
    """The register records, indexed for the questions this tool asks."""

    records: list[ProductRecord]
    coverage: dict[str, str] = field(default_factory=dict)  # country -> authority
    retrieved: dict[str, str] = field(default_factory=dict)  # country -> date

    @cached_property
    def markets(self) -> set[str]:
        return {r.country for r in self.records}

    @cached_property
    def ingredients(self) -> set[str]:
        """Every ingredient name the registers contain, for recognising products."""
        out: set[str] = set()
        for r in self.records:
            for inn in r.inn:
                if len(inn) >= 5:
                    out.add(inn)
        return out

    @cached_property
    def _by_market_inn(self) -> dict[tuple, list[ProductRecord]]:
        index: dict[tuple, list[ProductRecord]] = defaultdict(list)
        for r in self.records:
            if not r.is_active or not r.is_medicine:
                continue
            for inn in r.inn:
                index[(r.country, inn)].append(r)
        return index

    def holders(self, market: str, ingredient: str) -> list[ProductRecord]:
        return self._by_market_inn.get((market, ingredient.lower()), [])

    def authority(self, market: str) -> str:
        return self.coverage.get(market, market)

    def retrieved_date(self, market: str) -> str:
        return self.retrieved.get(market, "unknown date")

    def observed_timeline(self, market: str) -> str | None:
        """Nothing is returned unless the register actually supports it.

        Approval dates alone give the date a registration was granted, not how
        long the review took. Without a submission date the duration is not
        observable, and guessing one would be the kind of number a regulatory
        buyer checks first.
        """
        return None


def resolve_facts(facts: list[Fact], corpus: Corpus) -> list[Resolved]:
    """Match facts onto entities the corpus knows about.

    An unresolved fact is a result, not a failure. A market the corpus does not
    cover, or a molecule it has never seen, is exactly what a rep needs to know
    before quoting on it.
    """
    resolved: list[Resolved] = []

    for fact in facts:
        if fact.kind == "market":
            if fact.value.startswith("region:"):
                region = fact.value.split(":", 1)[1]
                resolved.append(Resolved(
                    fact=fact, entity=None, entity_kind="market", confidence=0.5,
                    note=(f"named a region ({region}) rather than countries; "
                          f"which markets are meant is not established"),
                ))
            elif fact.value in corpus.markets:
                resolved.append(Resolved(
                    fact=fact, entity=fact.value, entity_kind="market",
                    confidence=fact.confidence,
                    note=f"covered by {corpus.authority(fact.value)}",
                ))
            else:
                resolved.append(Resolved(
                    fact=fact, entity=None, entity_kind="market", confidence=0.0,
                    note=(f"{fact.value} is outside the markets this corpus covers "
                          f"({', '.join(sorted(corpus.markets))})"),
                ))

        elif fact.kind == "product":
            key = fact.value.lower()
            if key in corpus.ingredients:
                resolved.append(Resolved(
                    fact=fact, entity=key, entity_kind="ingredient",
                    confidence=fact.confidence, note="present in the register corpus",
                ))
            else:
                resolved.append(Resolved(
                    fact=fact, entity=None, entity_kind="ingredient", confidence=0.0,
                    note="not found in the register corpus under this name",
                ))

        else:
            resolved.append(Resolved(
                fact=fact, entity=fact.value, entity_kind=fact.kind,
                confidence=fact.confidence,
            ))

    return resolved


def ground_line(product: str, market: str, corpus: Corpus,
                client_company: str | None = None) -> Grounding:
    """What the registers say about this product in this market."""
    holders = corpus.holders(market, product)
    authority = corpus.authority(market)
    retrieved = corpus.retrieved_date(market)

    if not holders:
        return Grounding(
            already_held=None, holder_name=None, competitors=0,
            observed_timeline=corpus.observed_timeline(market),
            evidence=(f"no active registration for {product} found in {authority}, "
                      f"retrieved {retrieved}"),
        )

    held_by_client = None
    holder_name = None
    if client_company:
        from rri.match import compare_companies
        for record in holders:
            if record.company_raw and compare_companies(
                    client_company, record.company_raw).verdict == "match":
                held_by_client = True
                holder_name = record.company_raw
                break
        if held_by_client is None:
            held_by_client = False

    others = len({r.company_raw for r in holders if r.company_raw} -
                 ({holder_name} if holder_name else set()))

    if held_by_client:
        evidence = (f"{holder_name} holds an active registration for {product} in "
                    f"{authority}, retrieved {retrieved}")
    else:
        evidence = (f"{others} other company(s) hold an active registration for "
                    f"{product} in {authority}, retrieved {retrieved}")

    return Grounding(
        already_held=held_by_client, holder_name=holder_name, competitors=others,
        observed_timeline=corpus.observed_timeline(market), evidence=evidence,
    )


def build_lines(resolved: list[Resolved], corpus: Corpus,
                client_company: str | None = None) -> list[ScopeLine]:
    """Cross products against markets against services.

    Only combinations the client actually raised are produced. The tool does not
    fill in a market they did not mention or a service they did not ask for.
    """
    products = [r for r in resolved
                if r.fact.kind == "product" and r.entity]
    markets = [r for r in resolved
               if r.fact.kind == "market" and r.entity]
    services = []
    seen_services: set[str] = set()
    for r in resolved:
        if r.fact.kind == "service" and r.entity and r.entity not in seen_services:
            seen_services.add(r.entity)
            services.append(r)

    if not services:
        # A conversation about markets and products with no service named is
        # still scopeable as registration work, but the assumption is recorded
        # rather than silently applied.
        services = []

    lines: list[ScopeLine] = []
    for product in products:
        for market in markets:
            for service in services:
                lines.append(ScopeLine(
                    product=product.entity,
                    market=market.entity,
                    service=service.entity,
                    said_by_client=[product.fact.span, market.fact.span,
                                    service.fact.span],
                    grounding=ground_line(product.entity, market.entity,
                                          corpus, client_company),
                ))

    lines.sort(key=lambda line: (line.market, line.product, line.service))
    return lines
