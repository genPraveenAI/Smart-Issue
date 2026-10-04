from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from smartissue.agent import LocalKnowledgeBase, build_issue_draft
from smartissue.graphs.state import IssueState


def build_issue_summary_agent(knowledge_base: LocalKnowledgeBase):
    def draft_report(state: IssueState) -> dict[str, Any]:
        if not state.get("draft_requested"):
            return {"issue_draft": "", "draft_provider": ""}
        issue_draft, provider = build_issue_draft(state, knowledge_base)
        return {"issue_draft": issue_draft, "draft_provider": provider}

    graph = StateGraph(IssueState)
    graph.add_node("fact_grounded_issue_draft", draft_report)
    graph.add_edge(START, "fact_grounded_issue_draft")
    graph.add_edge("fact_grounded_issue_draft", END)
    return graph.compile()