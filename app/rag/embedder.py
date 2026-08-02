"""Index chunks into a vector store and expose a retriever.

The retriever is built lazily on first use and cached, so importing this
module doesn't trigger network calls. Call `build_index()` explicitly at
app startup (FastAPI lifespan) if you want indexing to happen up front.
"""

from functools import lru_cache

from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings

from .chunker import chunk_documents
from .loader import load_documents


@lru_cache(maxsize=1)
def _get_retriever():
    docs = load_documents()
    doc_splits = chunk_documents(docs)
    vectorstore = InMemoryVectorStore.from_documents(
        documents=doc_splits,
        embedding=OpenAIEmbeddings(),
    )
    return vectorstore.as_retriever()


def get_retriever():
    """Return the cached retriever (builds the index on first call)."""
    return _get_retriever()


def build_index() -> None:
    """Eagerly build the index. Call from FastAPI lifespan/startup."""
    _get_retriever()


def reset_index() -> None:
    """Drop the cached index (e.g. if sources change). Next call rebuilds."""
    _get_retriever.cache_clear()
