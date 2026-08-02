# Agentic RAG (LangGraph) — FastAPI-ready

Modular version of the LangGraph agentic RAG tutorial, split into copy-paste-able files.

## Structure

```
app/
├── main.py                 # Example FastAPI entrypoint (lifespan builds index)
├── rag/
│   ├── config.py           # Models, chunk sizes, source URLs (env-overridable)
│   ├── loader.py           # Fetch web pages -> Documents
│   ├── chunker.py          # RecursiveCharacterTextSplitter chunking
│   ├── embedder.py         # Vector store + cached retriever (build/reset index)
│   ├── search.py           # @tool retriever the agent calls
│   ├── nodes.py            # generate_query_or_respond / grade / rewrite / answer
│   └── graph.py            # StateGraph assembly, exports `graph`
└── routers/
    └── rag_router.py       # POST /rag/ask
```

## Flow

`generate_query_or_respond` → (tool call?) → `retrieve` → `grade_documents` →
`generate_answer` (relevant) or `rewrite_question` → loop back.

## Run

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
uvicorn app.main:app --reload
```

```bash
curl -X POST http://localhost:8000/rag/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What does Lilian Weng say about types of reward hacking?"}'
```

## Adding to an existing FastAPI server

1. Copy `app/rag/` and `app/routers/rag_router.py` into your project.
2. In your existing `main.py`:
   ```python
   from app.rag.embedder import build_index
   from app.routers.rag_router import router as rag_router

   app.include_router(rag_router)
   # call build_index() in your startup/lifespan
   ```
3. Adjust the import paths if your package isn't named `app`.

## Customizing

- Change `SOURCE_URLS`, chunk sizes, and models in `config.py` (or via env vars).
- Swap `InMemoryVectorStore` in `embedder.py` for Chroma/Pinecone/pgvector for
  persistence — only that file needs to change.
- Rename `retrieve_blog_posts` in `search.py` and update its docstring so the
  agent knows what your corpus contains (the docstring is the tool description
  the LLM sees — it matters).
