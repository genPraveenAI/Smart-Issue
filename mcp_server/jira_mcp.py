"""MCP server exposing Jira search and Jira-RCA knowledge sync. Run: python -m mcp_server.jira_mcp (stdio)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from smartissue.jira import search_jira_issues  # noqa: E402
from smartissue.jira_kb import build_article, sync_knowledge_from_jira  # noqa: E402
from smartissue.text import clean_text  # noqa: E402

mcp = FastMCP("smartissue-jira")


def _public_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": issue["key"],
        "summary": clean_text(issue["summary"], 160),
        "status": issue["status"],
        "resolved": issue["resolved"],
        "issue_type": issue["issue_type"],
        "description": clean_text(issue["description"], 1500),
        "has_root_cause_analysis": build_article(issue) is not None,
    }


@mcp.tool()
def search_jira(jql: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search Jira issues with JQL. Text is redacted of emails, phone and account numbers."""
    return [_public_issue(issue) for issue in search_jira_issues(jql, limit=limit)]


@mcp.tool()
def preview_rca_article(issue_key: str) -> dict[str, Any]:
    """Preview the knowledge-base article built from one issue's 'Root cause:' and 'Resolution:' text."""
    issues = search_jira_issues(f'key = "{issue_key}"', limit=1)
    if not issues:
        return {"error": "Issue not found."}
    return build_article(issues[0]) or {"error": "Not indexable: needs the approval label and 'Root cause:' plus 'Resolution:' text."}


@mcp.tool()
def sync_knowledge_base(limit: int = 50, jql: str = "") -> dict[str, Any]:
    """Rebuild .data/jira_knowledge.json from resolved Jira issues that contain root cause analysis."""
    return sync_knowledge_from_jira(limit=limit, jql=jql or None)


if __name__ == "__main__":
    mcp.run()
