# CrossCheck

An AI medical report and prescription cross-checker. It reads a patient's own
documents — prescriptions, lab reports, discharge summaries, typed or
handwritten, in any language — merges them into one dated timeline, and
cross-checks that timeline for drug interactions, duplicate therapy,
conflicting doses and lab trends that no single document would reveal.

**Data decides, AI explains.** A language model never determines whether a
safety finding exists. That verdict comes from a drug database; the model's only
job is to say what it means in plain English.

> Informational cross-check — not a medical diagnosis.

## Stack

FastAPI · SQLAlchemy 2.0 · PostgreSQL (Supabase) · LangChain + LangGraph ·
OpenAI GPT-5.4 · React 19 + Vite · Clerk

Reference data: DDInter 2.0 (215,382 interaction rules), RxNorm, WHO ATC, openFDA.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env            # then fill in the four required keys
uvicorn app.main:app --reload   # migrations run at startup
```

```bash
cd frontend && npm install && npm run dev
```

Required in `.env`: `OPENAI_API_KEY`, `DATABASE_URL`, `CLERK_ISSUER`,
`CLERK_AUTHORIZED_PARTIES`. Optional: `SUPABASE_URL` + `SUPABASE_SERVICE_KEY`
(object storage — without them uploads go to the local disk, which a deploy
wipes), `TAVILY_API_KEY`, `LANGSMITH_API_KEY`.

## Layout

```
app/
├── ingest/     upload → parse → transcribe → extract → normalise → timeline
├── checks/     interactions, duplicates, allergies, doses, lab trends
├── rag/        the question-answering agent and its 11 tools
├── routers/    the HTTP API
└── db/         models and forward-only SQL migrations
frontend/       React app — timeline, interactions, trends, chat, review
evals/          test cases generated from the database, graded deterministically
docs/           technical summary
```

## Evals

```bash
python -m evals.run             # add --kind safety to run one family
```

Cases are generated from the database, so they cannot drift from the record, and
every grader is deterministic — never one model judging another.
