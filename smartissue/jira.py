from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import quote, urlparse

import requests
from langgraph.graph import END, START, StateGraph


class JiraAgentState(TypedDict, total=False):
    associate_confirmed: bool
    associate_id: str
    title: str
    description: str
    report_id: str
    screenshot_path: Path | None
    screenshot_type: str | None
    diagnostic_log_path: Path | None
    application: str
    knowledge_ids: list[str]
    attempted_steps: list[str]
    diagnostics: dict[str, str]
    status: str
    result: dict[str, Any] | None


def jira_enabled() -> bool:
    return os.getenv("JIRA_ENABLED", "false").lower() == "true"


def jira_configured() -> bool:
    return jira_configuration_problem() is None


def jira_configuration_problem() -> str | None:
    if not jira_enabled():
        return "Jira is disabled. Set JIRA_ENABLED=true to create Jira issues."
    required = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT_KEY")
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        return f"Jira configuration is incomplete. Set: {', '.join(missing)}."
    base_url = os.getenv("JIRA_BASE_URL", "").strip().rstrip("/")
    parsed_base = urlparse(base_url)
    if parsed_base.scheme != "https" and parsed_base.hostname not in {"localhost", "127.0.0.1"}:
        return "JIRA_BASE_URL must use HTTPS."
    if parsed_base.hostname in {"atlassian.com", "www.atlassian.com", "home.atlassian.com", "id.atlassian.com"}:
        return "JIRA_BASE_URL must be your Jira site URL, such as https://your-site.atlassian.net, not the Atlassian account homepage."
    if parsed_base.path not in {"", "/"}:
        return "JIRA_BASE_URL must be the Jira site root, without a path."
    return None


def jira_issue_url(issue_key: str, *, base_url: str | None = None) -> str | None:
    if not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", issue_key):
        return None
    configured_base = (base_url or os.getenv("JIRA_BASE_URL", "")).strip().rstrip("/")
    if not configured_base:
        return None
    return f"{configured_base}/browse/{quote(issue_key, safe='')}"


def create_jira_issue(
    *,
    title: str,
    description: str,
    report_id: str,
    screenshot_path: Path | None,
    screenshot_type: str | None = None,
    diagnostic_log_path: Path | None = None,
    application: str = "Payments web application",
    knowledge_ids: list[str] | None = None,
    attempted_steps: list[str] | None = None,
    diagnostics: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    if not jira_enabled():
        return None
    configuration_problem = jira_configuration_problem()
    if configuration_problem:
        raise RuntimeError(configuration_problem)

    base_url = os.environ["JIRA_BASE_URL"].rstrip("/")
    auth = (os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])
    paragraphs = [
        f"Associate report: {report_id}",
        f"Associate confirmation: {diagnostics.get('associate_id', 'local-associate') if diagnostics else 'local-associate'}",
        f"Application: {application}",
        description,
        f"Support notes checked: {', '.join(knowledge_ids or []) or 'None'}",
        "Steps already tried: " + ("; ".join(attempted_steps or []) or "None recorded"),
        "Browser and app details: " + ("; ".join(f"{key}: {value}" for key, value in (diagnostics or {}).items()) or "Not provided"),
    ]
    payload = {
        "fields": {
            "project": {"key": os.environ["JIRA_PROJECT_KEY"]},
            "summary": title,
            "issuetype": {"name": "Bug"},
            "labels": ["associate-report"],
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": text}]}
                    for text in paragraphs
                ],
            },
        }
    }
    response = requests.post(
        f"{base_url}/rest/api/3/issue",
        auth=auth,
        json=payload,
        headers={"Accept": "application/json"},
        timeout=15,
    )
    if not response.ok:
        raise RuntimeError(f"Jira issue creation failed with HTTP {response.status_code}.")
    issue_key = response.json().get("key")
    if not issue_key:
        raise RuntimeError("Jira returned no issue key.")

    screenshot_attached = False
    log_attached = False
    warning = None
    attachments = []
    if screenshot_path and screenshot_path.is_file():
        attachments.append((screenshot_path, screenshot_type or "application/octet-stream", "screenshot"))
    if diagnostic_log_path and diagnostic_log_path.is_file():
        attachments.append((diagnostic_log_path, "text/plain", "diagnostic log"))

    for attachment_path, content_type, attachment_label in attachments:
        try:
            with attachment_path.open("rb") as image:
                attachment = requests.post(
                    f"{base_url}/rest/api/3/issue/{issue_key}/attachments",
                    auth=auth,
                    files={"file": (attachment_path.name, image, content_type)},
                    headers={"X-Atlassian-Token": "no-check", "Accept": "application/json"},
                    timeout=20,
                )
            if attachment_label == "screenshot":
                screenshot_attached = attachment.ok
            else:
                log_attached = attachment.ok
            if not attachment.ok:
                warning = f"Jira issue was created, but the {attachment_label} attachment failed."
        except requests.RequestException:
            warning = f"Jira issue was created, but the {attachment_label} attachment failed."

    return {
        "key": issue_key,
        "screenshot_attached": screenshot_attached,
        "diagnostic_log_attached": log_attached,
        "warning": warning,
    }


def build_jira_agent():
    def associate_confirmation_gate(state: JiraAgentState) -> dict[str, Any]:
        if not state.get("associate_confirmed") or not state.get("associate_id", "").strip():
            return {"status": "blocked", "result": None}
        return {"status": "associate_confirmed"}

    def create_issue(state: JiraAgentState) -> dict[str, Any]:
        result = create_jira_issue(
            title=state["title"],
            description=state["description"],
            report_id=state["report_id"],
            screenshot_path=state.get("screenshot_path"),
            screenshot_type=state.get("screenshot_type"),
            diagnostic_log_path=state.get("diagnostic_log_path"),
            application=state.get("application", "Payments web application"),
            knowledge_ids=state.get("knowledge_ids", []),
            attempted_steps=state.get("attempted_steps", []),
            diagnostics=state.get("diagnostics", {}),
        )
        return {"status": "created" if result else "jira_disabled", "result": result}

    def route_after_confirmation(state: JiraAgentState) -> str:
        return "create_issue" if state.get("status") == "associate_confirmed" else "blocked"

    graph = StateGraph(JiraAgentState)
    graph.add_node("associate_confirmation_gate", associate_confirmation_gate)
    graph.add_node("jira_issue_creator", create_issue)
    graph.add_edge(START, "associate_confirmation_gate")
    graph.add_conditional_edges(
        "associate_confirmation_gate",
        route_after_confirmation,
        {"create_issue": "jira_issue_creator", "blocked": END},
    )
    graph.add_edge("jira_issue_creator", END)
    return graph.compile()