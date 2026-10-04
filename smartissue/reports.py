from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from requests import RequestException

from .agent import clean_text, redact_sensitive_text
from .evidence import process_evidence
from .jira import build_jira_agent, jira_configured, jira_configuration_problem, jira_enabled

ROOT = Path(__file__).resolve().parent.parent
REPORTS_PATH = ROOT / ".data" / "reports"
ALLOWED_IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
KNOWLEDGE_IDS = {item["id"] for item in json.loads((ROOT / "data" / "knowledge_base.json").read_text(encoding="utf-8"))}


def save_report(
    *,
    title: str,
    description: str,
    issue_draft: str,
    knowledge_ids: list[str],
    attempted_steps: list[str],
    associate_confirmed: bool,
    customer_id: str | None = None,
    application: str = "Payments · Web",
    screenshot: bytes | None = None,
    screenshot_type: str | None = None,
    console_log: bytes | None = None,
    diagnostic_context: dict[str, str] | None = None,
    diagnostics: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not associate_confirmed:
        raise ValueError("The associate must confirm this issue before submission.")
    title = clean_text(title, 160)
    description = clean_text(description, 1600)
    issue_draft = redact_sensitive_text(issue_draft).replace("\x00", "").strip()[:6000]
    if not title or not description or not issue_draft:
        raise ValueError("A title, description, and issue summary are required.")
    customer_id = clean_text(customer_id or "", 40)
    if customer_id and not re.fullmatch(r"CUST-[A-Z0-9-]{1,32}", customer_id):
        raise ValueError("Invalid customer reference.")
    application = clean_text(application, 80) or "Payments · Web"

    evidence = process_evidence(
        screenshot=screenshot,
        screenshot_mime=screenshot_type,
        console_log=console_log,
        diagnostic_context=diagnostic_context,
    )
    screenshot = evidence.get("sanitized_screenshot")
    screenshot_type = evidence.get("sanitized_screenshot_mime")
    sanitized_log = evidence.get("sanitized_log")
    extension = ALLOWED_IMAGE_TYPES.get(screenshot_type or "")

    report_id = f"IR-{uuid.uuid4().hex[:8].upper()}"
    report_directory = REPORTS_PATH / report_id
    report_directory.mkdir(parents=True, exist_ok=False)
    image_name = f"screen-capture.{extension}" if screenshot is not None else None
    image_path = report_directory / image_name if image_name else None
    if image_path and screenshot is not None:
        image_path.write_bytes(screenshot)
    log_name = "diagnostic-log.txt" if sanitized_log else None
    log_path = report_directory / log_name if log_name else None
    if log_path and sanitized_log:
        log_path.write_text(sanitized_log, encoding="utf-8")

    safe_diagnostics = {
        key: clean_text(value, 160)
        for key, value in (diagnostics or {}).items()
        if key in {
            "browser",
            "viewport",
            "captured_at",
            "application_version",
            "workflow",
            "error_code",
            "application_event_id",
            "associate_id",
        }
        and isinstance(value, str)
    }
    report: dict[str, Any] = {
        "report_id": report_id,
        "status": "awaiting_review",
        "created_at": datetime.now(UTC).isoformat(),
        "title": title,
        "description": description,
        "issue_draft": issue_draft,
        "customer_id": customer_id or None,
        "application": application,
        "knowledge_ids": sorted({item for item in knowledge_ids if item in KNOWLEDGE_IDS})[:5],
        "attempted_steps": [clean_text(step, 300) for step in attempted_steps[:10]],
        "diagnostics": safe_diagnostics,
        "screenshot_name": image_name,
        "diagnostic_log_name": log_name,
        "evidence_redactions": evidence.get("log_redactions", 0),
        "jira_key": None,
        "jira_warning": None,
    }

    persist_report(report)
    if not jira_configured():
        report["jira_warning"] = (
            f"{jira_configuration_problem()} The confirmed report was saved locally for review."
        )
        persist_report(report)
        return report

    try:
        jira_state = get_jira_agent().invoke(
            {
                "associate_confirmed": True,
                "associate_id": safe_diagnostics.get("associate_id", "local-associate"),
                "title": report["title"],
                "description": report["issue_draft"],
                "report_id": report_id,
                "screenshot_path": image_path,
                "screenshot_type": screenshot_type,
                "diagnostic_log_path": log_path,
                "application": report["application"],
                "knowledge_ids": report["knowledge_ids"],
                "attempted_steps": report["attempted_steps"],
                "diagnostics": report["diagnostics"],
            }
        )
        jira_result = jira_state.get("result")
        if jira_result:
            report["status"] = "jira_created"
            report["jira_key"] = jira_result["key"]
            report["jira_warning"] = jira_result["warning"]
        else:
            report["jira_warning"] = "Jira is not configured. The confirmed report was saved locally for review."
    except (OSError, RuntimeError, ValueError, RequestException):
        report["jira_warning"] = "The associate-confirmed report was saved locally, but Jira creation failed. Check Jira before any manual retry."
    persist_report(report)
    return report


def persist_report(report: dict[str, Any]) -> None:
    report_directory = REPORTS_PATH / report["report_id"]
    temporary_path = report_directory / "report.tmp"
    final_path = report_directory / "report.json"
    temporary_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temporary_path.replace(final_path)


def approve_report(report_id: str, reviewer: str) -> dict[str, Any]:
    if not re.fullmatch(r"IR-[A-F0-9]{8}", report_id):
        raise ValueError("Invalid report ID.")
    report_file = REPORTS_PATH / report_id / "report.json"
    if not report_file.is_file():
        raise ValueError("Report not found.")
    lock_file = report_file.parent / "jira.lock"
    owns_lock = False
    try:
        with lock_file.open("x", encoding="utf-8"):
            owns_lock = True
            report = json.loads(report_file.read_text(encoding="utf-8"))
            if report.get("status") != "awaiting_review":
                raise ValueError("Only reports awaiting review can be approved.")
            report["reviewer"] = clean_text(reviewer, 120) or "Local reviewer"
            report["reviewed_at"] = datetime.now(UTC).isoformat()
            report["status"] = "review_approved"
            persist_report(report)

            screenshot_name = report.get("screenshot_name")
            screenshot_path = report_file.parent / screenshot_name if screenshot_name else None
            log_name = report.get("diagnostic_log_name")
            log_path = report_file.parent / log_name if log_name else None
            screenshot_type = next(
                (mime for mime, extension in ALLOWED_IMAGE_TYPES.items() if screenshot_name and screenshot_name.endswith(f".{extension}")),
                None,
            )
            try:
                jira_state = get_jira_agent().invoke(
                    {
                        "associate_confirmed": True,
                        "associate_id": report["reviewer"],
                        "title": report["title"],
                        "description": report["issue_draft"],
                        "report_id": report_id,
                        "screenshot_path": screenshot_path,
                        "screenshot_type": screenshot_type,
                        "diagnostic_log_path": log_path,
                        "application": report["application"],
                        "knowledge_ids": report["knowledge_ids"],
                        "attempted_steps": report["attempted_steps"],
                        "diagnostics": report["diagnostics"],
                    }
                )
                jira_result = jira_state.get("result")
            except (OSError, RuntimeError, ValueError, RequestException):
                report["status"] = "review_approved"
                report["jira_warning"] = "Reviewer approval was recorded, but Jira creation failed. Check Jira before any manual retry to avoid duplicates."
                persist_report(report)
                return report

            if jira_result:
                report["status"] = "jira_created"
                report["jira_key"] = jira_result["key"]
                report["jira_warning"] = jira_result["warning"]
            else:
                report["status"] = "review_approved"
                report["jira_warning"] = "Jira is disabled; reviewer approval was recorded locally."
            persist_report(report)
            return report
    except FileExistsError as error:
        raise ValueError("Another reviewer is already processing this report.") from error
    finally:
        if owns_lock:
            lock_file.unlink(missing_ok=True)


@lru_cache(maxsize=1)
def get_jira_agent():
    return build_jira_agent()


def reject_report(report_id: str, reviewer: str, reason: str) -> dict[str, Any]:
    if not re.fullmatch(r"IR-[A-F0-9]{8}", report_id):
        raise ValueError("Invalid report ID.")
    report_file = REPORTS_PATH / report_id / "report.json"
    if not report_file.is_file():
        raise ValueError("Report not found.")
    report = json.loads(report_file.read_text(encoding="utf-8"))
    if report.get("status") != "awaiting_review":
        raise ValueError("Only reports awaiting review can be rejected.")
    report["status"] = "rejected"
    report["reviewer"] = clean_text(reviewer, 120) or "Local reviewer"
    report["reviewed_at"] = datetime.now(UTC).isoformat()
    report["review_reason"] = clean_text(reason, 500)
    persist_report(report)
    return report


def list_reports(limit: int = 100) -> list[dict[str, Any]]:
    if not REPORTS_PATH.exists():
        return []
    reports = []
    for report_file in REPORTS_PATH.glob("*/report.json"):
        try:
            reports.append(json.loads(report_file.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    reports.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return reports[:limit]