"""Fetch and load raw documents from the web."""

import bs4
import requests
from langchain_core.documents import Document

from .config import SOURCE_URLS


def load_web_page(url: str, bs_kwargs: dict | None = None) -> list[Document]:
    """Fetch a single web page and return it as a Document."""
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    soup = bs4.BeautifulSoup(response.text, "html.parser", **(bs_kwargs or {}))
    return [Document(page_content=soup.get_text(), metadata={"source": url})]


def load_documents(urls: list[str] | None = None) -> list[Document]:
    """Load all source documents. Defaults to SOURCE_URLS from config."""
    urls = urls or SOURCE_URLS
    docs: list[Document] = []
    for url in urls:
        docs.extend(load_web_page(url))
    return docs
