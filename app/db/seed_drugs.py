"""Load the drug reference tables. Run once:

    python -m app.db.seed_drugs

Downloads DDInter 2.0, maps its drug names to RxNorm ids, fetches the WHO
class of each, and loads `drug` and `interaction_rule`. Also folds in whatever
`data/drug_cache.json` already holds from ingestion.

The mapping step is the reason this exists as a script rather than a lookup at
request time. DDInter names its drugs its own way — 'Acetylsalicylic acid' for
aspirin, 'Acetaminophen' for paracetamol — so matching prescriptions to it by
name would miss warfarin + aspirin, the most consequential interaction there
is, and quietly report nothing found. Resolving all 1,971 names to ids once, up
front, turns every later check into an integer comparison.

Downloads and lookups are cached on disk, so a re-run is cheap and an
interrupted run can simply be repeated.

Nothing here touches patient data.
"""

import asyncio
import csv
import io
import json
import logging
import sys
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.config import PROJECT_ROOT, RXNAV_TIMEOUT
from app.db.models import Drug, DrugAlias, InteractionRule
from app.db.session import SessionLocal
from app.ingest.drugs import atc_for_rxcui, clean_name, rxcui_for_name

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("seed_drugs")

DDINTER_URL = "http://ddinter.scbdd.com/static/media/download/ddinter_downloads_code_{}.csv"
# DDInter splits its data by the first letter of the ATC code.
DDINTER_CODES = ("A", "B", "C", "D", "G", "H", "J", "L", "M", "N", "P", "R", "S", "V")

CACHE_DIR = PROJECT_ROOT / "data" / "ddinter"
NAME_MAP_PATH = PROJECT_ROOT / "data" / "ddinter_rxcui.json"
DRUG_CACHE_PATH = PROJECT_ROOT / "data" / "drug_cache.json"

# RxNav asks for no more than 20 requests a second; stay well under.
CONCURRENCY = 8

# Rows per insert. Passed as executemany parameters rather than built into one
# huge VALUES clause: 235,000 rows in 5,000-row statements is enough to drop
# the connection, and this form lets the driver pipeline them instead.
BATCH = 1_000


# --- download -----------------------------------------------------------------


def download() -> dict[str, str]:
    """Fetch the CSVs, keeping a copy so a re-run needs no network."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}

    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for code in DDINTER_CODES:
            path = CACHE_DIR / f"ddinter_{code}.csv"
            if path.exists() and path.stat().st_size > 0:
                files[code] = path.read_text(encoding="utf-8")
                continue

            logger.info("downloading DDInter %s ...", code)
            response = client.get(DDINTER_URL.format(code))
            response.raise_for_status()
            path.write_text(response.text, encoding="utf-8")
            files[code] = response.text

    return files


def read_pairs(files: dict[str, str]) -> tuple[dict[tuple[str, str], str], set[str]]:
    """Every interacting pair, and every drug name mentioned.

    Read with a real CSV parser: some names contain commas — 'Insulin (human,
    rapid acting)' — and splitting on commas mangles them.
    """
    pairs: dict[tuple[str, str], str] = {}
    names: set[str] = set()

    for text in files.values():
        for row in csv.DictReader(io.StringIO(text)):
            a, b = (row.get("Drug_A") or "").strip(), (row.get("Drug_B") or "").strip()
            severity = (row.get("Level") or "Unknown").strip() or "Unknown"
            if not a or not b:
                continue
            names.update((a, b))
            pairs[tuple(sorted((a.lower(), b.lower())))] = severity

    return pairs, names


# --- mapping names to ids -----------------------------------------------------


async def _lookup(client: httpx.AsyncClient, name: str, gate: asyncio.Semaphore) -> tuple[str, str | None]:
    async with gate:
        try:
            return name, await rxcui_for_name(client, name)
        except Exception:  # noqa: BLE001 - a miss is a normal outcome here
            return name, None


async def map_names(names: set[str]) -> dict[str, str]:
    """Resolve DDInter's drug names to RxNorm ids, remembering the answers."""
    known: dict[str, str | None] = {}
    if NAME_MAP_PATH.exists():
        known = json.loads(NAME_MAP_PATH.read_text())

    pending = sorted(name for name in names if name not in known)
    if pending:
        logger.info("resolving %s drug names against RxNorm ...", len(pending))
        gate = asyncio.Semaphore(CONCURRENCY)
        async with httpx.AsyncClient(timeout=RXNAV_TIMEOUT) as client:
            for start in range(0, len(pending), 200):
                chunk = pending[start : start + 200]
                for name, rxcui in await asyncio.gather(
                    *(_lookup(client, name, gate) for name in chunk)
                ):
                    known[name] = rxcui
                logger.info("  %s/%s", min(start + 200, len(pending)), len(pending))
                NAME_MAP_PATH.write_text(json.dumps(known, indent=0, sort_keys=True))

    resolved = {name: rxcui for name, rxcui in known.items() if rxcui}
    logger.info("mapped %s of %s names (%s could not be resolved)",
                len(resolved), len(known), len(known) - len(resolved))
    return resolved


async def fetch_classes(rxcuis: set[str]) -> dict[str, list[dict[str, str]]]:
    """The WHO classes for each ingredient — what duplicate detection compares."""
    logger.info("fetching ATC classes for %s ingredients ...", len(rxcuis))
    gate = asyncio.Semaphore(CONCURRENCY)
    out: dict[str, list[dict[str, str]]] = {}

    async def one(client: httpx.AsyncClient, rxcui: str) -> None:
        async with gate:
            try:
                out[rxcui] = await atc_for_rxcui(client, rxcui)
            except Exception:  # noqa: BLE001 - a drug with no class is still usable
                out[rxcui] = []

    ordered = sorted(rxcuis)
    async with httpx.AsyncClient(timeout=RXNAV_TIMEOUT) as client:
        for start in range(0, len(ordered), 200):
            await asyncio.gather(*(one(client, r) for r in ordered[start : start + 200]))
            logger.info("  %s/%s", min(start + 200, len(ordered)), len(ordered))

    return out


# --- loading ------------------------------------------------------------------


def load_drugs(
    mapping: dict[str, str], classes: dict[str, list[dict[str, str]]]
) -> int:
    """One row per ingredient. Later names for the same id simply update it."""
    rows: dict[str, dict] = {}
    for name, rxcui in mapping.items():
        atc = classes.get(rxcui) or []
        rows[rxcui] = {
            "rxcui": rxcui,
            "name": name.lower(),
            "atc_codes": [a["code"] for a in atc if a.get("code")],
            "atc_names": [a.get("name", "") for a in atc if a.get("code")],
            "source": "rxnorm",
        }

    statement = insert(Drug)
    statement = statement.on_conflict_do_update(
        index_elements=["rxcui"],
        set_={
            "atc_codes": statement.excluded.atc_codes,
            "atc_names": statement.excluded.atc_names,
        },
    )

    values = list(rows.values())
    with SessionLocal() as db:
        for start in range(0, len(values), BATCH):
            db.execute(statement, values[start : start + BATCH])
        db.commit()
    return len(rows)


def load_rules(pairs: dict[tuple[str, str], str], mapping: dict[str, str]) -> tuple[int, int]:
    """Load the interaction pairs, keyed on ids.

    Two different DDInter names can resolve to the same ingredient — a drug and
    its salt, say — which would produce a pair of a drug with itself. Those are
    dropped rather than stored as a self-interaction.
    """
    lower = {name.lower(): rxcui for name, rxcui in mapping.items()}
    rows: dict[tuple[str, str], dict] = {}
    unmapped = 0

    for (a, b), severity in pairs.items():
        rxcui_a, rxcui_b = lower.get(a), lower.get(b)
        if not rxcui_a or not rxcui_b:
            unmapped += 1
            continue
        if rxcui_a == rxcui_b:
            continue
        key = (rxcui_a, rxcui_b) if rxcui_a < rxcui_b else (rxcui_b, rxcui_a)
        rows[key] = {
            "rxcui_a": key[0],
            "rxcui_b": key[1],
            "severity": severity if severity in ("Major", "Moderate", "Minor") else "Unknown",
            "name_a": a if key[0] == rxcui_a else b,
            "name_b": b if key[0] == rxcui_a else a,
            "source": "ddinter",
        }

    statement = insert(InteractionRule)
    statement = statement.on_conflict_do_update(
        index_elements=["rxcui_a", "rxcui_b"],
        set_={"severity": statement.excluded.severity},
    )

    values = list(rows.values())
    with SessionLocal() as db:
        for start in range(0, len(values), BATCH):
            db.execute(statement, values[start : start + BATCH])
            if start and start % (BATCH * 25) == 0:
                db.commit()
                logger.info("  %s/%s rules", start, len(values))
        db.commit()

    return len(values), unmapped


# drugs.py names its routes for the pipeline's benefit; the tables record where
# an identity came from. 'cache' is not a source — it is a previous lookup.
_SOURCE_MAP = {
    "rxnorm_exact": "rxnorm",
    "model_verified": "model_verified",
    "cache": "model_verified",
    "user": "user",
}


def import_cache() -> int:
    """Fold the identities ingestion has already worked out into the tables.

    Unresolved names are recorded too: knowing that a name could not be
    identified is worth caching, since otherwise every upload pays for the same
    failed lookup.
    """
    if not DRUG_CACHE_PATH.exists():
        return 0

    cache = json.loads(DRUG_CACHE_PATH.read_text())
    added = 0

    with SessionLocal() as db:
        for alias, entry in cache.items():
            rxcui = (entry.get("rxcuis") or [None])[0]
            ingredients = entry.get("ingredients") or []
            source = _SOURCE_MAP.get(entry.get("source", ""), "model_verified")

            if rxcui and ingredients:
                atc = entry.get("atc") or []
                db.execute(
                    insert(Drug)
                    .values(
                        rxcui=rxcui,
                        name=ingredients[0],
                        atc_codes=[a["code"] for a in atc if a.get("code")],
                        atc_names=[a.get("name", "") for a in atc if a.get("code")],
                        source=source,
                    )
                    .on_conflict_do_nothing(index_elements=["rxcui"])
                )

            db.execute(
                insert(DrugAlias)
                .values(
                    alias=clean_name(alias).lower() or alias.lower(),
                    rxcui=rxcui if ingredients else None,
                    source=source,
                    confidence=entry.get("confidence"),
                    unresolved=not bool(rxcui and ingredients),
                    reason=entry.get("reason"),
                )
                .on_conflict_do_nothing(index_elements=["alias"])
            )
            added += 1
        db.commit()

    return added


def main() -> int:
    files = download()
    pairs, names = read_pairs(files)
    logger.info("DDInter: %s pairs across %s drug names", len(pairs), len(names))

    mapping = asyncio.run(map_names(names))
    classes = asyncio.run(fetch_classes(set(mapping.values())))

    drugs = load_drugs(mapping, classes)
    rules, unmapped = load_rules(pairs, mapping)
    aliases = import_cache()

    with SessionLocal() as db:
        logger.info(
            "\nloaded: %s drugs, %s interaction rules, %s aliases",
            db.scalar(select(func.count()).select_from(Drug)),
            db.scalar(select(func.count()).select_from(InteractionRule)),
            db.scalar(select(func.count()).select_from(DrugAlias)),
        )
    logger.info(
        "%s pairs skipped: at least one drug has no RxNorm id (%.1f%%)",
        unmapped,
        100 * unmapped / max(len(pairs), 1),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
