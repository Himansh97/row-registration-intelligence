"""Canonical forms for the fields used to match a product across registers.

Registers describe the same product differently. Nigeria says
"Amoxicillin (Amoxicillin Trihydrate); Clavulanic Acid" in a dedicated
ingredient field; Tanzania says "Amoxicillin 500 mg capsule" in a free-text
generic-name field. Nothing joins until both are reduced to the same shape.

Two rules run through all of this:

  1. Missing is missing. Registers write "NA", "see Composition", "-" and blank
     to mean absent. All of it collapses to None. A placeholder that survives
     into the matcher becomes a false match.
  2. Normalisation never invents. Where a value cannot be parsed confidently it
     returns None rather than a guess, and the caller decides what to do with
     the gap.
"""

from __future__ import annotations

import html
import re

# Values registers use to mean "no value". Checked lowercased and stripped.
MISSING_TOKENS = {
    "", "na", "n/a", "n.a.", "nil", "none", "null", "-", "--",
    "not applicable", "not available", "see composition", "as above",
}

# Legal-form and corporate-noise tokens. Removed before comparing company names.
# "pharma"/"pharmaceutical" stay OUT of this list: they carry signal in names
# like "Sun Pharmaceutical", and stripping them collapses distinct companies.
LEGAL_TOKENS = {
    "ltd", "limited", "plc", "inc", "incorporated", "corp", "corporation",
    "co", "company", "gmbh", "ag", "sa", "srl", "spa", "bv", "nv", "as",
    "pvt", "pte", "private", "llc", "lp", "llp", "kg", "kft", "oy", "ab",
    "sas", "sarl", "cc", "and", "the", "of", "amp",
}

# Geographic qualifiers on local marketing entities. "Ranbaxy Nigeria Limited"
# and "Ranbaxy Laboratories" are the same corporate group; the country token is
# what makes them look different.
GEO_TOKENS = {
    "nigeria", "nigerian", "nig", "tanzania", "tanzanian", "zambia", "kenya",
    "ghana", "uganda", "africa", "african", "india", "indian", "east", "west",
    "south", "north", "international", "global", "overseas", "export",
}

_PAREN_RE = re.compile(r"\(([^)]*)\)")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s./%-]")
_WS_RE = re.compile(r"\s+")

# Dosage forms collapse to coarse buckets. Registers disagree on adjectives
# ("Film Coated Tablet" vs "Tablet") but agree on the underlying form, and it is
# the underlying form that decides whether two records are the same product.
FORM_BUCKETS = {
    "tablet": {"tablet", "tablets", "caplet", "caplets", "film coated tablet",
               "coated tablet", "dispersible tablet", "chewable tablet",
               "effervescent tablet", "scored tablet", "sugar coated tablet",
               "enteric coated tablet", "prolonged release tablet",
               "modified release tablet", "orodispersible tablet", "vaginal tablet"},
    "capsule": {"capsule", "capsules", "hard capsule", "soft capsule",
                "softgel", "soft gelatin capsule", "hard gelatin capsule"},
    "injection": {"injection", "injections", "powder for injection",
                  "solution for injection", "suspension for injection",
                  "injectable", "infusion", "solution for infusion"},
    "oral_liquid": {"syrup", "suspension", "oral solution", "oral suspension",
                    "powder for suspension", "elixir", "oral liquid", "liquid",
                    "solution", "drops", "oral drops"},
    "cream": {"cream", "ointment", "gel", "lotion", "paste", "topical cream",
              "topical gel", "topical ointment"},
    "drops": {"solution/drops", "eye drops", "ear drops", "ophthalmic solution",
              "otic solution", "nasal drops"},
    "suppository": {"suppository", "suppositories", "pessary", "pessaries"},
    "powder": {"powder", "granules", "sachet", "powder for oral suspension"},
    "inhaler": {"inhaler", "inhalation", "metered dose inhaler", "nebuliser solution"},
    "patch": {"patch", "transdermal patch"},
}
_FORM_LOOKUP = {variant: bucket for bucket, variants in FORM_BUCKETS.items()
                for variant in variants}


def is_missing(value) -> bool:
    """True when a register field carries no usable value."""
    if value is None:
        return True
    return str(value).strip().lower() in MISSING_TOKENS


def clean(value) -> str | None:
    """Trim, decode HTML entities, and lowercase; placeholders collapse to None.

    The NAFDAC feed carries raw HTML entities in company and product names,
    "St Luke&#039;s Pharmaceuticals", "J &amp; J Company West Africa". Left
    encoded, "&amp;" tokenises as the word "amp" and becomes part of the
    company's matching key.
    """
    if is_missing(value):
        return None
    text = html.unescape(str(value))
    if is_missing(text):
        return None
    return _WS_RE.sub(" ", text.strip().lower())


def normalize_inn(value) -> tuple[str, ...]:
    """Reduce an ingredient string to a sorted tuple of base INNs.

    Multi-ingredient products are semicolon-separated, and salt forms appear in
    parentheses after the base name:

        "Amoxicillin (Amoxicillin Trihydrate); Clavulanic Acid"
            -> ("amoxicillin", "clavulanic acid")

    The parenthetical is dropped rather than kept: the salt varies between
    registers for what is clinically the same product, so keeping it would split
    matches that should join. Sorted so ingredient order cannot affect the key.
    """
    text = clean(value)
    if text is None:
        return ()

    parts = [p for p in re.split(r"[;+]", text) if p.strip()]
    inns = []
    for part in parts:
        base = _PAREN_RE.sub(" ", part)
        base = _NON_ALNUM_RE.sub(" ", base)
        base = _WS_RE.sub(" ", base).strip()
        if base and not is_missing(base):
            inns.append(base)
    return tuple(sorted(set(inns)))


def normalize_strength(value) -> tuple[str, ...]:
    """Reduce a strength string to canonical components.

        "500 mg"        -> ("500mg",)
        "1 g"           -> ("1000mg",)
        "80 mg; 480 mg" -> ("480mg", "80mg")
        "125 mg/5 mL"   -> ("125mg/5ml",)

    Grams convert to milligrams so "1 g" and "1000 mg" collide. Anything that
    does not parse returns empty rather than a guess.
    """
    text = clean(value)
    if text is None:
        return ()

    components = []
    for part in re.split(r"[;+]", text):
        part = part.strip().replace(" ", "")
        if not part:
            continue
        # concentration, e.g. 125mg/5ml - normalise the unit casing only
        conc = re.match(r"^([\d.]+)(mg|g|mcg|µg|ug|iu|ml)/([\d.]+)(ml|l|g)$", part)
        if conc:
            numerator, denominator = _num(conc.group(1)), _num(conc.group(3))
            if numerator is not None and denominator is not None:
                components.append(f"{numerator}{conc.group(2)}/"
                                  f"{denominator}{conc.group(4)}")
            continue
        simple = re.match(r"^([\d.]+)(mg|g|mcg|µg|ug|iu|ml|%)$", part)
        if simple:
            qty, unit = _num(simple.group(1)), simple.group(2)
            if qty is None:
                continue  # malformed number - drop out as unknown, never guess
            if unit == "g":
                qty = _num(float(qty) * 1000)
                unit = "mg"
            elif unit in {"µg", "ug"}:
                unit = "mcg"
            components.append(f"{qty}{unit}")
    return tuple(sorted(set(components)))


def _num(value) -> str | None:
    """Render a number without a trailing .0 so 500 and 500.0 collide.

    Returns None for anything that is not a clean number. Real register values
    include malformed strengths such as "0.1.". A trailing period that the
    digit pattern happily matches and float() then rejects. A strength that
    cannot be parsed has to drop out as unknown; raising would take down a whole
    register load over one bad row, and guessing would be worse.
    """
    try:
        f = float(str(value).strip().rstrip("."))
    except (TypeError, ValueError):
        return None
    return str(int(f)) if f == int(f) else str(f)


def normalize_form(value) -> str | None:
    """Collapse a dosage form to a coarse bucket, or None if unrecognised."""
    text = clean(value)
    if text is None:
        return None
    text = _NON_ALNUM_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if text in _FORM_LOOKUP:
        return _FORM_LOOKUP[text]
    # fall back to the longest known variant appearing as whole words
    best = None
    for variant, bucket in _FORM_LOOKUP.items():
        if re.search(rf"\b{re.escape(variant)}\b", text) and (
            best is None or len(variant) > best[0]
        ):
            best = (len(variant), bucket)
    return best[1] if best else None


def normalize_route(value) -> tuple[str, ...]:
    """Reduce a route string to a sorted tuple of routes."""
    text = clean(value)
    if text is None:
        return ()
    parts = [_WS_RE.sub(" ", _NON_ALNUM_RE.sub(" ", p)).strip()
             for p in re.split(r"[;/,]", text)]
    return tuple(sorted({p for p in parts if p and not is_missing(p)}))


def company_tokens(value) -> tuple[str, ...]:
    """Distinctive tokens of a company name, in original order.

    Legal forms and geographic qualifiers are stripped so that
    "Ranbaxy Nigeria Limited" and "Ranbaxy Laboratories" both reduce to a core
    beginning with "ranbaxy".

    This is deliberately token-based. Substring matching on company names is
    unsafe: searching for "sun pharma" inside applicant names matches
    "AniSUN PHARMAceutical Company Limited", inventing a corporate relationship
    that does not exist. Token equality does not make that mistake.
    """
    text = clean(value)
    if text is None:
        return ()
    text = _PAREN_RE.sub(" ", text)
    text = _NON_ALNUM_RE.sub(" ", text)
    tokens = [t for t in _WS_RE.sub(" ", text).strip().split()
              if t and t not in LEGAL_TOKENS and t not in GEO_TOKENS]
    return tuple(tokens)


def company_key(value) -> str | None:
    """A single comparable key for a company name, or None if unusable."""
    tokens = company_tokens(value)
    return " ".join(tokens) if tokens else None
