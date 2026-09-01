"""Turn one conversation into a scope, end to end.

Run:  python -m rri.sales.scope notes.txt --company "Getz Pharma"
      cat notes.txt | python -m rri.sales.scope -
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rri.analyze import load_nafdac, load_tmda
from rri.sales import gaps as gapmod
from rri.sales.extract import RuleExtractor, default_extractor, extract
from rri.sales.ground import Corpus, build_lines, resolve_facts
from rri.sales.schema import Scope
from rri.sources.anvisa import AnvisaAdapter


def load_corpus() -> Corpus:
    """Every register currently ingested."""
    records = []
    coverage: dict[str, str] = {}
    retrieved: dict[str, str] = {}

    ng, ng_cov = load_nafdac()
    if ng_cov:
        records += ng
        coverage["NG"] = ng_cov.authority
        retrieved["NG"] = ng_cov.retrieved_date

    tz, tz_cov = load_tmda()
    if tz_cov:
        records += tz
        coverage["TZ"] = tz_cov.authority
        retrieved["TZ"] = tz_cov.retrieved_date

    loaded = AnvisaAdapter().load()
    if loaded:
        br, br_cov = loaded
        records += br
        coverage["BR"] = br_cov.authority
        retrieved["BR"] = br_cov.retrieved_date

    return Corpus(records=records, coverage=coverage, retrieved=retrieved)


def build_scope(text: str, corpus: Corpus, client_company: str | None = None,
                extractor=None) -> Scope:
    extractor = extractor or default_extractor(corpus.ingredients)
    facts, _rejected = extract(text, extractor)
    resolved = resolve_facts(facts, corpus)
    lines = build_lines(resolved, corpus, client_company)
    found = gapmod.detect(resolved, lines, client_company)

    return Scope(
        source_text=text,
        facts=facts,
        resolved=resolved,
        lines=lines,
        gaps=found,
        unresolved=[r for r in resolved if not r.is_resolved],
    )


def render(scope: Scope, client_company: str | None = None) -> str:
    """A scope sheet a rep can act on."""
    out: list[str] = []
    add = out.append

    add("# Scope reading")
    add("")
    add("Every line below points at words the client used. Nothing here is "
        "inferred from what they might have meant.")
    add("")

    if client_company:
        add(f"**Client entity:** {client_company}")
        add("")

    counts = scope.counts()
    add(f"Facts read: {counts['facts']}  |  resolved: {counts['resolved']}  |  "
        f"scope lines: {counts['lines']}  |  open questions: {counts['gaps']}")
    add("")

    if not scope.can_be_quoted:
        add("> **Not ready to quote.** High impact questions below are still open. "
            "Answering them changes what the work is, not just what it costs.")
        add("")

    held = gapmod.already_held(scope.lines)
    if held:
        add("## Already held")
        add("")
        add("These appear in the registers under this client already. Quoting to "
            "file them again in front of someone who knows their own portfolio is "
            "the fastest way to lose the room.")
        add("")
        for line in held:
            add(f"- **{line.product.title()}** in {line.market}. "
                f"{line.grounding.evidence}")
        add("")

    if scope.lines:
        add("## Scope lines")
        add("")
        add("| Product | Market | Service | What the registers show |")
        add("|---|---|---|---|")
        for line in scope.lines:
            evidence = line.grounding.evidence if line.grounding else "not checked"
            add(f"| {line.product.title()} | {line.market} | "
                f"{line.service.replace('_', ' ')} | {evidence} |")
        add("")
    else:
        add("## Scope lines")
        add("")
        add("None. The conversation did not establish a product, a market and a "
            "service together, which is the minimum needed to describe a unit of work.")
        add("")

    if scope.gaps:
        add("## Ask before quoting")
        add("")
        for impact in ("high", "medium", "low"):
            items = [g for g in scope.gaps if g.impact == impact]
            if not items:
                continue
            add(f"**{impact.title()} impact**")
            add("")
            for gap in items:
                add(f"- {gap.question}")
                add(f"  <br>{gap.why_it_matters}")
            add("")

    if scope.unresolved:
        add("## Not checked")
        add("")
        add("Mentioned in the call but outside what the register corpus can "
            "confirm. These are not conclusions, only limits on what was verified.")
        add("")
        for item in scope.unresolved:
            add(f"- **{item.fact.value}** ({item.fact.kind}). {item.note}")
        add("")

    add("## What the client said")
    add("")
    add("| Read as | Value | Their words |")
    add("|---|---|---|")
    for fact in scope.facts:
        add(f"| {fact.kind.replace('_', ' ')} | {fact.value} | "
            f"\"{fact.span.text}\" |")
    add("")

    text = "\n".join(out)

    from rri.language import assert_clean
    assert_clean(text, what="scope sheet")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("notes", help="path to a call note, or - for stdin")
    parser.add_argument("--company", default=None,
                        help="the client legal entity, so existing registrations "
                             "can be looked up")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    text = sys.stdin.read() if args.notes == "-" else Path(args.notes).read_text()
    if not text.strip():
        print("empty input", file=sys.stderr)
        return 1

    corpus = load_corpus()
    if not corpus.records:
        print("no registers ingested; run the ingest commands first", file=sys.stderr)
        return 1

    scope = build_scope(text, corpus, args.company)
    sheet = render(scope, args.company)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(sheet, encoding="utf-8")
        print(f"written {args.out}")
    else:
        print(sheet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
