"""App-wide config. RAG-specific settings live in app/rag/config.py."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/rag_agent"
)

# Small pool on purpose — Supabase free tier has tight connection limits
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", 5))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", 2))

# --- uploads -----------------------------------------------------------------

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", PROJECT_ROOT / "uploads"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", 25))

# What pages.py can actually open. Anything else is rejected at upload rather
# than failing later in the pipeline.
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
}

# --- page handling -----------------------------------------------------------

# Rasterisation DPI for the vision model. Rendering too low is the single most
# common cause of bad reads; 200-300 is the useful band.
PAGE_RENDER_DPI = int(os.getenv("PAGE_RENDER_DPI", 220))

# Refuse absurd inputs before they consume the API budget
MAX_PAGES = int(os.getenv("MAX_PAGES", 50))

# --- ingestion models --------------------------------------------------------

VISION_MODEL = os.getenv("VISION_MODEL", "openai:gpt-5.4")

# Pages are transcribed concurrently; this caps in-flight vision calls so a
# long document does not trip provider rate limits.
MAX_CONCURRENT_VISION = int(os.getenv("MAX_CONCURRENT_VISION", 4))

# Decides what kind of document each page is. Text-only and an easy judgement,
# so a small model is enough — and most pages never reach it, because keyword
# markers settle them first.
CLASSIFY_MODEL = os.getenv("CLASSIFY_MODEL", "openai:gpt-5.4-mini")

# Fills in the extraction forms from the transcribed text. The most accuracy-
# sensitive call in the pipeline — a missed drug or a misread dose is a real
# safety problem — so it does not share the classifier's small model.
EXTRACT_MODEL = os.getenv("EXTRACT_MODEL", "openai:gpt-5.4")

# Groups are extracted concurrently; caps in-flight calls, as with vision.
MAX_CONCURRENT_EXTRACT = int(os.getenv("MAX_CONCURRENT_EXTRACT", 4))

# --- normalisation -----------------------------------------------------------

# How to read a date whose order cannot be worked out from the numbers
# themselves: 04-07-2019 is 4 July outside the US and 7 April inside it.
# Applies only when the day is 12 or less; anything higher settles itself.
DATE_ORDER = os.getenv("DATE_ORDER", "DMY")

# --- drug identification -----------------------------------------------------

# RxNorm and the ATC classification, from the US National Library of Medicine.
# Free, no key. Used to verify drug names and to get their WHO drug class.
RXNAV_BASE_URL = os.getenv("RXNAV_BASE_URL", "https://rxnav.nlm.nih.gov/REST")
RXNAV_TIMEOUT = float(os.getenv("RXNAV_TIMEOUT", 20))
# NLM asks for no more than 20 requests a second.
MAX_CONCURRENT_RXNAV = int(os.getenv("MAX_CONCURRENT_RXNAV", 8))

# Names a brand's active ingredient when RxNorm cannot — RxNorm is US-centric
# and has no ingredient link for Betaloc, Voltaren or Losec. Whatever it says
# is then checked against RxNorm, so a model that invents a drug is caught.
DRUG_MODEL = os.getenv("DRUG_MODEL", EXTRACT_MODEL)

# Answers are cached here so a name is looked up once, ever. Migration 004
# loads this same file into the drug table; nothing written now is wasted.
DRUG_CACHE_PATH = Path(os.getenv("DRUG_CACHE_PATH", PROJECT_ROOT / "data" / "drug_cache.json"))

# Writes the plain-language wording for a finding that has already been
# established from the drug database. Never decides whether a finding exists.
EXPLAIN_MODEL = os.getenv("EXPLAIN_MODEL", EXTRACT_MODEL)

# Turn off to run ingestion with no network and no drug identification.
ENABLE_DRUG_LOOKUP = os.getenv("ENABLE_DRUG_LOOKUP", "1") not in ("0", "false", "False")

# --- interaction fallbacks ---------------------------------------------------

# The FDA's own label text, consulted only when DDInter has no rule for a pair.
# Free and needs no key; 240 requests a minute is far more than this uses.
# Answers with a quotable sentence rather than a verdict.
OPENFDA_BASE_URL = os.getenv("OPENFDA_BASE_URL", "https://api.fda.gov")
OPENFDA_TIMEOUT = float(os.getenv("OPENFDA_TIMEOUT", 15))
ENABLE_OPENFDA = os.getenv("ENABLE_OPENFDA", "1") not in ("0", "false", "False")

# --- answering questions -------------------------------------------------------

# How long one question may take before the caller is told plainly that it
# failed. There is a browser waiting; an unexplained hang is worse than a
# stated timeout.
QA_TIMEOUT_SECONDS = float(os.getenv("QA_TIMEOUT_SECONDS", 120))

# --- authentication ----------------------------------------------------------

# Clerk issues the session token; we verify it against Clerk's public keys.
# Copy this from the Clerk dashboard: API keys -> Show JWT public key -> Issuer.
# Looks like https://your-app-42.clerk.accounts.dev (no trailing slash).
#
# Leaving it empty keeps the demo stub in app/auth.py alive, so the API still
# runs locally with no Clerk account. Set it and every request needs a real
# signed token.
CLERK_ISSUER = os.getenv("CLERK_ISSUER", "").rstrip("/")

# Clerk puts the calling origin in the token's `azp` claim. Pinning it means a
# token minted for your app cannot be replayed from someone else's page.
# Comma-separated; empty disables the check.
CLERK_AUTHORIZED_PARTIES = [
    party.strip()
    for party in os.getenv("CLERK_AUTHORIZED_PARTIES", "").split(",")
    if party.strip()
]

# Clerk session tokens are short-lived (60s by default, refreshed silently by
# the frontend SDK), so a small allowance for clock drift matters more here
# than it would with hour-long tokens.
CLERK_CLOCK_SKEW_SECONDS = int(os.getenv("CLERK_CLOCK_SKEW_SECONDS", 30))

# --- browser access ----------------------------------------------------------

# The frontend runs on a different origin to the API, so without this the
# browser blocks every call. Comma-separated exact origins — a wildcard would
# let any page on the internet call this API with a token it managed to obtain.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]
