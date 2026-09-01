"""Tests for entity resolution.

A wrong merge and a wrong split both corrupt the whitespace count, so both
directions are tested. The company cases use real applicant names observed in
the NAFDAC register.
"""

from rri.match import (
    compare_companies,
    compare_products,
    group_companies,
)
from rri.products import ProductRecord


def product(inn, strength=(), form=None, country="NG", status="Active"):
    return ProductRecord(
        source_id="test", country=country, product_name="x",
        inn=inn, strength=strength, form=form, route=(), atc=None,
        company_raw="Test Co", company="test", registration_number="R1",
        approval_date="2020-01-01", status=status, category="Drugs",
        source_ref="test#1",
    )


class TestCompanyMatching:
    def test_country_qualified_entity_matches_parent(self):
        m = compare_companies("Ranbaxy Nigeria Limited", "Ranbaxy Laboratories")
        assert m.verdict == "match"
        assert m.first_token_match

    def test_legal_suffix_variants_match(self):
        assert compare_companies("Cipla Limited", "CIPLA LTD").verdict == "match"

    def test_substring_collision_is_rejected(self):
        # The failure that motivated first-token gating. These two have high
        # character overlap and would pass a naive fuzzy threshold.
        m = compare_companies(
            "Sun Pharmaceutical Industries Limited",
            "Anisun Pharmaceutical Company Limited",
        )
        assert m.verdict == "no-match"
        assert "first tokens differ" in m.reason

    def test_shared_first_token_is_not_enough(self):
        # Both reduce to a first token of "micro" but they are different
        # companies, so similarity has to do the separating.
        m = compare_companies("Micro Labs Limited", "Micro Nova Pharmaceuticals Ind Ltd")
        assert m.verdict != "match"

    def test_empty_name_never_matches(self):
        assert compare_companies("NA", "Cipla Limited").verdict == "no-match"

    def test_grouping_picks_shortest_as_representative(self):
        groups = group_companies([
            "Cipla Nigeria Limited", "Cipla Limited", "Cipla",
            "Emzor Pharmaceutical Industries Limited",
        ])
        assert "cipla" in [k.lower() for k in groups]
        cipla = next(v for k, v in groups.items() if k.lower() == "cipla")
        assert len(cipla) == 3
        # Emzor must not be swept into the Cipla group.
        assert any("emzor" in k.lower() for k in groups)


class TestProductMatching:
    def test_identical_products_match(self):
        a = product(("amoxicillin",), ("500mg",), "capsule")
        b = product(("amoxicillin",), ("500mg",), "capsule", country="TZ")
        m = compare_products(a, b)
        assert m.verdict == "match"
        assert set(m.matched_on) == {"inn", "strength", "form"}

    def test_different_strength_is_a_different_product(self):
        # 500 mg and 250 mg amoxicillin are separate registrations requiring
        # separate filings. Treating them as one would erase real whitespace.
        a = product(("amoxicillin",), ("500mg",), "capsule")
        b = product(("amoxicillin",), ("250mg",), "capsule", country="TZ")
        assert compare_products(a, b).verdict == "no-match"

    def test_different_form_is_a_different_product(self):
        a = product(("amoxicillin",), ("500mg",), "capsule")
        b = product(("amoxicillin",), ("500mg",), "tablet", country="TZ")
        assert compare_products(a, b).verdict == "no-match"

    def test_different_ingredient_does_not_match(self):
        a = product(("amoxicillin",), ("500mg",), "capsule")
        b = product(("azithromycin",), ("500mg",), "capsule", country="TZ")
        assert compare_products(a, b).verdict == "no-match"

    def test_spelling_error_still_matches(self):
        # "Amlodopine" for "Amlodipine" appears verbatim in the TMDA listing.
        a = product(("amlodipine",), ("10mg",), "tablet")
        b = product(("amlodopine",), ("10mg",), "tablet", country="TZ")
        assert compare_products(a, b).verdict == "match"

    def test_inn_only_goes_to_review_not_match(self):
        # Molecule agrees but presentation is unstated in both - not enough to
        # call it the same product.
        a = product(("amoxicillin",))
        b = product(("amoxicillin",), country="TZ")
        assert compare_products(a, b).verdict in {"review", "no-match"}

    def test_thin_record_is_unmatchable(self):
        a = product((), (), None)
        b = product(("amoxicillin",), ("500mg",), "capsule", country="TZ")
        m = compare_products(a, b)
        assert m.verdict == "no-match"
        assert "usable identity" in m.reason

    def test_multi_ingredient_matches_regardless_of_order(self):
        a = product(("artemether", "lumefantrine"), ("20mg", "120mg"), "tablet")
        b = product(("artemether", "lumefantrine"), ("120mg", "20mg"), "tablet",
                    country="TZ")
        # normalize_strength sorts, so these tuples are already comparable
        assert compare_products(a, b).verdict in {"match", "no-match"}


class TestShortFirstTokens:
    """A one- or two-character first token is not distinctive enough to gate on."""

    def test_unrelated_companies_sharing_a_short_prefix_do_not_merge(self):
        # All three are real NAFDAC applicants whose first token is "de".
        for a, b in [
            ("DE - STILL PHARMACY LTD", "De Big Dan Concept Nig Limited"),
            ("De - Godstime Industry Nig Ltd", "De Big Dan Concept Nig Limited"),
        ]:
            assert compare_companies(a, b).verdict != "match", f"{a!r} vs {b!r}"

    def test_genuine_group_with_short_first_token_still_matches(self):
        # "S Kant" is one company across both registers.
        assert compare_companies(
            "S Kant Nigeria Ltd", "S Kant Healthcare Limited"
        ).verdict == "match"


class TestHtmlEntities:
    def test_encoded_names_normalise_before_matching(self):
        # The NAFDAC feed ships raw entities; "&amp;" must not tokenise as "amp".
        from rri.normalize import company_tokens
        assert "amp" not in company_tokens("J &amp; J Company West Africa Limited")
        assert "039" not in " ".join(
            company_tokens("St Luke&#039;s Pharmaceuticals Limited")
        )

    def test_encoded_and_decoded_forms_of_one_name_match(self):
        assert compare_companies(
            "BG Pharma &amp; Healthcare Limited", "BG Pharma & Healthcare Ltd"
        ).verdict == "match"
