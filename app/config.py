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

# --- object storage ----------------------------------------------------------
#
# Supabase Storage when these are set, the local disk when they are not. Not a
# preference: a container filesystem does not survive a redeploy, so on a host
# the originals are gone by the next deploy while the facts extracted from them
# stay in Postgres — a timeline citing page 1 of a document nobody can open
# again. Locally the disk is fine and needs no account, so both are supported
# and the environment decides.
#
# The service key bypasses every access rule in the project. It belongs on the
# server and must never be given a VITE_ name or reach the browser.
SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "documents")

# How long a link to a document stays good. Long enough to open and read a
# scan, short enough that a URL copied out of devtools is useless by the time
# it is pasted anywhere.
SIGNED_URL_TTL = int(os.getenv("SIGNED_URL_TTL", 300))

# What pages.py can actually open. Anything else is rejected at upload rather
# than failing later in the pipeline.
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
}

# --- page handling -----------------------------------------------------------

# --- model calls -------------------------------------------------------------
#
# How long to wait on one model call, and how many times to try again.
#
# Set because the defaults are not survivable. The OpenAI SDK waits 600 seconds
# for a read and retries twice, so a connection dropped mid-request — routine on
# a home line — parks a document in 'extracting' for up to half an hour with
# nothing on screen but a spinner. Measured on a real 19-page upload: 618
# seconds and still waiting, on a socket the network had already closed.
#
# A dropped connection almost always fails instantly on the retry, so a short
# timeout with more attempts recovers in seconds where the default recovers in
# minutes. The worst case drops from ~30 minutes to ~6.
MODEL_TIMEOUT = int(os.getenv("MODEL_TIMEOUT", 120))
MODEL_RETRIES = int(os.getenv("MODEL_RETRIES", 3))

# Vision gets longer: the request carries a rasterised page, so it is uploading
# a megabyte before the model starts, and a slow upstream link is not a fault.
VISION_TIMEOUT = int(os.getenv("VISION_TIMEOUT", 180))

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

# Reads a whole lab report and decides which kinds of doctor usually review one
# like it. Unlike EXPLAIN_MODEL this one does make the choice — but only from
# the specialties the live directories actually list, which it reaches through
# a search tool. See app/referral/report.py.
REPORT_MODEL = os.getenv("REPORT_MODEL", EXTRACT_MODEL)

# How many times the agent may search the directories before answering. Enough
# to try a clinical term, see what came back and refine it; low enough that a
# model looping on an unanswerable question still finishes an upload.
REPORT_MAX_SEARCHES = int(os.getenv("REPORT_MAX_SEARCHES", 6))

# Turn off to ingest lab reports without asking a model who should read them.
ENABLE_REPORT_RECOMMENDATION = os.getenv("ENABLE_REPORT_RECOMMENDATION", "1") not in (
    "0",
    "false",
    "False",
)

# Turn off to run ingestion with no network and no drug identification.
ENABLE_DRUG_LOOKUP = os.getenv("ENABLE_DRUG_LOOKUP", "1") not in ("0", "false", "False")

# --- interaction fallbacks ---------------------------------------------------

# The FDA's own label text, consulted only when DDInter has no rule for a pair.
# Free and needs no key; 240 requests a minute is far more than this uses.
# Answers with a quotable sentence rather than a verdict.
OPENFDA_BASE_URL = os.getenv("OPENFDA_BASE_URL", "https://api.fda.gov")
OPENFDA_TIMEOUT = float(os.getenv("OPENFDA_TIMEOUT", 15))
ENABLE_OPENFDA = os.getenv("ENABLE_OPENFDA", "1") not in ("0", "false", "False")

# --- finding a doctor --------------------------------------------------------
#
# Two directories answering two different questions, and neither alone is an
# answer. eChannelling knows *people* — which cardiologist channels where — and
# nothing about where that is on a map. Google knows *places* — the address, the
# phone, how far away it is — and in Sri Lanka lists no individual doctors at
# all. The feature is the join.
#
# Nothing either returns is ever stored. Google's terms require it (names,
# ratings and phone numbers must be fetched live, only a place_id may be kept),
# and it is the right shape anyway: a clinic that closed last week should
# disappear from the results, not persist in a table of ours.

# Google Maps Platform. One key, several APIs: Geocoding turns 'Kandy' into
# coordinates, Places finds the clinic and its details. Absent, the whole
# feature degrades to eChannelling names with no distance rather than failing.
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
GOOGLE_MAPS_TIMEOUT = float(os.getenv("GOOGLE_MAPS_TIMEOUT", 20))

# eChannelling's public API, behind Mobitel's IBM API Connect gateway.
ECHANNELLING_BASE_URL = os.getenv(
    "ECHANNELLING_BASE_URL",
    "https://echannelling-apigw.mobitel.lk/echannelling/ext/echannelling/ech-api/ech",
)
ECHANNELLING_TIMEOUT = float(os.getenv("ECHANNELLING_TIMEOUT", 40))

# The gateway wants an x-ibm-client-id. Theirs is published in their own web
# bundle, and `providers/echannelling.py` reads the current one from there at
# runtime rather than pinning a copy — pinned, it breaks silently the day they
# rotate it. Set this only to override with a client id of your own.
ECHANNELLING_CLIENT_ID = os.getenv("ECHANNELLING_CLIENT_ID")
ECHANNELLING_SITE_URL = os.getenv("ECHANNELLING_SITE_URL", "https://www.echannelling.com")

# Either directory can be switched off. Each degrades to the other, and with
# both off the endpoint reports that no directory is available rather than
# returning an empty list that reads as 'there are no doctors near you'.
# Doc990 (doc.lk), Dialog's channelling network — the other half of Sri Lankan
# private channelling. Server-rendered HTML, no key, no gateway. Worth asking
# separately rather than instead: eChannelling lists no cardiologist within
# 50 km of Vavuniya and Doc990 has one in the town, because the two networks
# sign different hospitals.
DOC990_BASE_URL = os.getenv("DOC990_BASE_URL", "https://www.doc.lk")
DOC990_TIMEOUT = float(os.getenv("DOC990_TIMEOUT", 60))

# How many hospitals near the patient to ask Doc990 about. Its search cannot
# filter by location, so each one costs a request and a 3 MB page; six covers a
# city without turning one search into a crawl.
DOC990_MAX_HOSPITALS = int(os.getenv("DOC990_MAX_HOSPITALS", 6))

# Any directory can be switched off. Each degrades independently, and with all
# of them off the endpoints report that no directory is available rather than
# returning an empty list that reads as 'there are no doctors near you'.
ENABLE_ECHANNELLING = os.getenv("ENABLE_ECHANNELLING", "1") not in ("0", "false", "False")
ENABLE_DOC990 = os.getenv("ENABLE_DOC990", "1") not in ("0", "false", "False")
ENABLE_PLACES = os.getenv("ENABLE_PLACES", "1") not in ("0", "false", "False")

# The widening ladder. A search that finds nothing nearby is not finished: for
# a patient in Jaffna needing an oncologist the nearest is 245 km away, and
# 'no results' would be both true and useless. Each radius is tried in turn
# until enough are found, and the response reports every rung tried so the
# interface can say how far it looked.
REFERRAL_RADII_KM = [
    int(r) for r in os.getenv("REFERRAL_RADII_KM", "5,10,25,50,150,400").split(",") if r.strip()
]
REFERRAL_ENOUGH = int(os.getenv("REFERRAL_ENOUGH", 3))
REFERRAL_MAX_RESULTS = int(os.getenv("REFERRAL_MAX_RESULTS", 20))

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
