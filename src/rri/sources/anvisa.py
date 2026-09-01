"""ANVISA (Brazil). The national medicines register.

Brazil publishes bulk open data, which makes it the cheapest large market to add
and the only one so far that also publishes regulated prices and real review
durations.

Four things about this source need care:

  encoding    ISO-8859-1, not UTF-8. Read as UTF-8 it mangles every accented
              company name, and company names are a matching key.
  expiry      "062036" is MMYYYY, not a date. Parsed as a date it silently
              yields the year 0620.
  holder      "05044984000126 - LEGRAND PHARMA ..." carries the CNPJ, Brazil's
              company registration number. That is a stable identifier and it
              makes entity resolution here exact rather than fuzzy.
  ingredient  Portuguese (DCB): "olanzapina", "vedolizumabe", "cloridrato de
              fluoxetina". Cross-market matching against English INN needs
              translation. Triggers do not (they are within-market) so this
              does not block the trigger feed.

Run:  python -m rri.sources.anvisa
"""

from __future__ import annotations

import csv
import io
import re

import httpx

from rri.normalize import clean, company_key, normalize_inn
from rri.products import ProductRecord, _identifier
from rri.provenance import REPO_ROOT, Snapshot
from rri.sources.base import RegisterAdapter
from rri.whitespace import SourceCoverage

DATA_URL = "https://dados.anvisa.gov.br/dados/DADOS_ABERTOS_MEDICAMENTOS.csv"
LANDING_URL = "https://dados.anvisa.gov.br/dados/"
ENCODING = "iso-8859-1"

# ANVISA serves only its leaf certificate and omits the Sectigo intermediate,
# so a strict TLS client cannot build a path to a trusted root. This bundle is
# certifi's roots plus that intermediate, fetched from the CA Issuers URI in
# the leaf's own Authority Information Access extension. Verification stays on.
CA_BUNDLE = str(REPO_ROOT / "config" / "certs" / "anvisa-bundle.pem")

# Conventional medicines. The register also carries DINAMIZADO (homeopathic),
# Fitoterápico (herbal) and BAIXO RISCO (low-risk/notified) categories, which
# follow different regulatory routes and mostly lack an active ingredient,
# 60% ingredient coverage across all active records, 99.9% once restricted to
# these categories.
CONVENTIONAL = {"Similar", "Genérico", "Novo", "Específico", "Biológico"}

ACTIVE = "Ativo"

# "05044984000126 - LEGRAND PHARMA INDÚSTRIA FARMACÊUTICA"
HOLDER_RE = re.compile(r"^\s*(?P<cnpj>\d{11,14})\s*-\s*(?P<name>.+?)\s*$")
EXPIRY_RE = re.compile(r"^(?P<month>\d{2})(?P<year>\d{4})$")


def parse_expiry(value) -> str | None:
    """"062036" -> "2036-06". Anything else -> None.

    The field is a month-year stamp with no separator. Returning None rather
    than a guess matters: a mis-parsed expiry becomes a renewal trigger on the
    wrong date, which is worse than no trigger at all.
    """
    text = (value or "").strip()
    match = EXPIRY_RE.fullmatch(text)
    if not match:
        return None
    month, year = int(match.group("month")), int(match.group("year"))
    if not (1 <= month <= 12 and 1990 <= year <= 2100):
        return None
    # Day-of-month is not published; the first is used so the value sorts and
    # compares as a date. Only year and month are ever displayed.
    return f"{year:04d}-{month:02d}"


def split_holder(value) -> tuple[str | None, str | None]:
    """Separate the CNPJ from the company name."""
    text = (value or "").strip()
    if not text:
        return None, None
    match = HOLDER_RE.match(text)
    if match:
        return match.group("cnpj"), match.group("name").strip()
    return None, text


class AnvisaAdapter(RegisterAdapter):
    source_id = "anvisa_medicamentos"
    country = "BR"
    authority = "ANVISA"
    landing_url = LANDING_URL
    verify = CA_BUNDLE

    def fetch(self) -> list[dict]:
        with httpx.Client(follow_redirects=True, timeout=300.0,
                          verify=self.verify) as client:
            response = client.get(DATA_URL)
            response.raise_for_status()
        text = response.content.decode(ENCODING, errors="replace")
        rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in rows]

    def to_records(self, raw: list[dict], source_ref: str) -> list[ProductRecord]:
        records: list[ProductRecord] = []
        for i, row in enumerate(raw):
            if row.get("CATEGORIA_REGULATORIA") not in CONVENTIONAL:
                continue

            cnpj, holder = split_holder(row.get("EMPRESA_DETENTORA_REGISTRO"))
            expiry = parse_expiry(row.get("DATA_VENCIMENTO_REGISTRO"))

            records.append(ProductRecord(
                source_id=self.source_id,
                country=self.country,
                product_name=(row.get("NOME_PRODUTO") or "").strip(),
                # Portuguese ingredient names, kept verbatim. Translation to
                # English INN is a separate, verified step - inventing it here
                # would bury a guess inside the canonical record.
                inn=normalize_inn(row.get("PRINCIPIO_ATIVO")),
                strength=(),   # not published in this dataset
                form=None,     # not published in this dataset
                route=(),
                atc=None,
                company_raw=holder,
                company=company_key(holder),
                registration_number=_identifier(row.get("NUMERO_REGISTRO_PRODUTO")),
                approval_date=_br_date(row.get("DATA_FINALIZACAO_PROCESSO")),
                status="Active" if row.get("SITUACAO_REGISTRO") == ACTIVE else "Inactive",
                category="Drugs",
                source_ref=f"{source_ref}#row={i}",
                extras={
                    "cnpj": cnpj,
                    "expiry": expiry,
                    "expiry_raw": row.get("DATA_VENCIMENTO_REGISTRO"),
                    "regulatory_category": row.get("CATEGORIA_REGULATORIA"),
                    "therapeutic_class": clean(row.get("CLASSE_TERAPEUTICA")),
                    "process_number": row.get("NUMERO_PROCESSO"),
                },
            ))
        return records

    def coverage(self, records: list[ProductRecord], snap: Snapshot) -> SourceCoverage:
        active = [r for r in records if r.is_active]
        with_expiry = sum(1 for r in active if r.extras.get("expiry"))
        # Coverage end is the latest registration the source KNOWS ABOUT, which
        # comes from approval dates. Deriving it from expiry dates yields 2058
        # and defeats the purpose: the field exists to stop this source being
        # compared against another that extends past what it can see.
        years = [str(r.approval_date)[:4] for r in active if r.approval_date]

        limitation = (
            f"Conventional medicines only ({', '.join(sorted(CONVENTIONAL))}); "
            f"homeopathic, herbal and low-risk categories excluded as they follow "
            f"different routes and largely lack an active ingredient. "
            f"{len(active):,} active of {len(records):,} conventional records; "
            f"expiry present for {with_expiry:,}. "
            f"Strength and dosage form are not published in this dataset, so "
            f"products are identified by ingredient alone."
        )
        return SourceCoverage(
            source_id=self.source_id,
            country=self.country,
            authority=self.authority,
            record_count=len(active),
            retrieved_date=snap.retrieved_date,
            limitation=limitation,
            coverage_end_year=max((int(y) for y in years), default=None),
        )


def _br_date(value) -> str | None:
    """"27/11/2001" -> "2001-11-27"."""
    text = (value or "").strip()
    match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", text)
    if not match:
        return None
    return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"


def main() -> int:
    adapter = AnvisaAdapter()
    print(f"Fetching {adapter.authority} from {DATA_URL}")
    snap = adapter.refresh()
    loaded = adapter.load()
    if loaded is None:
        return 1
    records, cov = loaded
    active = [r for r in records if r.is_active]
    print(f"\nconventional records  {len(records):,}")
    print(f"active                {len(active):,}")
    print(f"with ingredient       {sum(1 for r in active if r.inn):,}")
    print(f"with expiry           {sum(1 for r in active if r.extras.get('expiry')):,}")
    print(f"with CNPJ             {sum(1 for r in active if r.extras.get('cnpj')):,}")
    print(f"coverage end year     {cov.coverage_end_year}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
