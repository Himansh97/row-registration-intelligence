"""Entity resolution: deciding when two register rows describe the same thing.

Two questions, deliberately kept separate because they fail differently:

  1. Are these two records the same COMPANY?
     Registers record the local marketing entity, not the parent, so
     "Ranbaxy Nigeria Limited" and "Ranbaxy Laboratories" must resolve together.

  2. Are these two records the same PRODUCT?
     "Amoxicillin (Amoxicillin Trihydrate)" + "500 mg" + "Capsule" in Nigeria is
     the same product as "Amoxicillin 500 mg capsule" in Tanzania.

Every decision this module makes carries a confidence and the evidence behind
it. Nothing is resolved silently in either direction: a pair that is neither
clearly a match nor clearly not one goes to a review queue, because a wrong
merge and a wrong split both corrupt the whitespace count.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from rri.normalize import company_tokens
from rri.products import ProductRecord

# Above this, two company names are the same corporate group.
COMPANY_MATCH_THRESHOLD = 80.0
# Between this and the threshold above, a human decides.
COMPANY_REVIEW_THRESHOLD = 60.0


# A first token this short is not distinctive enough to gate a match on.
MIN_DISCRIMINATOR_LENGTH = 3


def _discriminator(tokens: tuple[str, ...]) -> str:
    """The leading token(s) two names must share to be considered the same group.

    Normally the first token. But a very short first token is not distinctive:
    "de" alone leads four unrelated Nigerian companies, "DE - STILL PHARMACY",
    "De - Godstime Industry", "De Big Dan Concept", and gating on it invites a
    wrong merge. When the first token is shorter than three characters, the
    second is folded in, so "S Kant Nigeria" keys on "s kant" and still matches
    "S Kant Healthcare" while "De Big" and "De Still" stay apart.
    """
    if len(tokens[0]) < MIN_DISCRIMINATOR_LENGTH and len(tokens) > 1:
        return f"{tokens[0]} {tokens[1]}"
    return tokens[0]


@dataclass(frozen=True)
class CompanyMatch:
    """A judgement about whether two company names are the same group."""

    left: str
    right: str
    score: float
    first_token_match: bool
    verdict: str  # "match" | "review" | "no-match"
    reason: str


def compare_companies(left: str, right: str) -> CompanyMatch:
    """Judge whether two company names denote the same corporate group.

    Requires the first distinctive token to be equal before any similarity score
    is considered. That single rule is what stops the substring failure mode:
    "Sun Pharmaceutical Industries" and "Anisun Pharmaceutical Company" have
    high character overlap and would score well on fuzzy similarity alone, but
    their first tokens ("sun" vs "anisun") differ, so they never match.

    First-token equality alone is not sufficient either, "Micro Labs" and
    "Micro Nova Pharmaceuticals" share a first token and are different
    companies, so a similarity threshold applies on top.
    """
    left_tokens = company_tokens(left)
    right_tokens = company_tokens(right)

    if not left_tokens or not right_tokens:
        return CompanyMatch(left, right, 0.0, False, "no-match",
                            "one or both names are empty after normalisation")

    left_key = _discriminator(left_tokens)
    right_key = _discriminator(right_tokens)
    first_match = left_key == right_key
    score = fuzz.token_set_ratio(" ".join(left_tokens), " ".join(right_tokens))

    if not first_match:
        return CompanyMatch(
            left, right, score, False, "no-match",
            f"first tokens differ ({left_key!r} vs {right_key!r})",
        )
    if score >= COMPANY_MATCH_THRESHOLD:
        return CompanyMatch(left, right, score, True, "match",
                            f"first token {left_tokens[0]!r}, similarity {score:.0f}")
    if score >= COMPANY_REVIEW_THRESHOLD:
        return CompanyMatch(left, right, score, True, "review",
                            f"first token matches but similarity only {score:.0f}")
    return CompanyMatch(left, right, score, True, "no-match",
                        f"first token matches but similarity only {score:.0f}")


def group_companies(names: list[str]) -> dict[str, list[str]]:
    """Cluster company names into corporate groups.

    Greedy single-pass clustering: each name joins the first group whose
    representative it matches, otherwise starts its own. Sorting by length first
    means the shortest name, usually the bare parent, "Cipla" over "Cipla
    Nigeria Limited", becomes the representative.
    """
    groups: dict[str, list[str]] = {}
    for name in sorted({n for n in names if n}, key=lambda n: (len(n), n)):
        for representative in groups:
            if compare_companies(representative, name).verdict == "match":
                groups[representative].append(name)
                break
        else:
            groups[name] = [name]
    return groups


@dataclass(frozen=True)
class ProductMatch:
    """A judgement about whether two product records are the same product."""

    verdict: str  # "match" | "review" | "no-match"
    score: float
    matched_on: tuple[str, ...]
    reason: str


def compare_products(left: ProductRecord, right: ProductRecord) -> ProductMatch:
    """Judge whether two product records describe the same product.

    INN must agree. Exactly, or closely enough to absorb the spelling errors
    that appear in real registers ("Amlodopine" for "Amlodipine"). Strength and
    dosage form then corroborate.

    A record too thin to match on is reported as unmatchable rather than being
    force-fitted to whatever looks closest.
    """
    if not left.has_usable_identity or not right.has_usable_identity:
        return ProductMatch("no-match", 0.0, (),
                            "one or both records lack a usable identity")

    inn_score = fuzz.token_sort_ratio(" ".join(left.inn), " ".join(right.inn))
    if inn_score < 85:
        return ProductMatch("no-match", inn_score, (),
                            f"ingredient similarity {inn_score:.0f} below 85")

    matched = ["inn"]

    # Strength: when both registers state it, it must agree. Disagreement means
    # a different product, not a weaker match - 500 mg and 250 mg amoxicillin
    # are separate registrations requiring separate filings.
    if left.strength and right.strength:
        if left.strength != right.strength:
            return ProductMatch("no-match", inn_score, tuple(matched),
                                f"strength differs ({'+'.join(left.strength)} "
                                f"vs {'+'.join(right.strength)})")
        matched.append("strength")

    if left.form and right.form:
        if left.form != right.form:
            return ProductMatch("no-match", inn_score, tuple(matched),
                                f"dosage form differs ({left.form} vs {right.form})")
        matched.append("form")

    # INN alone is not enough to call it the same product: it says the molecule
    # matches but not the presentation.
    if len(matched) == 1:
        return ProductMatch("review", inn_score, tuple(matched),
                            "ingredient matches but neither strength nor form "
                            "is stated in both registers")

    score = inn_score if len(matched) >= 3 else inn_score * 0.9
    return ProductMatch("match", score, tuple(matched),
                        f"matched on {', '.join(matched)}")
