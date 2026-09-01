"""Types for turning a sales conversation into a scope.

One rule shapes all of these. Every fact the system believes about a client has
to point at the words the client actually used. A scope line nobody asked for is
worse than a missing one, because it reaches a proposal and then the client asks
where it came from.

So a Fact without a verified Span does not exist. There is no low confidence
tier for unsupported facts, the same way an extraction without a supporting
quote is not recorded elsewhere in this project.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Span:
    """A verbatim stretch of the source text, located by offset.

    Offsets are held so a reader can be shown exactly where a fact came from,
    and so the quote can be re-checked against the source at any later point.
    """

    text: str
    start: int
    end: int

    def context(self, source: str, window: int = 60) -> str:
        """The span with surrounding words, for showing a rep why something was read."""
        left = max(0, self.start - window)
        right = min(len(source), self.end + window)
        prefix = "..." if left > 0 else ""
        suffix = "..." if right < len(source) else ""
        return f"{prefix}{source[left:right].strip()}{suffix}"


# What the system is willing to believe about a client from a conversation.
FACT_KINDS = (
    "product",       # a molecule, brand or product family
    "market",        # a country or region
    "service",       # registration, renewal, variation, labelling, PV
    "product_type",  # generic, biosimilar, new chemical entity, device
    "volume",        # how many products or SKUs
    "timing",        # a deadline or target date
    "constraint",    # budget, existing approvals, reference market
)


@dataclass(frozen=True)
class Fact:
    """Something the client said, with the words they said it in."""

    kind: str
    value: str
    span: Span
    confidence: float
    method: str  # how it was found, so a wrong reading can be traced

    def __post_init__(self) -> None:
        if self.kind not in FACT_KINDS:
            raise ValueError(f"unknown fact kind: {self.kind}")
        if not 0.0 < self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")


@dataclass(frozen=True)
class Resolved:
    """A fact matched onto something the register corpus knows about.

    `entity` is None when the fact could not be resolved. That is a reportable
    outcome, not a failure to hide: a market or molecule the corpus does not
    cover is exactly what a rep needs to know before quoting.
    """

    fact: Fact
    entity: str | None
    entity_kind: str | None
    confidence: float
    note: str = ""

    @property
    def is_resolved(self) -> bool:
        return self.entity is not None


@dataclass(frozen=True)
class Grounding:
    """What the registers say about one product in one market."""

    already_held: bool | None  # None when the corpus cannot answer
    holder_name: str | None
    competitors: int | None
    observed_timeline: str | None
    evidence: str


@dataclass
class ScopeLine:
    """One unit of work: a product, in a market, needing a service."""

    product: str
    market: str
    service: str
    said_by_client: list[Span] = field(default_factory=list)
    grounding: Grounding | None = None
    note: str = ""

    @property
    def is_supported(self) -> bool:
        """A line with no client span behind it should never leave the building."""
        return bool(self.said_by_client)


# Facts whose absence changes what the work costs. Ordered by how much.
@dataclass(frozen=True)
class Gap:
    """Something the client did not say that has to be known before quoting."""

    question: str
    why_it_matters: str
    impact: str  # "high" | "medium" | "low"
    kind: str

    IMPACT_ORDER = {"high": 0, "medium": 1, "low": 2}

    @property
    def rank(self) -> int:
        return self.IMPACT_ORDER.get(self.impact, 9)


@dataclass
class Scope:
    """The whole reading of one conversation."""

    source_text: str
    facts: list[Fact]
    resolved: list[Resolved]
    lines: list[ScopeLine]
    gaps: list[Gap]
    unresolved: list[Resolved] = field(default_factory=list)

    @property
    def can_be_quoted(self) -> bool:
        """False while a high impact question is still open.

        The tool is meant to be willing to say a scope is not priceable yet.
        """
        return not any(g.impact == "high" for g in self.gaps)

    def counts(self) -> dict[str, int]:
        return {
            "facts": len(self.facts),
            "resolved": sum(1 for r in self.resolved if r.is_resolved),
            "unresolved": len(self.unresolved),
            "lines": len(self.lines),
            "gaps": len(self.gaps),
        }
