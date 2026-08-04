"""Central config for the RAG agent. Tweak everything from here."""

import os

from dotenv import load_dotenv

# Load .env before any getenv below. This module is imported before the model
# clients are built, so OPENAI_API_KEY is in the environment by the time
# langchain-openai looks for it.
load_dotenv()

# Model used for agent decisions, rewriting, and answer generation
RESPONSE_MODEL = os.getenv("RAG_RESPONSE_MODEL", "openai:gpt-5.4-mini")
# Model used for grading retrieved docs (can be a cheaper model)
GRADER_MODEL = os.getenv("RAG_GRADER_MODEL", "openai:gpt-5.4-mini")

# --- web lookup ---------------------------------------------------------------
#
# Used by one tool only: explaining an interaction the drug database has already
# flagged. Absent a key that tool is not built at all, and the agent answers from
# the record as it always did — a missing key must degrade, never crash, because
# it is set on a laptop long before anyone remembers to set it on the host.
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# Enough for a consistent picture, few enough to stay inside the context. Kept
# to sources that publish drug monographs: this tool exists to put a known
# interaction into plain words, not to survey the open web on drug safety.
WEB_RESULTS = int(os.getenv("WEB_RESULTS", 4))

# Consumer and clinical drug references only. `nih.gov` was here and came out
# again: it matched PubMed Central, so a question about two of a patient's
# medicines returned cohort studies on unrelated cohorts. medlineplus.gov is
# NIH's own consumer library and is the part that was wanted.
WEB_DOMAINS = [
    "drugs.com",
    "medlineplus.gov",
    "nhs.uk",
    "mayoclinic.org",
    "medscape.com",
    "rxlist.com",
]

# Chunking
CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", 100))
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", 50))

# Source URLs to index at startup (replace with your own)
SOURCE_URLS = [
    "https://lilianweng.github.io/posts/2024-11-28-reward-hacking/",
    "https://lilianweng.github.io/posts/2024-07-07-hallucination/",
    "https://lilianweng.github.io/posts/2024-04-12-diffusion-video/",
]
