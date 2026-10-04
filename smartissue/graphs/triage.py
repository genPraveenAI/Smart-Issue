from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from smartissue.agent import MODEL_ID, LocalKnowledgeBase
from smartissue.agents.intake import build_intake_agent
from smartissue.agents.issue_summary import build_issue_summary_agent
from smartissue.agents.resolution import build_resolution_agent
from smartissue.graphs.state import IssueState


def build_triage_graph(knowledge_base: LocalKnowledgeBase):
    graph = StateGraph(IssueState)
    graph.add_node("intake_agent", build_intake_agent())
    graph.add_node("resolution_agent", build_resolution_agent(knowledge_base))
    graph.add_node("issue_summary_agent", build_issue_summary_agent(knowledge_base))
    graph.add_edge(START, "intake_agent")
    graph.add_edge("intake_agent", "resolution_agent")
    graph.add_edge("resolution_agent", "issue_summary_agent")
    graph.add_edge("issue_summary_agent", END)
    return graph.compile()


def run_triage(
    graph: Any,
    title: str,
    description: str,
    *,
    draft_requested: bool = False,
    attempted_steps: list[str] | None = None,
    hosted_model_consent: bool = False,
    workflow: str = "",
    error_code: str = "",
    application_event_id: str = "",
) -> dict[str, Any]:
    return graph.invoke(
        {
            "title": title,
            "description": description,
            "attempted_steps": attempted_steps or [],
            "application": "Payments · Web",
            "workflow": workflow,
            "error_code": error_code,
            "application_event_id": application_event_id,
            "draft_requested": draft_requested,
            "hosted_model_consent": hosted_model_consent,
            "query": "",
            "matches": [],
            "candidate_count": 0,
            "context_tokens": 0,
            "model_id": MODEL_ID,
            "issue_draft": "",
            "draft_provider": "",
        }
    )