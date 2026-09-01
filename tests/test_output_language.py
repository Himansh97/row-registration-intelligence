"""The output linter. This is the second release gate.

Absence from a scraped source is not proof a product is unregistered. Every
source has bounded coverage, so a gap can only ever support a statement about
what was searched. Never a verdict about a company's regulatory standing.

These tests check the guard itself, then run it over every generated artifact in
the repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rri.language import assert_clean, check

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestGuardCatchesOverclaims:
    @pytest.mark.parametrize("text", [
        "Cipla is not registered in Tanzania.",
        "The product is not registered in Nigeria.",
        "This is an unregistered product.",
        "The company is non-compliant.",
        "The applicant is noncompliant with local rules.",
        "They failed to register the product.",
        "The holder has no registration in Zambia.",
        "Marketing it there would be illegal.",
    ])
    def test_forbidden_phrasing_is_caught(self, text):
        assert check(text), f"guard missed an overclaim: {text!r}"


class TestGuardAllowsSupportableStatements:
    @pytest.mark.parametrize("text", [
        "Not found in TMDA source, retrieved 2026-08-30.",
        "Did not appear in the NAFDAC Greenbook snapshot of 2026-08-30.",
        "No registration found in the TMDA published subset.",
        "This product-market pair has not been filed in the sources searched.",
        "Active registration in Nigeria: TZ 19 H 0248.",
        "Registered in Nigeria; absent from the Tanzanian source searched.",
    ])
    def test_supportable_phrasing_passes(self, text):
        assert not check(text), (
            f"guard rejected a supportable statement: {text!r} -> {check(text)}"
        )


class TestAssertClean:
    def test_raises_on_violation(self):
        with pytest.raises(ValueError, match="unsupportable claim"):
            assert_clean("Cipla is not registered in Tanzania.", "test report")

    def test_passes_on_clean_text(self):
        assert_clean("Not found in TMDA source, retrieved 2026-08-30.", "test report")

    def test_error_names_the_fix(self):
        with pytest.raises(ValueError) as exc:
            assert_clean("The product is not registered in Nigeria.")
        assert "not found in" in str(exc.value).lower()


class TestGeneratedArtifactsAreClean:
    """Run the guard over everything the project actually emits."""

    def _artifacts(self):
        out_dir = REPO_ROOT / "data" / "reports"
        if not out_dir.exists():
            return []
        return [p for p in out_dir.rglob("*")
                if p.suffix.lower() in {".md", ".txt", ".html", ".json", ".csv"}]

    def test_every_generated_report_is_clean(self):
        artifacts = self._artifacts()
        if not artifacts:
            pytest.skip("no generated reports yet")

        failures = []
        for path in artifacts:
            violations = check(path.read_text(encoding="utf-8", errors="ignore"))
            for violation in violations:
                failures.append(f"{path.relative_to(REPO_ROOT)}: {violation}")

        assert not failures, (
            f"{len(failures)} unsupportable claim(s) in generated output:\n  "
            + "\n  ".join(failures[:20])
        )
