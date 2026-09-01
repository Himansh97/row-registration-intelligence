"""What the client did not say that has to be known before quoting.

A rep leaves a discovery call missing three facts that move the price
substantially and does not know it. This names them, ranked by how much they
move it.

The rule that makes this useful rather than annoying: a gap is only raised when
its absence actually changes the work. "What is your annual turnover" is not a
gap. "Is this a generic or a biosimilar" is, because the two need different
dossiers.

One class of gap is only available because the register corpus exists. When the
client asks about filing a product in a market where they already appear to hold
a registration, the right question is not about price. It is whether they know.
"""

from __future__ import annotations

from rri.sales.schema import Gap, Resolved, ScopeLine

# Facts whose absence changes the shape of the work. Each is only raised when
# the conversation did not already establish it.
REQUIRED = [
    {
        "kind": "product_type",
        "question": "Is this a generic, a biosimilar, or a new chemical entity?",
        "why": "The category decides which dossier is required and therefore the "
               "size of the job. A biosimilar and a generic are not comparable work.",
        "impact": "high",
    },
    {
        "kind": "product",
        "question": "Which specific products or molecules are in scope?",
        "why": "Nothing can be checked against the registers, and no per product "
               "work can be counted, until the products are named.",
        "impact": "high",
    },
    {
        "kind": "market",
        "question": "Which countries specifically?",
        "why": "Requirements, timelines and reliance routes differ by country. A "
               "region is not a market.",
        "impact": "high",
    },
    {
        "kind": "service",
        "question": "Is this new registration, renewal, or variation work?",
        "why": "These are different scopes of work with different deliverables.",
        "impact": "high",
    },
    {
        "kind": "volume",
        "question": "How many products and presentations in total?",
        "why": "Per product work cannot be counted without a number, and clients "
               "often mean SKUs when they say products.",
        "impact": "medium",
    },
    {
        "kind": "timing",
        "question": "Is there a deadline driving this?",
        "why": "A tender or launch date decides sequencing and whether a reliance "
               "route is worth pursuing.",
        "impact": "medium",
    },
]

# Regulatory specifics a rep should leave a call holding. Absence of each one
# genuinely changes the route or the effort.
REGULATORY = [
    {
        "question": "Do they hold a CPP or a reference market approval?",
        "why": "It decides whether a reliance or collaborative route is open, "
               "which changes both timeline and effort.",
        "impact": "high",
        "trigger": "always",
    },
    {
        "question": "Is the dossier already in CTD format, and who owns it?",
        "why": "Reformatting an existing dossier and building one from source "
               "documents are different pieces of work.",
        "impact": "high",
        "trigger": "no_dossier_service",
    },
    {
        "question": "Who is the local representative or licence holder in each market?",
        "why": "Several markets require a local entity to hold the registration. "
               "Without one the filing cannot proceed.",
        "impact": "medium",
        "trigger": "always",
    },
    {
        "question": "Are any of these products already registered in the target markets?",
        "why": "Filing work already done is not work to quote for.",
        "impact": "high",
        "trigger": "no_holding_check",
    },
]


def detect(resolved: list[Resolved], lines: list[ScopeLine],
           client_company: str | None = None) -> list[Gap]:
    """Everything still unknown that changes what the work is."""
    present = {r.fact.kind for r in resolved if r.is_resolved}
    gaps: list[Gap] = []

    for item in REQUIRED:
        if item["kind"] in present:
            continue
        gaps.append(Gap(question=item["question"], why_it_matters=item["why"],
                        impact=item["impact"], kind=item["kind"]))

    # A region was named but no country. The fact resolved to nothing, so the
    # generic "which markets" gap above will not have fired if any single
    # country was also mentioned. Ask anyway, because the region is unscoped.
    for r in resolved:
        if (r.fact.kind == "market" and not r.is_resolved
                and r.fact.value.startswith("region:")):
            region = r.fact.value.split(":", 1)[1]
            gaps.append(Gap(
                question=f"Which countries within {region} are in scope?",
                why_it_matters=("A region is not a market. Requirements and "
                                "timelines differ country by country, and the "
                                "count of countries drives the size of the job."),
                impact="high", kind="market"))

    # A market outside what the corpus covers cannot be checked here at all.
    for r in resolved:
        if r.fact.kind == "market" and not r.is_resolved and "outside" in r.note:
            gaps.append(Gap(
                question=f"Confirm requirements for {r.fact.value} from another source",
                why_it_matters=r.note,
                impact="medium", kind="market"))

    # A product the registers have never seen under that name.
    for r in resolved:
        if r.fact.kind == "product" and not r.is_resolved:
            gaps.append(Gap(
                question=f"Confirm the active ingredient for {r.fact.value}",
                why_it_matters=("It was not found in the register corpus under "
                                "that name, so nothing about it has been checked. "
                                "It may be a brand name."),
                impact="medium", kind="product"))

    has_dossier_service = any(line.service == "dossier_preparation" for line in lines)
    for item in REGULATORY:
        if item["trigger"] == "no_dossier_service" and has_dossier_service:
            continue
        if item["trigger"] == "no_holding_check" and client_company:
            continue
        gaps.append(Gap(question=item["question"], why_it_matters=item["why"],
                        impact=item["impact"], kind="regulatory"))

    if not client_company:
        gaps.append(Gap(
            question="Which legal entity will hold the registrations?",
            why_it_matters=("Without the entity name their existing registrations "
                            "cannot be looked up, so work they have already done "
                            "cannot be excluded from the quote."),
            impact="high", kind="client"))

    gaps.sort(key=lambda g: (g.rank, g.kind))
    return gaps


def already_held(lines: list[ScopeLine]) -> list[ScopeLine]:
    """Scope lines the client appears to hold already.

    The most valuable thing this tool produces. A rep who quotes for a filing
    the client already has, in front of a regulatory affairs director who knows
    their own portfolio, does not recover in that meeting.
    """
    return [line for line in lines
            if line.grounding and line.grounding.already_held]
