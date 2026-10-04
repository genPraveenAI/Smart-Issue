from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from smartissue.agent import clean_text
from smartissue.graphs.state import IssueState


def build_intake_agent():
    def normalize_intake(state: IssueState) -> dict[str, Any]:
        title = clean_text(state.get("title", ""), 160)
        description = clean_text(state.get("description", ""), 1600)
        return {"title": title, "description": description, "query": f"{title}. {description}"[:900]}

    graph = StateGraph(IssueState)
    graph.add_node("normalize_and_redact", normalize_intake)
    graph.add_edge(START, "normalize_and_redact")
    graph.add_edge("normalize_and_redact", END)
    return graph.compile()