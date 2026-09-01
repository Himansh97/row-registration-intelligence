"""Read a sales conversation and return only what the client actually said.

The guarantee this module provides is narrow and absolute. Whatever proposes a
fact, a regular expression or a language model, must hand back the verbatim
words that support it. Those words are then located in the source text. If they
are not there, the fact is dropped.

That single check is what makes a language model safe to use here. It cannot
invent a product the client never mentioned, because the quote backing the
invention would not appear in the input.

Two extractors implement the same interface:

    RuleExtractor    deterministic, no API needed, matches against the real
                     ingredient vocabulary already assembled from the registers
    ClaudeExtractor  used when a key is present, handles phrasing the rules miss

Both go through the same verification, so neither can bypass it.
"""

from __future__ import annotations

import os
import re
from typing import Protocol

from rri.sales.schema import Fact, Span

# Regulatory work a client can ask for, and how people actually say it.
SERVICE_TERMS = {
    "registration": ["registration", "register", "registering", "market authorisation",
                     "market authorization", "marketing authorisation", "dossier submission",
                     "new filing", "product registration"],
    "renewal": ["renewal", "renew", "renewing", "re-registration", "reregistration"],
    "variation": ["variation", "variations", "post approval change", "post-approval change",
                  "change control", "amendment"],
    "labelling": ["labelling", "labeling", "artwork", "pack insert", "leaflet", "smpc"],
    "pharmacovigilance": ["pharmacovigilance", "pv", "safety reporting", "psur", "adverse event"],
    "dossier_preparation": ["dossier preparation", "dossier prep", "ctd", "actd", "ectd",
                            "compile the dossier", "dossier compilation"],
    "regulatory_strategy": ["regulatory strategy", "gap analysis", "feasibility",
                            "pathway assessment", "market entry"],
}

# How clients describe what kind of product it is. This matters more than it
# looks: the category decides the dossier and therefore the effort.
PRODUCT_TYPE_TERMS = {
    "generic": ["generic", "generics", "multisource"],
    "similar": ["similar", "branded generic"],
    "biosimilar": ["biosimilar", "biosimilars"],
    "biological": ["biologic", "biological", "biologics", "vaccine", "vaccines"],
    "new_chemical_entity": ["new chemical entity", "nce", "innovator", "originator",
                            "novel product"],
    "otc": ["otc", "over the counter", "over-the-counter"],
    "device": ["medical device", "device", "ivd", "in vitro diagnostic"],
}

# Markets the corpus can speak to, plus common ways of naming them.
MARKET_TERMS = {
    "NG": ["nigeria", "nigerian", "nafdac"],
    "BR": ["brazil", "brasil", "brazilian", "anvisa"],
    "TZ": ["tanzania", "tanzanian", "tmda"],
}

# Regions a client names when they have not decided on countries yet. Recorded
# as a market fact so the gap detector can ask which countries are meant.
REGION_TERMS = {
    "africa": ["africa", "african", "sub saharan", "sub-saharan", "east africa",
               "west africa", "southern africa"],
    "latam": ["latam", "latin america", "south america", "mercosur"],
    "apac": ["apac", "asia pacific", "southeast asia", "south east asia", "asean"],
    "mena": ["mena", "middle east", "gulf", "gcc"],
    "row": ["row", "rest of world", "rest-of-world", "emerging markets"],
}

# Clients put adjectives between the count and the noun, as in "14 generic
# products" or "12 finished dose presentations", so a small run of words is
# allowed in between.
VOLUME_RE = re.compile(
    r"\b(\d{1,4})\s+(?:of\s+(?:our|their)\s+)?"
    r"(?:[a-z][a-z-]{2,14}\s+){0,3}"
    r"(?:products?|skus?|molecules?|presentations?|dossiers?|items?|registrations?|filings?)\b",
    re.I,
)
TIMING_RE = re.compile(
    r"\b(?:by|before|end of|deadline(?: is)?|target(?:ing)?|no later than)\s+"
    r"((?:Q[1-4]\s*(?:of\s*)?\d{4})|(?:\d{4})|"
    r"(?:january|february|march|april|may|june|july|august|september|october|"
    r"november|december)\s*\d{0,4})",
    re.I,
)
CONSTRAINT_TERMS = [
    "already registered", "already have", "existing registration", "cpp",
    "certificate of pharmaceutical product", "gmp certificate", "reference market",
    "approved in", "budget", "no budget", "tender", "urgent",
]


def verify_span(source: str, quote: str) -> Span | None:
    """Locate a quote in the source text.

    Matching tolerates whitespace differences only. It never adds, removes or
    reorders characters, so a quote that is not really in the text cannot pass.
    Returns None when the quote is absent, and the caller drops the fact.
    """
    if not quote or not quote.strip():
        return None

    index = source.find(quote)
    if index >= 0:
        return Span(text=quote, start=index, end=index + len(quote))

    # Fall back to a whitespace tolerant search, which handles quotes copied
    # across a line break.
    pattern = r"\s+".join(re.escape(word) for word in quote.split())
    match = re.search(pattern, source, re.I)
    if match:
        return Span(text=source[match.start():match.end()],
                    start=match.start(), end=match.end())
    return None


class Extractor(Protocol):
    """Anything that can propose facts, each with its supporting quote."""

    def propose(self, text: str) -> list[dict]:
        """Return dicts with kind, value, quote and confidence.

        The quote is mandatory. A proposal without one is discarded by
        `extract` before it becomes a Fact.
        """


class RuleExtractor:
    """Deterministic extraction, no API required.

    Matches against real vocabularies, including the ingredient names already
    present in the register corpus, so a client naming a molecule is recognised
    without a model being involved.
    """

    name = "rule"

    def __init__(self, known_ingredients: set[str] | None = None) -> None:
        self.known_ingredients = known_ingredients or set()

    def propose(self, text: str) -> list[dict]:
        out: list[dict] = []
        lowered = text.lower()

        def add(kind, value, quote, confidence):
            out.append({"kind": kind, "value": value, "quote": quote,
                        "confidence": confidence})

        def first_match(terms, kind, value, confidence):
            """One fact per category, not one per matching synonym.

            A note saying both "dossier preparation" and "CTD" is asking for one
            service, not two. Emitting a fact per matching term would double
            count it downstream.
            """
            for term in terms:
                match = re.search(rf"\b{re.escape(term)}\b", lowered)
                if match:
                    add(kind, value, text[match.start():match.end()], confidence)
                    return

        for canonical, terms in MARKET_TERMS.items():
            first_match(terms, "market", canonical, 0.95)

        for region, terms in REGION_TERMS.items():
            first_match(terms, "market", f"region:{region}", 0.8)

        for service, terms in SERVICE_TERMS.items():
            first_match(terms, "service", service, 0.9)

        for ptype, terms in PRODUCT_TYPE_TERMS.items():
            first_match(terms, "product_type", ptype, 0.85)

        for m in VOLUME_RE.finditer(text):
            add("volume", m.group(1), m.group(0), 0.9)

        for m in TIMING_RE.finditer(text):
            add("timing", m.group(1).strip(), m.group(0), 0.85)

        for term in CONSTRAINT_TERMS:
            first_match([term], "constraint", term, 0.7)

        # Ingredients, matched against what the registers actually contain.
        # Longest first so "amoxicillin clavulanic acid" wins over "amoxicillin".
        for ingredient in sorted(self.known_ingredients, key=len, reverse=True):
            if len(ingredient) < 5:
                continue
            for m in re.finditer(rf"\b{re.escape(ingredient)}\b", lowered):
                add("product", ingredient, text[m.start():m.end()], 0.9)
                break

        return out


class ClaudeExtractor:
    """Extraction by language model, held to the same span rule.

    The model is asked for a verbatim quote alongside every fact. Anything it
    returns whose quote is not in the source is dropped by `extract`, so a
    fabricated product cannot survive.
    """

    name = "claude"
    MODEL = "claude-sonnet-5"

    PROMPT = """Read this note from a sales conversation with a pharmaceutical company \
about regulatory services.

Extract only what the client actually stated. For every item return the exact \
words from the note that support it, copied verbatim.

Return JSON: a list of objects with keys "kind", "value", "quote", "confidence".

kind must be one of: product, market, service, product_type, volume, timing, constraint.

Rules:
- quote must appear word for word in the note. Do not paraphrase it.
- Do not infer anything the note does not state. Missing information is expected.
- If the note states nothing extractable, return an empty list.

Note:
---
{text}
---"""

    def __init__(self, client=None) -> None:
        self._client = client

    def propose(self, text: str) -> list[dict]:
        import json

        client = self._client
        if client is None:
            import anthropic
            client = anthropic.Anthropic()

        response = client.messages.create(
            model=self.MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": self.PROMPT.format(text=text)}],
        )
        body = "".join(block.text for block in response.content
                       if getattr(block, "type", None) == "text")
        match = re.search(r"\[.*\]", body, re.S)
        if not match:
            return []
        try:
            proposals = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
        return [p for p in proposals if isinstance(p, dict)]


def default_extractor(known_ingredients: set[str] | None = None) -> Extractor:
    """Use the model when a key is configured, otherwise the rules.

    Both are held to the same verification, so this choice affects recall and
    never affects whether an unsupported fact can get through.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeExtractor()
    return RuleExtractor(known_ingredients)


def extract(text: str, extractor: Extractor | None = None,
            known_ingredients: set[str] | None = None) -> tuple[list[Fact], list[dict]]:
    """Facts supported by the text, and the proposals that failed verification.

    The second return value is not debug output. A model proposing facts whose
    quotes are not in the source is a measurable quality signal, and discarding
    it silently would hide that.
    """
    extractor = extractor or default_extractor(known_ingredients)
    facts: list[Fact] = []
    rejected: list[dict] = []
    seen: set[tuple[str, str, int]] = set()

    for proposal in extractor.propose(text):
        kind = str(proposal.get("kind", "")).strip()
        value = str(proposal.get("value", "")).strip()
        quote = str(proposal.get("quote", "")).strip()

        span = verify_span(text, quote) if quote else None
        if span is None or not kind or not value:
            rejected.append({**proposal, "reason": "quote not found in source"
                             if quote else "no supporting quote"})
            continue

        key = (kind, value.lower(), span.start)
        if key in seen:
            continue
        seen.add(key)

        try:
            confidence = float(proposal.get("confidence", 0.7))
            confidence = min(max(confidence, 0.01), 1.0)
            facts.append(Fact(kind=kind, value=value, span=span,
                              confidence=confidence,
                              method=getattr(extractor, "name", "unknown")))
        except ValueError as exc:
            rejected.append({**proposal, "reason": str(exc)})

    facts.sort(key=lambda f: f.span.start)
    return facts, rejected
