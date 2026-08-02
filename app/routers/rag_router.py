"""FastAPI router exposing the RAG agent.

Wire into your app with:

    from app.routers.rag_router import router as rag_router
    app.include_router(rag_router)
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.rag.graph import graph

router = APIRouter(prefix="/rag", tags=["rag"])


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str


@router.post("/ask", response_model=AskResponse)
async def ask(payload: AskRequest) -> AskResponse:
    """Run the agentic RAG graph and return the final answer."""
    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": payload.question}]}
    )
    return AskResponse(answer=result["messages"][-1].content)
