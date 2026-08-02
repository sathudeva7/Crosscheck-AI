"""Assemble the agentic RAG graph."""

from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from .nodes import (
    generate_answer,
    generate_query_or_respond,
    grade_documents,
    rewrite_question,
)
from .search import retriever_tool


def route_on_tool_calls(state: MessagesState):
    """If the model requested a tool call, go retrieve; otherwise finish."""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return END


def build_graph():
    workflow = StateGraph(MessagesState)

    workflow.add_node(generate_query_or_respond)
    workflow.add_node("retrieve", ToolNode([retriever_tool]))
    workflow.add_node(rewrite_question)
    workflow.add_node(generate_answer)

    workflow.add_edge(START, "generate_query_or_respond")
    workflow.add_conditional_edges(
        "generate_query_or_respond",
        route_on_tool_calls,
        {"tools": "retrieve", END: END},
    )
    workflow.add_conditional_edges("retrieve", grade_documents)
    workflow.add_edge("generate_answer", END)
    workflow.add_edge("rewrite_question", "generate_query_or_respond")

    return workflow.compile()


# Singleton graph instance — import this in your FastAPI routes
graph = build_graph()
