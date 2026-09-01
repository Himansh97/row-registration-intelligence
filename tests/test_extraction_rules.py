"""Tests for the SmPC/assessment-report extraction rules.

Every case here is a layout observed in the real TMDA corpus. The address cases
are regressions: the citation gate caught them in production data, not review.
"""

from rri.extract_tmda import extract_mah, extract_registration_number


def pages(*lines):
    """One page containing the given lines."""
    return ["\n".join(lines)]


class TestHeadingOnItsOwnLine:
    def test_value_on_the_following_line(self):
        e = extract_mah(pages("7. MARKETING AUTHORISATION HOLDER", "Cipla Limited"))
        assert e.value == "Cipla Limited"

    def test_address_below_the_name_is_not_taken(self):
        e = extract_mah(pages(
            "7. MARKETING AUTHORISATION HOLDER",
            "Shelys Pharmaceuticals Ltd",
            "Plot No. 696, New Bagamoyo Road",
        ))
        assert e.value == "Shelys Pharmaceuticals Ltd"


class TestTableLayoutRegression:
    """Assessment reports put the heading and value on one line, no colon.

    Reading past it to the next line lands on the holder's street address, which
    is how 'Vill: Nandpur, Teh: Baddi, Distt:Solan' was reported as a marketing
    authorisation holder.
    """

    def test_value_on_the_same_line_as_the_heading(self):
        e = extract_mah(pages(
            "Marketing Authorization Holder Beta Drugs Ltd",
            "Kharuni-Lodhimajra Road,",
            "Vill: Nandpur, Teh: Baddi,Distt:Solan,",
        ))
        assert e.value == "Beta Drugs Ltd"
        assert e.method == "rule:heading-inline"

    def test_indian_address_is_never_the_company(self):
        e = extract_mah(pages(
            "Marketing Authorization Holder Lincoln Pharmaceuticals Limited",
            "Trimul Estate,Khantraj,Taluka: Kalol,District:",
        ))
        assert e.value == "Lincoln Pharmaceuticals Limited"

    def test_boilerplate_tail_is_not_mistaken_for_a_value(self):
        # "and Manufacturing Site Addresses" must not become the company name.
        e = extract_mah(pages(
            "7. Marketing Authorization Holder and Manufacturing Site Addresses:",
            "Gelnova Laboratories (India) Pvt. Limited",
        ))
        assert e.value == "Gelnova Laboratories (India) Pvt. Limited"


class TestNoisyBlocks:
    def test_page_number_between_heading_and_value_is_skipped(self):
        e = extract_mah(["7. MARKETING AUTHORISATION HOLDER\n17", "Cipla Limited"])
        assert e.value == "Cipla Limited"

    def test_nested_subheading_is_skipped(self):
        e = extract_mah(pages(
            "2. Marketing Authorization Holder and Manufacturing Site Addresses",
            "1. Name and Address of Marketing Authorization Holder",
            "Lincoln Pharmaceuticals Limited",
        ))
        assert e.value == "Lincoln Pharmaceuticals Limited"

    def test_pharmacovigilance_boilerplate_is_not_a_heading(self):
        # "report ... to the marketing authorisation holder" appears mid-sentence
        # in every SmPC and must never be treated as the section heading.
        assert extract_mah(pages(
            "Healthcare providers are asked to report any suspected adverse",
            "reactions to the marketing authorisation holder or via the national",
            "reporting system (see details below).",
        )) is None


class TestEvidence:
    def test_every_extraction_carries_a_quote_containing_its_value(self):
        e = extract_mah(pages("Marketing Authorization Holder Beta Drugs Ltd"))
        assert e.value in e.quote
        assert e.page == 1
        assert 0 < e.confidence <= 1


class TestRegistrationNumber:
    def test_both_observed_formats(self):
        assert extract_registration_number(pages("Registration number(s) TZ 17 H 0290")).value == "TZ 17 H 0290"
        assert extract_registration_number(pages("8. TAN 21 HM 0143")).value == "TAN 21 HM 0143"

    def test_absent_returns_none_not_a_guess(self):
        assert extract_registration_number(pages("No number here at all")) is None
