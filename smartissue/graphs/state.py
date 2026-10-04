from __future__ import annotations

from typing import Any, TypedDict


class IssueState(TypedDict, total=False):
    title: str
    description: str
    attempted_steps: list[str]
    application: str
    workflow: str
    error_code: str
    application_event_id: str
    draft_requested: bool
    hosted_model_consent: bool
    draft_provider: str
    query: str
    matches: list[dict[str, Any]]
    candidate_count: int
    context_tokens: int
    model_id: str
    issue_draft: str