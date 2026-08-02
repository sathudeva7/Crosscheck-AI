"""Split documents into chunks for indexing."""

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import CHUNK_OVERLAP, CHUNK_SIZE


def chunk_documents(docs: list[Document]) -> list[Document]:
    """Split documents into overlapping chunks sized by token count."""
    text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    return text_splitter.split_documents(docs)
