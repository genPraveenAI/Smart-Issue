from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import requests

from .jira import jira_configuration_problem, search_jira_issues
from .text import clean_text

ROOT = Path(__file__).resolve().parent.parent
JIRA_KNOWLEDGE_PATH = ROOT / ".data" / "jira_knowledge.json"
DEFAULT_JQL = "project = {project} AND statusCategory = Done{approval} ORDER BY resolutiondate DESC"
_RCA_LABEL = re.compile(r"^\s*(root cause(?: analysis)?|rca)\s*[:\-]\s*(.*)$", re.IGNORECASE)
_FIX_LABEL = re.compile(r"^\s*(resolution|fix|workaround|solution|steps?)\s*[:\-]\s*(.*)$", re.IGNORECASE)
_INLINE_HEADING = re.compile(r"(?<!\w)(resolution|workaround|solution)\s*:", re.IGNORECASE)


def approval_label() -> str:
    """Label a reviewer adds to approve an issue for the knowledge base; empty disables the gate."""
    return os.getenv("JIRA_KB_APPROVAL_LABEL", "kb-approved").strip()


def trusted_authors() -> set[str]:
    return {item.strip() for item in os.getenv("JIRA_KB_TRUSTED_AUTHORS", "").split(",") if item.strip()}


def _default_jql() -> str:
    label = approval_label()
    approval = f' AND labels = "{label}"' if label else ""
    return DEFAULT_JQL.format(project=os.environ.get("JIRA_PROJECT_KEY", ""), approval=approval)


def extract_rca(issue: dict[str, Any]) -> tuple[str, list[str]]:
    """Find 'Root cause:' and 'Resolution:/Fix:/Workaround:' text; with trusted authors set, only their comments count."""
    trusted = trusted_authors()
    comments = issue.get("comments", [])
    authors = issue.get("comment_authors", [])
    if trusted:
        candidates = [text for text, author in zip(comments, authors, strict=False) if author in trusted]
        candidates.reverse()
    else:
        candidates = [*reversed(comments), issue.get("description", "")]
    for text in candidates:
        text = _INLINE_HEADING.sub(r"\n\1:", text)
        cause: list[str] = []
        fix: list[str] = []
        current = None
        for line in text.splitlines():
            if match := _RCA_LABEL.match(line):
                current = cause
                line = match.group(2)
            elif match := _FIX_LABEL.match(line):
                current = fix
                line = match.group(2)
            line = re.sub(r"^\s*(?:[-*\u2022]|\d+[.)])\s*", "", line).strip()
            if current is not None and line:
                current.append(line)
        if cause and fix:
            return " ".join(cause), fix
    return "", []


def build_article(issue: dict[str, Any]) -> dict[str, Any] | None:
    label = approval_label()
    if label and label not in issue.get("labels", []):
        return None
    cause, steps = extract_rca(issue)
    if not cause or not steps or not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", issue.get("key", "")):
        return None
    return {
        "id": f"KB-JIRA-{issue['key']}",
        "title": clean_text(issue["summary"], 160),
        "summary": clean_text(f"Root cause: {cause}", 1200),
        "category": "Jira RCA \u00b7 approved" if label else "Jira RCA \u00b7 unreviewed",
        "updated": issue.get("resolved") or "unknown",
        "steps": [clean_text(step, 800) for step in steps[:10]],
    }


def sync_knowledge_from_jira(*, limit: int = 50, jql: str | None = None, path: Path = JIRA_KNOWLEDGE_PATH) -> dict[str, Any]:
    query = jql or os.getenv("JIRA_KB_JQL") or _default_jql()
    issues = search_jira_issues(query, limit=limit)
    articles = [article for issue in issues if (article := build_article(issue))]
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(articles, indent=2, ensure_ascii=False)
    changed = not path.is_file() or path.read_text(encoding="utf-8") != content
    if changed:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    return {
        "issues_scanned": len(issues),
        "articles_written": len(articles),
        "changed": changed,
        "article_ids": [article["id"] for article in articles],
        "skipped_without_rca": [issue["key"] for issue in issues if not build_article(issue)],
    }


_refresh_lock = threading.Lock()
_last_attempt = 0.0


def refresh_jira_knowledge_if_due() -> bool:
    """Sync at most every JIRA_KB_REFRESH_SECONDS; on any failure keep the last saved file."""
    global _last_attempt
    interval = float(os.getenv("JIRA_KB_REFRESH_SECONDS", "900"))
    with _refresh_lock:
        if time.monotonic() - _last_attempt < interval and _last_attempt:
            return False
        _last_attempt = time.monotonic()
    if jira_configuration_problem():
        return False
    try:
        return bool(sync_knowledge_from_jira()["changed"])
    except (RuntimeError, OSError, requests.RequestException, ValueError):
        return False


def knowledge_fingerprint(curated_path: Path = ROOT / "data" / "knowledge_base.json") -> str:
    digest = hashlib.sha256()
    for source in (curated_path, JIRA_KNOWLEDGE_PATH):
        if source.is_file():
            digest.update(source.read_bytes())
    return digest.hexdigest()
