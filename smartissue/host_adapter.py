from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from dataclasses import replace
from typing import Any, Literal
from uuid import uuid4

Workflow = Literal[
    "Card payment",
    "Bank transfer",
    "Customer session",
    "Update customer demographics",
    "Open a new account",
]
MAX_HOST_DIAGNOSTIC_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ApplicationErrorEvent:
    event_id: str
    workflow: Workflow
    error_code: str
    title: str
    description: str
    application: str
    application_version: str
    occurred_at: str
    source: str = "payments-web"
    diagnostic_log: bytes | None = None

    def to_dict(self) -> dict[str, str]:
        payload = asdict(self)
        payload.pop("diagnostic_log")
        return payload


class ApprovedHostAppConnector:
    """In-process adapter for diagnostic data from a trusted host application."""

    def __init__(self, *, application: str, source: str) -> None:
        if not application.strip() or not source.strip():
            raise ValueError("An approved application and source are required.")
        self.application = application
        self.source = source

    def attach_diagnostic_log(self, event: ApplicationErrorEvent, diagnostic_log: bytes) -> ApplicationErrorEvent:
        if event.application != self.application or event.source != self.source:
            raise ValueError("The event did not come from this approved host application.")
        if not diagnostic_log or len(diagnostic_log) > MAX_HOST_DIAGNOSTIC_BYTES:
            raise ValueError("Host diagnostic log must be smaller than 1 MB.")
        try:
            decoded = diagnostic_log.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Host diagnostic log must be UTF-8 text.") from error
        if "\x00" in decoded:
            raise ValueError("Host diagnostic log must be plain text.")
        return replace(event, diagnostic_log=diagnostic_log)


DEMO_ERRORS: dict[Workflow, tuple[str, str, str]] = {
    "Card payment": (
        "PAYMENT_AUTH_PENDING",
        "Card payment declined after verification",
        "The payment action returned a decline after verification. A pending authorisation is still visible and retrying returns the same error.",
    ),
    "Bank transfer": (
        "TRANSFER_STATUS_DELAYED",
        "Bank transfer remains pending",
        "The transfer status remains pending beyond its expected processing window. No completion confirmation is available.",
    ),
    "Customer session": (
        "CUSTOMER_SESSION_TIMEOUT",
        "Customer session timed out during review",
        "The customer workspace session timed out while the associate was reviewing the request. Reopening the session returns the same timeout.",
    ),
    "Update customer demographics": (
        "CUSTOMER_PROFILE_UPDATE_FAILED",
        "Customer profile update could not be confirmed",
        "The customer profile update returned a validation error and no saved confirmation was received. Review the verified profile values before retrying.",
    ),
    "Open a new account": (
        "ACCOUNT_APPLICATION_PENDING",
        "Account application is awaiting confirmation",
        "The account application did not return a confirmed account reference. Check the customer application status before attempting another submission.",
    ),
}


def make_demo_error_event(
    workflow: Workflow,
    application_version: str = "payments-web-demo",
    operation_context: str = "",
    *,
    application: str = "Payments · Web",
    source: str = "payments-web-demo",
    customer_id: str | None = None,
) -> ApplicationErrorEvent:
    error_code, title, description = DEMO_ERRORS[workflow]
    context = [operation_context.strip()] if operation_context.strip() else []
    if customer_id:
        context.append(f"Customer reference: {customer_id}.")
    if context:
        description = f"{description} {' '.join(context)}"
    event = ApplicationErrorEvent(
        event_id=f"EVT-{uuid4().hex[:10].upper()}",
        workflow=workflow,
        error_code=error_code,
        title=title,
        description=description,
        application=application,
        application_version=application_version,
        occurred_at=datetime.now(UTC).isoformat(),
        source=source,
    )
    demo_log = json.dumps(
        {
            "level": "ERROR",
            "event_id": event.event_id,
            "error_code": event.error_code,
            "workflow": event.workflow,
            "customer_reference": customer_id,
            "message": event.description,
        }
    ).encode("utf-8")
    connector = ApprovedHostAppConnector(application=event.application, source=event.source)
    return connector.attach_diagnostic_log(event, demo_log)


def triage_application_error(
    graph: Any,
    event: ApplicationErrorEvent | dict[str, str],
) -> dict[str, Any]:
    from .graphs import run_triage

    if isinstance(event, ApplicationErrorEvent):
        title, description = event.title, event.description
    else:
        title, description = event["title"], event["description"]
    return run_triage(graph, title, description)


def build_escalation_diagnostic_log(
    *,
    event: dict[str, str],
    host_diagnostic_log: bytes | None,
    attempted_steps: list[str],
    support_article_ids: list[str],
) -> bytes:
    host_diagnostics: Any = None
    if host_diagnostic_log:
        decoded_log = host_diagnostic_log.decode("utf-8", errors="replace")
        try:
            host_diagnostics = json.loads(decoded_log)
        except json.JSONDecodeError:
            host_diagnostics = decoded_log

    event_fields = {
        key: event[key]
        for key in (
            "event_id",
            "workflow",
            "error_code",
            "title",
            "description",
            "application",
            "application_version",
            "occurred_at",
            "customer_id",
        )
        if isinstance(event.get(key), str)
    }
    bundle = {
        "source": "smartissue_escalation",
        "captured_at": datetime.now(UTC).isoformat(),
        "application_event": event_fields,
        "host_diagnostics": host_diagnostics,
        "attempted_resolution_steps": attempted_steps[:10],
        "support_article_ids": support_article_ids[:10],
    }
    return json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")