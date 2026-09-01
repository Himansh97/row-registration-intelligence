"""Guard on the language this project is allowed to use about real companies.

The whole analysis rests on one distinction:

    supportable    "did not appear in the TMDA source retrieved 2026-08-30"
    NOT supportable "is not registered in Tanzania"

The first is a statement about a search. The second is a claim about a company's
regulatory status that no public source can support, because every source has
bounded coverage. Naming a real pharmaceutical company as unregistered, or
worse, non-compliant. In a market is defamatory if wrong, and it would be wrong
often.

That distinction is easy to hold while writing a function and easy to lose while
writing a sentence, so it is enforced mechanically rather than left to care.
`tests/test_output_language.py` runs this over every generated artifact and
fails the build on a violation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Phrases that assert a regulatory status rather than a search result.
# Each entry: (pattern, why it is forbidden, what to write instead).
FORBIDDEN = [
    (
        r"\b(?:is|are|was|were)\s+not\s+registered\b",
        "asserts a regulatory status the sources cannot support",
        'say "did not appear in <source>, retrieved <date>"',
    ),
    (
        r"\bnot\s+registered\s+in\b",
        "asserts a regulatory status the sources cannot support",
        'say "not found in <authority> source, retrieved <date>"',
    ),
    (
        r"\bunregistered\b",
        "asserts a regulatory status the sources cannot support",
        'describe the gap as "not found in <source>"',
    ),
    (
        r"\bnon-?compliant\b",
        "a compliance judgement about a real company",
        "state only what the public record literally says",
    ),
    (
        r"\bin\s+violation\b",
        "a compliance judgement about a real company",
        "state only what the public record literally says",
    ),
    (
        r"\bfail(?:s|ed|ing)?\s+to\s+(?:register|comply)\b",
        "imputes fault to a real company",
        "describe the gap neutrally as an unfiled product-market pair",
    ),
    (
        r"\bhas\s+no\s+registration\b",
        "asserts a regulatory status the sources cannot support",
        'say "no registration found in <source>"',
    ),
    (
        r"\billegal(?:ly)?\b",
        "a legal judgement about a real company",
        "remove it",
    ),
]

_COMPILED = [(re.compile(pattern, re.I), reason, fix)
             for pattern, reason, fix in FORBIDDEN]


@dataclass(frozen=True)
class Violation:
    line_no: int
    line: str
    matched: str
    reason: str
    fix: str

    def __str__(self) -> str:
        return (f"line {self.line_no}: {self.matched!r}, {self.reason}; "
                f"{self.fix}\n    {self.line.strip()[:100]}")


def check(text: str) -> list[Violation]:
    """Every forbidden phrase in a block of generated text."""
    violations = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern, reason, fix in _COMPILED:
            match = pattern.search(line)
            if match:
                violations.append(
                    Violation(line_no, line, match.group(0), reason, fix)
                )
    return violations


def assert_clean(text: str, what: str = "output") -> None:
    """Raise if generated text overclaims. Called by the report generator itself.

    The check runs at generation time as well as in the test suite, so a report
    written outside a test run cannot carry a claim the sources do not support.
    """
    violations = check(text)
    if violations:
        detail = "\n  ".join(str(v) for v in violations)
        raise ValueError(
            f"{what} contains {len(violations)} unsupportable claim(s):\n  {detail}"
        )
