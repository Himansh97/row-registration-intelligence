"""The canonical product record both registers reduce to.

Each register speaks its own dialect. This module is where those dialects
collapse into one shape that can be compared, counted, and cited.

Every record keeps a `source_ref` pointing back at the snapshot and row it came
from, so any figure derived from it can be traced to retrieved bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rri.normalize import (
    clean,
    is_missing,
    company_key,
    normalize_form,
    normalize_inn,
    normalize_route,
    normalize_strength,
)

# Categories that belong in a medicines whitespace analysis. Medical devices
# follow an entirely different registration pathway, so including them would
# compare products against requirements that do not apply to them.
MEDICINE_CATEGORIES = {"drugs", "vaccines and biologics"}

def _identifier(value) -> str | None:
    """Normalise whitespace on an identifier while preserving its case."""
    import re
    if is_missing(value):
        return None
    return re.sub(r"\s+", " ", str(value).strip())



@dataclass(frozen=True)
class ProductRecord:
    """One product as registered in one country."""

    source_id: str
    country: str
    product_name: str
    inn: tuple[str, ...]
    strength: tuple[str, ...]
    form: str | None
    route: tuple[str, ...]
    atc: str | None
    company_raw: str | None
    company: str | None  # normalised company key
    registration_number: str | None
    approval_date: str | None
    status: str | None
    category: str | None
    source_ref: str
    extras: dict = field(default_factory=dict, compare=False)

    @property
    def is_active(self) -> bool:
        """Only Active registrations count as live coverage.

        A lapsed registration is not market access. Treating Inactive rows as
        coverage would understate the whitespace; ignoring status entirely would
        misstate it in both directions.
        """
        return (self.status or "").strip().lower() == "active"

    @property
    def is_medicine(self) -> bool:
        if self.category is None:
            return True  # registers without a category field are medicines-only
        return self.category.strip().lower() in MEDICINE_CATEGORIES

    @property
    def identity(self) -> tuple:
        """The key that decides whether two records are the same product.

        INN plus strength plus dosage form. Route is deliberately excluded: it
        is derivable from the form for most oral solids and is missing more
        often than it is informative.
        """
        return (self.inn, self.strength, self.form)

    @property
    def has_usable_identity(self) -> bool:
        """False when the record cannot be matched on without guessing.

        A record with no INN, or with an INN but neither strength nor form, is
        too thin to match safely. Such records are reported as unmatchable
        rather than being force-fitted to something that looks close.
        """
        if not self.inn:
            return False
        return bool(self.strength) or self.form is not None


def from_nafdac(row: dict, source_ref: str) -> ProductRecord:
    """Build a canonical record from one NAFDAC Greenbook row."""
    category = row.get("category_name")
    if not category and isinstance(row.get("product_category"), dict):
        category = row["product_category"].get("name")

    applicant = row.get("applicant_name")
    if not applicant and isinstance(row.get("applicant"), dict):
        applicant = row["applicant"].get("name")

    return ProductRecord(
        source_id="nafdac_greenbook",
        country="NG",
        product_name=(row.get("product_name") or "").replace("#", "").replace("*", "").strip(),
        inn=normalize_inn(row.get("ingredient_name")),
        strength=normalize_strength(row.get("strength")),
        form=normalize_form(row.get("form_name")),
        route=normalize_route(row.get("route_name")),
        atc=clean(row.get("atc")),
        company_raw=applicant,
        company=company_key(applicant),
        # Registration numbers are identifiers, not text: preserve their case.
        # `clean()` lowercases, which would render NAFDAC's "A4-6238" as
        # "a4-6238" - a number a reader could not match against the register.
        registration_number=_identifier(row.get("NAFDAC")),
        approval_date=row.get("approval_date"),
        status=row.get("status"),
        category=category,
        source_ref=f"{source_ref}#product_id={row.get('product_id')}",
        extras={"expiry_date": row.get("expiry_date"),
                # The register's own row identifier. Registration numbers are
                # NOT unique here - 263 of them are shared across 538 records,
                # including different products - so this is what makes
                # a record identifiable across snapshots.
                "record_id": row.get("product_id")},
    )


def from_tmda(row: dict, source_ref: str) -> ProductRecord:
    """Build a canonical record from one TMDA listing row plus its extraction.

    TMDA's listing has no dedicated ingredient, strength, or form fields. They
    are embedded in a free-text generic name such as
    "Desloratadine 5 mg film coated tablets". Company and registration number
    come from the linked SmPC and are attached by `extract_tmda`.
    """
    parsed = row.get("parsed", {})
    company_raw = row.get("marketing_authorisation_holder")

    return ProductRecord(
        source_id="tmda_approved_products",
        country="TZ",
        product_name=(row.get("product_name") or "").strip(),
        inn=parsed.get("inn", ()),
        strength=parsed.get("strength", ()),
        form=parsed.get("form"),
        route=parsed.get("route", ()),
        atc=None,  # TMDA does not publish ATC in this listing
        company_raw=company_raw,
        company=company_key(company_raw),
        registration_number=_identifier(row.get("registration_number")),
        approval_date=row.get("first_registration_date"),
        # The listing publishes currently-approved products; it carries no
        # status column. Treated as active, and that assumption is stated
        # wherever Tanzania coverage is reported.
        status="Active",
        category=None,
        source_ref=source_ref,
        extras={"generic_name": row.get("generic_name"),
                "document_url": row.get("document_url")},
    )


def parse_generic_name(text: str) -> dict:
    """Pull INN, strength, and dosage form out of a free-text generic name.

    TMDA writes the whole product description in one string:

        "Desloratadine 5 mg film coated tablets"
            -> inn=("desloratadine",) strength=("5mg",) form="tablet"

    Each component is extracted independently, and any component that cannot be
    read is left empty rather than inferred from the others.
    """
    import re

    if not text:
        return {"inn": (), "strength": (), "form": None, "route": ()}

    raw = text.strip()

    # Strength: every number-unit pair anywhere in the string.
    strength_parts = re.findall(
        r"(\d+(?:\.\d+)?)\s*(mg|g|mcg|µg|ug|iu|ml|%)\b", raw, re.I
    )
    strength = normalize_strength(
        "; ".join(f"{qty} {unit}" for qty, unit in strength_parts)
    ) if strength_parts else ()

    form = normalize_form(raw)

    # INN: whatever remains once strengths and form words are removed.
    #
    # The alternation below MUST stay inside a single non-capturing group with
    # the word boundaries wrapped around the whole thing. Written flat as
    # `\bfoo|bar|baz\b`, the boundaries bind only to the first and last
    # alternatives, and `gels?` then matches inside "gelatin" and leaves "atin"
    # welded to the ingredient name.
    stripped = re.sub(r"\d+(?:\.\d+)?\s*(mg|g|mcg|µg|ug|iu|ml|%)\b", " ", raw, flags=re.I)
    stripped = re.sub(
        r"\b(?:"
        r"film|sugar|enteric|coated|tablets?|capsules?|caplets?|injections?|"
        r"syrups?|suspensions?|solutions?|creams?|ointments?|gels?|drops?|"
        r"powders?|granules?|suppositor(?:y|ies)|dispersible|chewable|"
        r"effervescent|oral|for|and|with|prolonged|modified|release|hard|soft|"
        r"gelatin|infusion|sterile|vaginal|topical|ophthalmic|dry|bp|usp|ph\.?eur"
        r")\b",
        " ", stripped, flags=re.I,
    )
    inn = normalize_inn(re.sub(r"\s+", " ", stripped).strip(" -,/&"))

    return {"inn": inn, "strength": strength, "form": form, "route": ()}
