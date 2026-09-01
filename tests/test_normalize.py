"""Tests for canonical forms.

The cases here are not hypothetical. Every one is a real value observed in the
NAFDAC or TMDA data, including the company-name collision that made token-based
matching necessary in the first place.
"""

from rri.normalize import (
    company_key,
    company_tokens,
    is_missing,
    normalize_form,
    normalize_inn,
    normalize_route,
    normalize_strength,
)


class TestMissing:
    def test_register_placeholders_are_missing(self):
        # Registers write these to mean "no value". If any survives into the
        # matcher it becomes a false match.
        for placeholder in ["NA", "na", "N/A", "", "  ", "see Composition",
                            "-", "None", "not applicable"]:
            assert is_missing(placeholder), f"{placeholder!r} should be missing"

    def test_real_values_are_not_missing(self):
        for value in ["500 mg", "Tablet", "Amoxicillin", "Oral"]:
            assert not is_missing(value)


class TestINN:
    def test_single_ingredient(self):
        assert normalize_inn("Paracetamol") == ("paracetamol",)

    def test_salt_form_is_dropped(self):
        # The salt varies between registers for the same clinical product;
        # keeping it would split matches that should join.
        assert normalize_inn("Amlodipine (Amlodipine Besilate)") == ("amlodipine",)
        assert normalize_inn("Tramadol Hydrochloride") == ("tramadol hydrochloride",)

    def test_multi_ingredient_splits_and_sorts(self):
        assert normalize_inn("Artemether; Lumefantrine") == ("artemether", "lumefantrine")

    def test_ingredient_order_does_not_matter(self):
        assert normalize_inn("Lumefantrine; Artemether") == normalize_inn(
            "Artemether; Lumefantrine"
        )

    def test_multi_ingredient_with_salts(self):
        raw = "Amoxicillin (Amoxicillin Trihydrate); Clavulanic Acid (Potassium Clavulanate)"
        assert normalize_inn(raw) == ("amoxicillin", "clavulanic acid")

    def test_missing_returns_empty(self):
        assert normalize_inn("NA") == ()
        assert normalize_inn(None) == ()


class TestStrength:
    def test_simple(self):
        assert normalize_strength("500 mg") == ("500mg",)

    def test_grams_convert_to_milligrams(self):
        # "1 g" and "1000 mg" are the same strength and must collide.
        assert normalize_strength("1 g") == normalize_strength("1000 mg") == ("1000mg",)

    def test_multi_component_sorted(self):
        assert normalize_strength("80 mg; 480 mg") == ("480mg", "80mg")

    def test_concentration(self):
        assert normalize_strength("125 mg/5 mL") == ("125mg/5ml",)

    def test_unparseable_returns_empty_not_a_guess(self):
        assert normalize_strength("see Composition") == ()
        assert normalize_strength("NA") == ()


class TestForm:
    def test_adjectives_collapse_to_bucket(self):
        for raw in ["Tablet", "Film Coated Tablet", "Caplet", "Dispersible Tablet"]:
            assert normalize_form(raw) == "tablet", raw

    def test_injection_variants(self):
        for raw in ["Injection", "Powder for injection", "Solution for injection"]:
            assert normalize_form(raw) == "injection", raw

    def test_unknown_returns_none(self):
        assert normalize_form("NA") is None
        assert normalize_form("Nappy (Diaper) Pants") is None


class TestRoute:
    def test_single(self):
        assert normalize_route("Oral") == ("oral",)

    def test_multi_route_sorted(self):
        assert normalize_route("Intravenous; Intramuscular") == (
            "intramuscular", "intravenous",
        )

    def test_missing(self):
        assert normalize_route("NA") == ()


class TestCompany:
    def test_legal_suffixes_stripped(self):
        assert company_key("Emzor Pharmaceutical Industries Limited") == (
            "emzor pharmaceutical industries"
        )

    def test_country_qualifier_stripped_so_group_matches(self):
        # "Ranbaxy Nigeria Limited" is the local marketing entity of the same
        # corporate group as "Ranbaxy Laboratories".
        assert company_tokens("Ranbaxy Nigeria Limited")[0] == "ranbaxy"
        assert company_tokens("Ranbaxy Laboratories")[0] == "ranbaxy"

    def test_substring_collision_is_not_a_match(self):
        # The regression that motivated token-based matching: a substring search
        # for "sun pharma" matches "AniSUN PHARMAceutical", inventing a
        # corporate relationship. Token sets must not collide.
        sun = company_tokens("Sun Pharmaceutical Industries Limited")
        anisun = company_tokens("Anisun Pharmaceutical Company Limited")
        daily_sun = company_tokens("Daily Sun Pharmaceutical Company Ltd")

        assert sun[0] == "sun"
        assert anisun[0] == "anisun"
        assert sun[0] != anisun[0]
        # "Daily Sun" does contain the token "sun", so first-token comparison is
        # what separates it - not mere token membership.
        assert daily_sun[0] == "daily"

    def test_pharma_token_is_preserved(self):
        # Stripping "pharma" would collapse "Sun Pharmaceutical" to "sun".
        assert "pharmaceutical" in company_tokens("Sun Pharmaceutical Industries")

    def test_missing_company_returns_none(self):
        assert company_key("NA") is None
        assert company_key(None) is None


class TestGenericNameParsing:
    """TMDA embeds ingredient, strength, and form in one free-text field."""

    def test_basic_split(self):
        from rri.products import parse_generic_name
        r = parse_generic_name("Desloratadine 5 mg film coated tablets")
        assert r["inn"] == ("desloratadine",)
        assert r["strength"] == ("5mg",)
        assert r["form"] == "tablet"

    def test_gelatin_is_not_eaten_by_the_gel_pattern(self):
        # Regression: written as a flat alternation, the word boundaries bound
        # only to the first and last alternatives, so `gels?` matched inside
        # "gelatin" and left "atin" welded to the ingredient name.
        from rri.products import parse_generic_name
        r = parse_generic_name("Atomoxetine Hydrochloride Hard gelatin Capsule 40mg")
        assert r["inn"] == ("atomoxetine hydrochloride",)
        assert "atin" not in " ".join(r["inn"])

    def test_dry_powder_is_stripped(self):
        from rri.products import parse_generic_name
        r = parse_generic_name("Ceftriaxone Sodium Dry Powder for injection 500mg")
        assert r["inn"] == ("ceftriaxone sodium",)
        assert r["form"] == "injection"

    def test_multi_ingredient_with_ampersand(self):
        from rri.products import parse_generic_name
        r = parse_generic_name("Ceftriaxone Sodium & Sulbactam Dry Powder for injection")
        assert r["inn"] == ("ceftriaxone sodium sulbactam",)

    def test_empty_input_yields_nothing_not_a_guess(self):
        from rri.products import parse_generic_name
        r = parse_generic_name("")
        assert r["inn"] == () and r["strength"] == () and r["form"] is None


class TestMalformedStrengths:
    """Real registers contain values the pattern matches but float() rejects."""

    def test_trailing_period_does_not_raise(self):
        # Observed verbatim in the NAFDAC register: "0.1.". The digit pattern
        # matches it happily and float() then rejects it. One bad row must not
        # take down a whole register load.
        assert normalize_strength("0.1.") == ("0.1mg",) or \
               normalize_strength("0.1.") == ()

    def test_garbage_yields_empty_not_an_exception(self):
        for raw in ["...", "mg", "1..2 mg", "abc mg", "5..mg"]:
            normalize_strength(raw)  # must not raise

    def test_decimal_strengths_still_work(self):
        assert normalize_strength("2.5 mg") == ("2.5mg",)
        assert normalize_strength("0.5 g") == ("500mg",)
