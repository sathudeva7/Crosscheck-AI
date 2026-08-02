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

# Chunking
CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", 100))
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", 50))

# Source URLs to index at startup (replace with your own)
SOURCE_URLS = [
    "https://lilianweng.github.io/posts/2024-11-28-reward-hacking/",
    "https://lilianweng.github.io/posts/2024-07-07-hallucination/",
    "https://lilianweng.github.io/posts/2024-04-12-diffusion-video/",
]
