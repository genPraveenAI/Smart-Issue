from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from smartissue.agent import CONTEXT_BUDGET, MODEL_ID, LocalKnowledgeBase
from smartissue.graphs.state import IssueState


def build_resolution_agent(knowledge_base: LocalKnowledgeBase):
    def retrieve_guidance(state: IssueState) -> dict[str, Any]:
        matches, tokens = knowledge_base.search(state.get("query", ""))
        return {"matches": matches, "candidate_count": len(matches), "context_tokens": tokens, "model_id": MODEL_ID}

    def optimize_context(state: IssueState) -> dict[str, Any]:
        selected = []
        used_tokens = 0
        for match in state.get("matches", []):
            token_count = int(match.get("token_count", 0))
            if used_tokens + token_count > CONTEXT_BUDGET:
                continue
            selected.append(match)
            used_tokens += token_count
        return {"matches": selected, "context_tokens": used_tokens}

    graph = StateGraph(IssueState)
    graph.add_node("semantic_retrieval", retrieve_guidance)
    graph.add_node("token_budget_optimizer", optimize_context)
    graph.add_edge(START, "semantic_retrieval")
    graph.add_edge("semantic_retrieval", "token_budget_optimizer")
    graph.add_edge("token_budget_optimizer", END)
    return graph.compile()