from __future__ import annotations

import base64
import hashlib
import os
import platform
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from smartissue.customers import (
    ACCOUNT_PRODUCTS,
    open_customer_account,
    search_customers,
    update_customer_demographics,
)
from smartissue.screen_capture import capture_screen

if TYPE_CHECKING:
    from smartissue.agent import LocalKnowledgeBase
    from smartissue.host_adapter import Workflow

st.set_page_config(
    page_title="ResolveDesk · Associate app",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600;700&display=swap');
    :root { --bank-blue:#012169; --bank-red:#e31837; --accenture-purple:#a100ff; --ink:#172b4d; --muted:#5d6b82; --line:#e3e8ef; --paper:#fff; --blue-tint:#f3f6fb; }
    html, body, [class*="css"] { font-family:'DM Sans','Segoe UI',sans-serif; color:var(--ink); }
    .stApp, main, [data-testid="stAppViewContainer"] { background:var(--paper); }
    [data-testid="stHeader"] { background:#fff; }
    [data-testid="stSidebar"] { background:#fff; border-right:1px solid var(--line); }
    [data-testid="stSidebar"] * { color:var(--ink); }
    [data-testid="stSidebar"] [data-testid="stRadio"] label { padding:8px 10px; border-radius:4px; }
    [data-testid="stSidebar"] [data-testid="stRadio"] label:hover { background:var(--blue-tint); }
    [data-testid="stSidebar"] .brand-blue { color:var(--bank-blue) !important; }
    [data-testid="stSidebar"] .brand-purple { color:var(--accenture-purple) !important; }
    [data-testid="stSidebar"] .brand-red { color:var(--bank-red) !important; }
    .block-container { max-width:1140px; padding-top:2.2rem; padding-bottom:3rem; }
    h1,h2,h3 { color:var(--ink); font-family:'DM Sans','Segoe UI',sans-serif !important; letter-spacing:0 !important; }
    h1 { font-size:2rem !important; font-weight:700 !important; }
    h2 { font-size:1.35rem !important; font-weight:700 !important; }
    h3 { font-size:1.1rem !important; font-weight:700 !important; }
    p, li { color:var(--muted); }
    [data-testid="stMetric"] { padding:12px 14px; border:1px solid var(--line); border-radius:4px; background:#fff; }
    [data-testid="stMetricLabel"] p { color:var(--muted); font-size:.72rem; }
    [data-testid="stMetricValue"] { color:var(--bank-blue); font-family:'DM Mono',monospace; font-size:1.2rem; }
    .eyebrow { color:var(--bank-blue); font:600 .66rem 'DM Mono',monospace; letter-spacing:.08em; text-transform:uppercase; }
    .subtle { color:var(--muted); font-size:.86rem; }
    .panel { padding:1rem 1.15rem; border:1px solid var(--line); border-radius:4px; background:#fff; }
    .kb-id { color:var(--bank-blue); font: .68rem 'DM Mono',monospace; }
    .score { color:var(--accenture-purple); font: .7rem 'DM Mono',monospace; }
    [data-testid="stForm"] { border:1px solid var(--line); border-radius:4px; background:#fff; }
    .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] { border:1px solid var(--bank-blue); background:var(--bank-blue); color:#fff; }
    .stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover { border-color:#001747; background:#001747; color:#fff; }
    .stButton > button[kind="secondary"] { border:1px solid var(--accenture-purple); color:var(--accenture-purple); background:#fff; }
    .stButton > button[kind="secondary"]:hover { border-color:var(--accenture-purple); color:var(--accenture-purple); background:#faf5ff; }
    div[data-testid="stFileUploader"] { border:1px dashed #b8c4d5; border-radius:4px; background:#fff; }
    .app-footer { margin-top:2rem; padding:1rem 0 .25rem; border-top:1px solid var(--line); color:var(--muted); font-size:.75rem; text-align:right; }
    hr { border-color:var(--line); }
    @media (max-width:720px) { .block-container { padding:1.2rem .9rem 2rem; } h1 { font-size:1.75rem !important; } }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner=False)
def get_agent_resources(knowledge_source_hash: str) -> tuple[LocalKnowledgeBase, Any]:
    from smartissue.agent import get_knowledge_base
    from smartissue.graphs import build_triage_graph

    knowledge_base = get_knowledge_base(knowledge_source_hash)
    return knowledge_base, build_triage_graph(knowledge_base)


def initialize_state() -> None:
    defaults = {
        "page": "Customer search",
        "current_customer_id": "",
        "step": 1,
        "title": "",
        "description": "",
        "customer_resolution_requested": False,
        "active_error": None,
        "active_host_log": None,
        "operation_amount": 240.0,
        "operation_workflow": "Card payment",
        "triage": None,
        "attempted_ids": [],
        "attempted_steps": [],
        "issue_draft": "",
        "issue_draft_editor": "",
        "draft_provider": "",
        "hosted_model_consent": False,
        "draft_consent_used": None,
        "last_report": None,
        "captured_screenshot": None,
        "captured_screenshot_mime": None,
        "automatic_diagnostic_log": None,
        "capture_error": "",
        "capture_nonce": 0,
        "evidence_nonce": 0,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    if st.session_state.page == "Issue desk":
        st.session_state.page = "Payments workspace"


def stage_header(current_step: int) -> None:
    columns = st.columns([1, 1, 1])
    names = ["01  Operate", "02  Resolve", "03  Escalate"]
    for index, (column, name) in enumerate(zip(columns, names, strict=True), start=1):
        with column:
            if index == current_step:
                st.markdown(f"**:blue[{name}]**")
            elif index < current_step:
                st.markdown(f":blue[{name} ✓]")
            else:
                st.caption(name)


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("<div style='font-size:23px;font-weight:700;margin-bottom:3px'><span class='brand-blue'>Resolve</span><span class='brand-purple'>Desk</span><span class='brand-red'>.</span></div><div style='font:9px monospace;letter-spacing:1px;color:#5d6b82;margin:4px 0 28px 2px'>ASSOCIATE SERVICE DESK</div>", unsafe_allow_html=True)
        st.markdown("<div class='eyebrow' style='margin:0 0 8px 9px'>WORKSPACE</div>", unsafe_allow_html=True)
        next_page = st.session_state.pop("next_page", None)
        if next_page:
            st.session_state.page = next_page
        page = st.radio("Workspace", ["Customer search", "Payments workspace", "Customer services", "Knowledge base", "My reports", "Review queue"], label_visibility="collapsed", key="page")
        st.divider()
        return page


def render_customer_search() -> None:
    customer_profiles = search_customers("")
    customer_names = {
        profile["customer_id"]: f"{profile['first_name']} {profile['last_name']} · {profile['customer_id']}"
        for profile in customer_profiles
    }
    if not customer_names:
        return
    if st.session_state.current_customer_id and st.session_state.current_customer_id not in customer_names:
        st.session_state.current_customer_id = ""
    active_error = st.session_state.get("active_error")
    customer_selection_locked = (
        bool(active_error)
        and st.session_state.get("page") in {"Payments workspace", "Customer services"}
        and st.session_state.get("step") in {2, 3}
    )
    search_col, detail_col = st.columns([.42, .58], vertical_alignment="center")
    with search_col:
        query = st.text_input(
            "Search customer",
            placeholder="Name, customer ID, email, phone, city, or postcode",
            key="customer_search_query",
            disabled=customer_selection_locked,
        )
    matches = search_customers(query)
    if query.strip() and len(matches) == 1:
        st.session_state.current_customer_id = matches[0]["customer_id"]
        st.session_state.customer_search_selection_query = query
    elif query.strip() and len(matches) > 1:
        st.caption(f"{len(matches)} customers found")
        if st.session_state.get("customer_search_selection_query") != query:
            st.session_state.current_customer_id = None
        result_columns = st.columns(min(len(matches), 4))
        for index, profile in enumerate(matches[:4]):
            with result_columns[index]:
                if st.button(
                    f"{profile['first_name']} {profile['last_name']} · {profile['customer_id']}",
                    key=f"select_customer_{profile['customer_id']}",
                    use_container_width=True,
                ):
                    st.session_state.current_customer_id = profile["customer_id"]
                    st.session_state.customer_search_selection_query = query
                    st.rerun()
    elif query.strip():
        st.caption("No customer matches that search.")
    with detail_col:
        profile = next(
            (item for item in customer_profiles if item["customer_id"] == st.session_state.current_customer_id),
            None,
        )
        if profile and (not query.strip() or len(matches) == 1 or st.session_state.get("customer_search_selection_query") == query):
            st.caption(f"Selected customer · {profile['city']} · {len(profile.get('accounts', []))} account(s)")
        elif not query.strip() and st.session_state.page == "Customer search":
            st.caption("Search for a customer to continue.")
        else:
            st.caption("Select a matching customer to continue.")


def render_customer_search_landing() -> None:
    st.markdown("<div class='eyebrow'>CUSTOMER OPERATIONS</div>", unsafe_allow_html=True)
    st.title("Customer search")
    st.caption("Search for a customer, then choose a workspace.")
    query = st.session_state.get("customer_search_query", "").strip()
    customer = get_current_customer()
    if not query:
        st.info("Search by name, customer ID, email, phone, city, or postcode.")
        return
    if customer is None:
        if search_customers(query):
            st.info("Select a matching customer above to continue.")
        else:
            st.info("No customer matches that search.")
        return

    st.markdown(f"#### {customer['first_name']} {customer['last_name']} · {customer['customer_id']}")
    payment_col, service_col = st.columns(2)
    with payment_col:
        if st.button("Open Payments workspace", type="primary", use_container_width=True):
            st.session_state.next_page = "Payments workspace"
            st.rerun()
    with service_col:
        if st.button("Open Customer services", type="secondary", use_container_width=True):
            st.session_state.next_page = "Customer services"
            st.rerun()


def get_current_customer() -> dict[str, Any] | None:
    customer_id = st.session_state.get("current_customer_id")
    query = st.session_state.get("customer_search_query", "").strip()
    matches = search_customers(query)
    if query and not matches:
        return None
    if query and len(matches) > 1 and st.session_state.get("customer_search_selection_query") != query:
        return None
    if query and customer_id not in {profile["customer_id"] for profile in matches}:
        return None
    return next((profile for profile in matches if profile["customer_id"] == customer_id), None)


def run_search(graph: Any, title: str, description: str) -> dict[str, Any]:
    from smartissue.graphs import run_triage

    with st.spinner("Loading the local embedding model and searching support notes…"):
        return run_triage(graph, title, description)


def render_issue_desk(knowledge_base: LocalKnowledgeBase, graph: Any) -> None:
    from smartissue.host_adapter import (
        make_demo_error_event,
        triage_application_error,
    )

    st.markdown("<div class='eyebrow'>BRANCH 042 · CUSTOMER OPERATIONS</div>", unsafe_allow_html=True)
    st.title("Payments workspace")
    customer = get_current_customer()
    intro, customer_action = st.columns([.72, .28], vertical_alignment="bottom")
    with intro:
        st.markdown("Customer servicing · secure associate session")
    with customer_action:
        if st.button("Find or manage customer", type="secondary", use_container_width=True):
            st.session_state.next_page = "Customer services"
            st.rerun()
    st.caption("Search customer profiles, update demographics, or open a demo account.")
    stage_header(st.session_state.step)

    if st.session_state.step == 3:
        render_evidence_review(knowledge_base, graph)
        return

    application, assistant = st.columns([1.12, .88], gap="large")
    with application:
        st.markdown("### Customer operation")
        st.caption("Customer details are populated from the shared customer search.")
        with st.container(border=True):
            if customer:
                st.markdown(f"**{customer['first_name']} {customer['last_name']}** · {customer['customer_id']}")
                st.markdown(f"**Address** · {customer['city']} · {customer['postcode']}")
                accounts = customer.get("accounts", [])
                if accounts:
                    st.markdown(f"**Accounts** · {', '.join(account['product_type'] for account in accounts)}")
                else:
                    st.markdown("**Accounts** · No accounts on this demo profile")
            else:
                st.warning("Select a customer from the shared Search customer control.")
            st.divider()
            with st.form("associate_operation"):
                workflows: list[Workflow] = ["Card payment", "Bank transfer", "Customer session"]
                workflow = st.selectbox(
                    "Customer request",
                    workflows,
                    key="operation_workflow",
                    disabled=bool(st.session_state.active_error),
                )
                amount = st.number_input(
                    "Payment or transfer amount (£)",
                    min_value=1.0,
                    max_value=10000.0,
                    step=10.0,
                    key="operation_amount",
                    disabled=workflow == "Customer session" or bool(st.session_state.active_error),
                )
                operation_submitted = st.form_submit_button(
                    "Continue customer request",
                    type="primary",
                    disabled=bool(st.session_state.active_error) or customer is None,
                    use_container_width=True,
                )
            st.caption("Demo host connector · the operation result below is simulated.")

        if operation_submitted:
            try:
                operation_context = f"Operation amount: £{amount:.2f}." if workflow != "Customer session" else ""
                event = make_demo_error_event(
                    workflow,
                    operation_context=operation_context,
                    customer_id=customer["customer_id"],
                )
                with st.spinner("Application error received · searching support guidance…"):
                    st.session_state.triage = triage_application_error(graph, event)
                st.session_state.active_error = {**event.to_dict(), "customer_id": customer["customer_id"]}
                st.session_state.active_host_log = event.diagnostic_log
                st.session_state.title = event.title
                st.session_state.description = event.description
                st.session_state.step = 2
                st.session_state.attempted_ids = []
                st.session_state.attempted_steps = []
                st.session_state.issue_draft = ""
                st.session_state.issue_draft_editor = ""
                st.session_state.draft_provider = ""
                st.session_state.hosted_model_consent = False
                st.session_state.draft_consent_used = None
                st.rerun()
            except (OSError, RuntimeError, ValueError) as error:
                st.error(f"Could not process the application error: {error}")

        active_error = st.session_state.active_error
        if active_error:
            st.error(f"{active_error['error_code']} · {active_error['title']}")
            st.caption(f"Event {active_error['event_id']} · {active_error['occurred_at']}")

    with assistant:
        st.markdown("### Resolution insights")
        st.caption("Contextual support · linked to the current application event")
        active_error = st.session_state.active_error
        if not active_error:
            st.info("Ready to help. Continue a customer request to see relevant support guidance.")
        else:
            capture_result = capture_screen(key=f"raise_issue_capture_{st.session_state.capture_nonce}")
            if accept_screen_capture(capture_result, active_error, st.session_state.triage.get("matches", [])):
                st.rerun()
            if st.session_state.capture_error:
                st.error(st.session_state.capture_error)
            result = st.session_state.triage or {}
            matches = result.get("matches", [])
            st.markdown(f"**{active_error['error_code']}** · {active_error['workflow']}")
            st.caption(f"{len(matches)} relevant support note(s) · {result.get('context_tokens', 0)} context tokens")
            for index, article in enumerate(matches, start=1):
                with st.container(border=True):
                    st.markdown(
                        f"<span class='kb-id'>Knowledge base source · {article['id']} · {article['category']}</span>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(f"#### {article['title']}")
                    st.write(article["summary"])
                    with st.expander("Resolution steps", expanded=index == 1):
                        for resolution_step in article["steps"]:
                            st.markdown(f"- {resolution_step}")
                        if st.button("Mark resolution as tried", key=f"tried_{active_error['event_id']}_{article['id']}"):
                            if article["id"] not in st.session_state.attempted_ids:
                                st.session_state.attempted_ids.append(article["id"])
                                st.session_state.attempted_steps.extend(article["steps"])
                            st.success("Recorded with this application event.")
            if st.session_state.attempted_ids:
                st.caption(f"Tried: {', '.join(st.session_state.attempted_ids)}")
            if st.button("Customer request completed", use_container_width=True):
                st.session_state.active_error = None
                st.session_state.active_host_log = None
                st.session_state.triage = None
                st.session_state.step = 1
                st.session_state.last_resolution = "Customer request completed using support guidance."
                st.rerun()
        if st.session_state.get("last_resolution"):
            st.success(st.session_state.pop("last_resolution"))


def collect_diagnostics() -> dict[str, str]:
    user_agent = st.context.headers.get("User-Agent", "")
    event = st.session_state.get("active_error") or {}
    return {
        "browser": user_agent[:160],
        "application_version": event.get("application_version", os.getenv("APPLICATION_VERSION", "Payments web · local demo")),
        "captured_at": datetime.now(UTC).isoformat(),
        "runtime": f"{platform.system()} {platform.release()} · Python {platform.python_version()}",
        "workflow": event.get("workflow", ""),
        "error_code": event.get("error_code", ""),
        "application_event_id": event.get("event_id", ""),
        "event_occurred_at": event.get("occurred_at", ""),
        "associate_id": os.getenv("ASSOCIATE_ID", "local-associate"),
    }


def accept_screen_capture(
    capture_result: Any,
    active_error: dict[str, Any],
    matches: list[dict[str, Any]],
) -> bool:
    if not isinstance(capture_result, dict):
        return False
    if capture_result.get("action") == "error":
        st.session_state.capture_error = str(capture_result.get("message", "Screen capture failed."))
        return False
    if capture_result.get("action") != "captured":
        return False

    try:
        from smartissue.evidence import stitch_capture_frames
        from smartissue.host_adapter import build_escalation_diagnostic_log

        frames = capture_result.get("frames")
        if isinstance(frames, list) and frames:
            frame_bytes = []
            for frame in frames:
                if not isinstance(frame, str):
                    raise ValueError("A captured page frame was invalid.")
                _, encoded = frame.split(",", maxsplit=1)
                frame_bytes.append(base64.b64decode(encoded, validate=True))
            screenshot = stitch_capture_frames(frame_bytes)
        elif isinstance(capture_result.get("dataUrl"), str):
            _, encoded = capture_result["dataUrl"].split(",", maxsplit=1)
            screenshot = base64.b64decode(encoded, validate=True)
        else:
            raise ValueError("The browser did not return any captured page frames.")

        st.session_state.captured_screenshot = screenshot
        st.session_state.captured_screenshot_mime = "image/jpeg"
        st.session_state.capture_error = ""
        st.session_state.automatic_diagnostic_log = build_escalation_diagnostic_log(
            event=active_error,
            host_diagnostic_log=st.session_state.get("active_host_log"),
            attempted_steps=st.session_state.attempted_steps,
            support_article_ids=[item["id"] for item in matches],
        )
        st.session_state.issue_draft = ""
        st.session_state.issue_draft_editor = ""
        st.session_state.draft_provider = ""
        st.session_state.draft_consent_used = None
        st.session_state.capture_nonce += 1
        st.session_state.step = 3
        return True
    except (ValueError, KeyError, OSError) as error:
        st.session_state.capture_error = f"Could not prepare screenshot evidence: {error}"
        return False


def render_evidence_review(knowledge_base: LocalKnowledgeBase, graph: Any) -> None:
    from smartissue.evidence import process_evidence
    from smartissue.graphs import run_triage
    from smartissue.jira import jira_configured, jira_configuration_problem, jira_enabled
    from smartissue.reports import save_report

    left, right = st.columns([1.2, .8], gap="large")
    with left:
        st.markdown("### Review before sending")
        st.caption("The scroll capture includes the error and the page views you visited. Customer identifiers are redacted from text.")
        openrouter_configured = bool(os.getenv("OPENROUTER_API_KEY", "").strip())
        use_hosted_model = st.checkbox(
            "Use the configured OpenRouter model (sends redacted issue and retrieved support text to OpenRouter)",
            value=False,
            key="hosted_model_consent",
            disabled=not openrouter_configured,
        )
        if not openrouter_configured:
            st.caption("OpenRouter is not configured. Add OPENROUTER_API_KEY to the project-root .env file to enable hosted drafting.")
        refresh_draft = st.button(
            "Generate issue draft" if not st.session_state.issue_draft else "Regenerate issue draft",
            type="secondary",
        )
        consent_changed = st.session_state.draft_consent_used != use_hosted_model
        if not st.session_state.issue_draft or refresh_draft or consent_changed:
            try:
                with st.spinner("Drafting a clear issue report from the facts you provided…"):
                    draft_state = run_triage(
                        graph,
                        st.session_state.title,
                        st.session_state.description,
                        draft_requested=True,
                        attempted_steps=st.session_state.attempted_steps,
                        hosted_model_consent=use_hosted_model,
                        workflow=(st.session_state.get("active_error") or {}).get("workflow", ""),
                        error_code=(st.session_state.get("active_error") or {}).get("error_code", ""),
                        application_event_id=(st.session_state.get("active_error") or {}).get("event_id", ""),
                    )
                st.session_state.issue_draft = draft_state.get("issue_draft", "")
                customer_id = (st.session_state.get("active_error") or {}).get("customer_id")
                if customer_id:
                    st.session_state.issue_draft += f"\n\nCustomer reference\n{customer_id}"
                st.session_state.draft_provider = draft_state.get("draft_provider", "Fact-only template")
                st.session_state.triage = draft_state
                st.session_state.draft_consent_used = use_hosted_model
                st.session_state["issue_draft_editor"] = st.session_state.issue_draft
            except (OSError, RuntimeError, ValueError) as error:
                st.error(f"Could not prepare the issue report: {error}")
        if st.session_state.draft_provider:
            st.caption(f"Draft provider: {st.session_state.draft_provider}")
        issue_draft = st.text_area("Generated issue description", height=250, key="issue_draft_editor")
        st.markdown("#### Captured application screen")
        selected_image = st.session_state.get("captured_screenshot")
        selected_mime = st.session_state.get("captured_screenshot_mime")
        if selected_image:
            st.image(selected_image, caption="Scroll capture of the issue page", use_container_width=True)
        else:
            st.error("No screenshot was captured. Return to the resolution panel and raise the issue again.")

        st.markdown("#### Diagnostic evidence")
        st.caption("Captured while you scroll through the issue page: application error, guidance, and lower-page details. Secrets and common identifiers are redacted before saving.")
        raw_log = st.session_state.get("automatic_diagnostic_log") or st.session_state.get("active_host_log")
        diagnostics = collect_diagnostics()
        evidence_error = ""
        try:
            evidence_preview = process_evidence(
                screenshot=selected_image,
                screenshot_mime=selected_mime,
                console_log=raw_log,
                diagnostic_context=diagnostics,
            )
            if evidence_preview.get("sanitized_log"):
                with st.expander(f"Automatic escalation log · {evidence_preview['log_redactions']} values masked"):
                    st.code(evidence_preview["sanitized_log"], language="json")
            if evidence_preview.get("sanitized_screenshot"):
                st.caption("Image metadata will be removed and the attachment resized before it is stored.")
        except ValueError as error:
            evidence_error = str(error)
            st.error(evidence_error)
        with st.expander("Details included"):
            st.write({key: value for key, value in diagnostics.items() if key not in {"runtime", "associate_id"}})
            st.caption("The browser requires screen-sharing approval. Diagnostic evidence is collected from the host event, attempted resolutions, and retrieved support references.")
        if not selected_image:
            st.info("Capture the affected application screen before submitting this bug report.")
        confirmed = st.checkbox("I confirm this remains an application issue and approve sending the description and selected evidence.")
        if jira_enabled() and not jira_configured():
            st.warning(f"{jira_configuration_problem()} The report will still be saved locally.")
        elif not jira_configured():
            st.info("Jira is not configured. Submissions are saved locally for review.")
        back, submit = st.columns([.65, 1.35])
        with back:
            if st.button("← Back to resolutions"):
                st.session_state.step = 2
                st.rerun()
        with submit:
            submit_label = "Confirm and create Jira bug" if jira_configured() else "Confirm and save report"
            send_clicked = st.button(
                submit_label,
                type="primary",
                disabled=not confirmed or bool(evidence_error) or not selected_image,
                use_container_width=True,
            )
        if send_clicked:
            try:
                with st.spinner("Saving the report and creating a configured Jira issue…"):
                    report = save_report(
                        title=st.session_state.title,
                        description=st.session_state.description,
                        issue_draft=issue_draft,
                        knowledge_ids=[item["id"] for item in (st.session_state.triage or {}).get("matches", [])],
                        attempted_steps=st.session_state.attempted_steps,
                        associate_confirmed=confirmed,
                        customer_id=(st.session_state.get("active_error") or {}).get("customer_id"),
                        application=(st.session_state.get("active_error") or {}).get("application", "Payments · Web"),
                        screenshot=selected_image,
                        screenshot_type=selected_mime,
                        console_log=raw_log,
                        diagnostic_context=diagnostics,
                        diagnostics=diagnostics,
                    )
                st.session_state.last_report = report
                st.session_state.next_page = "My reports"
                st.session_state.step = 1
                st.rerun()
            except (OSError, RuntimeError, ValueError) as error:
                st.error(f"Report could not be saved: {error}")
    with right:
        st.markdown("### Report preview")
        with st.container(border=True):
            st.markdown(f"**{st.session_state.title}**")
            st.write(st.session_state.description)
            st.divider()
            st.caption("APPLICATION")
            st.write((st.session_state.get("active_error") or {}).get("application", "Payments · Web"))
            st.caption("SUPPORT NOTES CHECKED")
            selected_ids = [item["id"] for item in (st.session_state.triage or {}).get("matches", [])]
            st.write(", ".join(selected_ids) if selected_ids else "None")
            st.caption("SCREENSHOT")
            st.write("Captured on raise issue" if selected_image else "Missing")
            st.caption("DIAGNOSTIC LOG")
            st.write("Automatic escalation diagnostic bundle" if raw_log else "Application runtime details")
            st.caption("HANDOFF")
            st.write("Associate confirmed · Jira issue created only when explicitly configured")


def render_customer_issue_resolution(
    knowledge_base: LocalKnowledgeBase | None = None,
    graph: Any | None = None,
) -> None:
    active_error = st.session_state.active_error
    if knowledge_base is None or graph is None:
        st.error("Knowledge resources are unavailable for this request.")
        return

    result = st.session_state.triage or {}
    matches = result.get("matches", [])
    st.markdown("### Resolution insights")
    insights, operation = st.columns([.95, 1.05], gap="large")
    with insights:
        st.caption("Guidance matched to the current customer-service error")
        st.markdown(f"**{active_error['error_code']}** · {active_error['workflow']}")
        st.caption(f"{len(matches)} relevant support note(s) · {result.get('context_tokens', 0)} context tokens")
        capture_result = capture_screen(key=f"customer_raise_issue_capture_{st.session_state.capture_nonce}")
        if accept_screen_capture(capture_result, active_error, matches):
            st.rerun()
        if st.session_state.capture_error:
            st.error(st.session_state.capture_error)
        if not matches:
            st.warning("No close support note found. You can still raise an issue.")
        for index, article in enumerate(matches, start=1):
            with st.container(border=True):
                st.markdown(
                    f"<span class='kb-id'>Knowledge base source · {article['id']} · {article['category']}</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"#### {article['title']}")
                st.write(article["summary"])
                with st.expander("Resolution steps", expanded=index == 1):
                    for step in article["steps"]:
                        st.markdown(f"- {step}")
                    if st.button("Mark resolution as tried", key=f"customer_tried_{active_error['event_id']}_{article['id']}"):
                        if article["id"] not in st.session_state.attempted_ids:
                            st.session_state.attempted_ids.append(article["id"])
                            st.session_state.attempted_steps.extend(article["steps"])
                        st.success("Recorded with this customer-service event.")
        if st.session_state.attempted_ids:
            st.caption(f"Tried: {', '.join(st.session_state.attempted_ids)}")

    with operation:
        st.markdown("### Customer operation")
        render_customer_error_context(active_error)
        pending = st.session_state.get("pending_customer_operation") or {}
        action = pending.get("action")
        resolve_label = {
            "demographics": "Save demographic changes",
            "account": "Complete account opening",
        }.get(action, "Customer request completed")
        if st.button(resolve_label, use_container_width=True):
            try:
                if action == "demographics":
                    update_customer_demographics(pending["customer_id"], pending["demographics"])
                    resolution_message = "Customer demographics were saved."
                elif action == "account":
                    account = open_customer_account(pending["customer_id"], pending["product_type"])
                    resolution_message = f"{account['product_type']} opened · {account['account_id']}"
                else:
                    resolution_message = "Customer request completed using support guidance."
                st.session_state.active_error = None
                st.session_state.active_host_log = None
                st.session_state.triage = None
                st.session_state.pending_customer_operation = None
                st.session_state.customer_resolution_requested = False
                st.session_state.step = 1
                st.session_state.last_resolution = resolution_message
                st.rerun()
            except (OSError, RuntimeError, ValueError) as error:
                st.error(f"Customer request could not be completed: {error}")


def render_customer_error_context(active_error: dict[str, Any]) -> None:
    with st.container(border=True):
        st.error(f"{active_error['error_code']} · {active_error['title']}")
    if st.session_state.get("last_resolution"):
        st.success(st.session_state.pop("last_resolution"))


def begin_customer_operation(
    customer_id: str,
    workflow: Workflow,
    action: str,
    operation_context: str,
    pending_operation: dict[str, Any],
) -> None:
    from smartissue.host_adapter import make_demo_error_event

    event = make_demo_error_event(
        workflow,
        application_version="customer-services-demo",
        operation_context=operation_context,
        application="Customer services · Web",
        source="customer-services-demo",
        customer_id=customer_id,
    )
    st.session_state.triage = None
    st.session_state.active_error = {
        **event.to_dict(),
        "customer_id": customer_id,
        "operation_context": operation_context,
    }
    st.session_state.active_host_log = event.diagnostic_log
    st.session_state.pending_customer_operation = pending_operation
    st.session_state.title = event.title
    st.session_state.description = event.description
    st.session_state.step = 2
    st.session_state.customer_resolution_requested = False
    st.session_state.attempted_ids = []
    st.session_state.attempted_steps = []
    st.session_state.issue_draft = ""
    st.session_state.issue_draft_editor = ""
    st.session_state.draft_provider = ""
    st.session_state.hosted_model_consent = False
    st.session_state.draft_consent_used = None
    st.session_state.captured_screenshot = None
    st.session_state.captured_screenshot_mime = None
    st.session_state.automatic_diagnostic_log = None
    st.rerun()


def render_customer_services(knowledge_base: LocalKnowledgeBase | None = None, graph: Any | None = None) -> None:
    st.markdown("<div class='eyebrow'>CUSTOMER OPERATIONS</div>", unsafe_allow_html=True)
    st.title("Customer services")
    st.caption("Maintain customer demographics or open an account for the selected customer.")

    active_error = st.session_state.get("active_error") or {}
    if active_error.get("application") == "Customer services · Web":
        if st.session_state.step == 3:
            if knowledge_base is None or graph is None:
                st.error("Knowledge resources are unavailable for evidence review.")
                return
            render_evidence_review(knowledge_base, graph)
            return
        if st.session_state.step == 2:
            render_customer_error_context(active_error)
            stage_header(2)
            if not st.session_state.get("customer_resolution_requested"):
                if st.button("Get resolution guidance", type="primary"):
                    try:
                        from smartissue.host_adapter import triage_application_error

                        with st.spinner("Searching support guidance for this error…"):
                            knowledge_source = ROOT / "data" / "knowledge_base.json"
                            knowledge_hash = hashlib.sha256(knowledge_source.read_bytes()).hexdigest()
                            _, resolution_graph = get_agent_resources(knowledge_hash)
                            st.session_state.triage = triage_application_error(resolution_graph, active_error)
                        st.session_state.customer_resolution_requested = True
                        st.rerun()
                    except (OSError, RuntimeError, ValueError) as error:
                        st.error(f"Support guidance could not be loaded: {error}")

    customer = get_current_customer()
    profile_tab, account_tab = st.tabs(
        ["Demographics", "Open an account"],
        key="customer_services_tabs",
        on_change="rerun",
    )
    with profile_tab:
        if not customer:
            st.info("Search for a customer in the shared customer selector to view demographics.")
        else:
            st.markdown(f"#### {customer['first_name']} {customer['last_name']} · {customer['customer_id']}")
            account_count = len(customer.get("accounts", []))
            metric_customer, metric_accounts = st.columns(2)
            metric_customer.metric("Customer reference", customer["customer_id"])
            metric_accounts.metric("Open accounts", account_count)
            with st.form(f"demographics_{customer['customer_id']}"):
                name_col, contact_col = st.columns(2)
                with name_col:
                    first_name = st.text_input("First name", value=customer["first_name"], key=f"first_{customer['customer_id']}")
                    last_name = st.text_input("Last name", value=customer["last_name"], key=f"last_{customer['customer_id']}")
                    birth_date = st.date_input(
                        "Date of birth",
                        value=date.fromisoformat(customer["date_of_birth"]),
                        max_value=date.today(),
                        key=f"dob_{customer['customer_id']}",
                    )
                    address_line_1 = st.text_input("Address line 1", value=customer["address_line_1"], key=f"address1_{customer['customer_id']}")
                    address_line_2 = st.text_input("Address line 2", value=customer.get("address_line_2", ""), key=f"address2_{customer['customer_id']}")
                with contact_col:
                    email = st.text_input("Email address", value=customer["email"], key=f"email_{customer['customer_id']}")
                    phone = st.text_input("Phone number", value=customer["phone"], key=f"phone_{customer['customer_id']}")
                    city = st.text_input("Town or city", value=customer["city"], key=f"city_{customer['customer_id']}")
                    postcode = st.text_input("Postcode", value=customer["postcode"], key=f"postcode_{customer['customer_id']}")
                    country = st.text_input("Country", value=customer["country"], key=f"country_{customer['customer_id']}")
                save_demographics = st.form_submit_button("Submit demographic update", type="primary")
            if save_demographics:
                try:
                    demographics = {
                            "first_name": first_name,
                            "last_name": last_name,
                            "date_of_birth": birth_date.isoformat(),
                            "email": email,
                            "phone": phone,
                            "address_line_1": address_line_1,
                            "address_line_2": address_line_2,
                            "city": city,
                            "postcode": postcode,
                            "country": country,
                        }
                    begin_customer_operation(
                        customer["customer_id"],
                        "Update customer demographics",
                        "demographics",
                        "Request to update the customer demographic record.",
                        {"action": "demographics", "customer_id": customer["customer_id"], "demographics": demographics},
                    )
                except (OSError, RuntimeError, ValueError) as error:
                    st.error(f"Customer profile update could not be started: {error}")
            if customer.get("accounts"):
                st.markdown("#### Existing accounts")
                for account in customer["accounts"]:
                    st.markdown(
                        f"**{account['product_type']}** · {account['account_id']} · {account['status']}"
                    )

    with account_tab:
        if not customer:
            st.info("Search for a customer in the shared customer selector before opening an account.")
        else:
            st.markdown(f"#### {customer['first_name']} {customer['last_name']} · {customer['customer_id']}")
            with st.form(f"open_customer_account_{customer['customer_id']}"):
                product_type = st.selectbox("Account product", ACCOUNT_PRODUCTS)
                st.caption("The demo checks support guidance before completing this account-opening request.")
                open_account = st.form_submit_button("Submit account opening", type="primary")
            if open_account:
                try:
                    begin_customer_operation(
                        customer["customer_id"],
                        "Open a new account",
                        "account",
                        f"Requested account product: {product_type}.",
                        {"action": "account", "customer_id": customer["customer_id"], "product_type": product_type},
                    )
                except (OSError, RuntimeError, ValueError) as error:
                    st.error(f"Account request could not be started: {error}")

    if (
        active_error.get("application") == "Customer services · Web"
        and st.session_state.step == 2
        and st.session_state.get("customer_resolution_requested")
    ):
        st.divider()
        render_customer_issue_resolution(knowledge_base, graph)


def render_knowledge_page(knowledge_base: LocalKnowledgeBase) -> None:
    st.markdown("<div class='eyebrow'>SUPPORT GUIDANCE</div>", unsafe_allow_html=True)
    st.title("Knowledge base")
    articles = list(knowledge_base.articles.values())
    categories = sorted({article["category"] for article in articles})
    metric_articles, metric_categories, metric_chunks = st.columns(3)
    metric_articles.metric("Support notes", len(articles))
    metric_categories.metric("Categories", len(categories))
    metric_chunks.metric("Indexed chunks", knowledge_base.collection.count())
    with st.form("knowledge_search"):
        query_col, category_col = st.columns([.72, .28])
        with query_col:
            query = st.text_input("Search support notes", placeholder="Account opening, address changes, payment delays…")
        with category_col:
            category = st.selectbox("Category", ["All categories", *categories])
        submitted = st.form_submit_button("Search guidance", type="primary")
    if submitted:
        try:
            selected_category = None if category == "All categories" else category
            if query.strip():
                matches, _ = knowledge_base.search(query, category=selected_category)
            else:
                matches = [article for article in articles if selected_category is None or article["category"] == selected_category]
            st.session_state.knowledge_results = matches
        except (OSError, RuntimeError, ValueError) as error:
            st.error(f"Knowledge search failed: {error}")
    results = st.session_state.get("knowledge_results")
    if results is None:
        results = sorted(articles, key=lambda article: (article["category"], article["title"]))
    st.markdown(f"#### {len(results)} support note(s)")
    if not results:
        st.info("No support notes match those filters. Try a broader phrase or another category.")
    for article in results:
        with st.container(border=True):
            title_col, meta_col = st.columns([.72, .28])
            with title_col:
                st.markdown(f"#### {article['title']}")
            with meta_col:
                st.caption(f"{article['category']} · {article['id']}")
            st.write(article["summary"])
            if article.get("score") is not None:
                st.caption(f"{article['score']:.0%} relevance · Updated {article['updated']}")
            else:
                st.caption(f"Updated {article['updated']}")
            with st.expander("Resolution steps"):
                for step in article["steps"]:
                    st.markdown(f"- {step}")
    sync = knowledge_base.last_ingest_stats
    st.caption(
        f"Local vector index · {sync['source_articles']} articles · {sync['total_chunks']} JSON chunks · "
        f"{sync['upserted_chunks']} changed · {sync['deleted_chunks']} removed · "
        f"{os.getenv('EMBEDDING_MODEL_ID', 'all-MiniLM-L6-v2')}"
    )


def render_reports_page() -> None:
    from smartissue.reports import list_reports
    from smartissue.jira import jira_issue_url

    st.markdown("<div class='eyebrow'>REPORT TRACKER</div>", unsafe_allow_html=True)
    st.title("My reports")
    st.markdown("Saved local reports and configured Jira handoffs.")
    if st.button("Return to Payments workspace", type="primary"):
        st.session_state.next_page = "Payments workspace"
        st.session_state.step = 1
        st.session_state.title = ""
        st.session_state.description = ""
        st.session_state.active_error = None
        st.session_state.active_host_log = None
        st.session_state.triage = None
        st.session_state.attempted_ids = []
        st.session_state.attempted_steps = []
        st.session_state.issue_draft = ""
        st.session_state.issue_draft_editor = ""
        st.session_state.captured_screenshot = None
        st.session_state.captured_screenshot_mime = None
        st.session_state.automatic_diagnostic_log = None
        st.session_state.capture_error = ""
        st.session_state.capture_nonce += 1
        st.session_state.evidence_nonce += 1
        st.rerun()
    reports = list_reports()
    if not reports:
        st.info("No reports have been sent yet. Continue a customer request in the Payments workspace.")
        return
    for report in reports:
        with st.container(border=True):
            details, status = st.columns([.75, .25])
            with details:
                st.markdown(f"#### {report['title']}")
                st.caption(f"{report['report_id']} · {report['created_at'][:19].replace('T', ' ')} · {report['application']}")
                if report.get("customer_id"):
                    st.caption(f"Customer reference · {report['customer_id']}")
                st.write(report["description"])
                if report.get("screenshot_name"):
                    st.caption(f"Evidence attached: {report['screenshot_name']}")
                if report.get("diagnostic_log_name"):
                    log_path = ROOT / ".data" / "reports" / report["report_id"] / report["diagnostic_log_name"]
                    st.caption(f"Sanitized diagnostic log · {report.get('evidence_redactions', 0)} values masked")
                    if log_path.is_file():
                        with st.expander("View sanitized log"):
                            st.code(log_path.read_text(encoding="utf-8"), language="text")
                if report.get("jira_warning"):
                    st.warning(report["jira_warning"])
            with status:
                if report.get("jira_key"):
                    st.success(f"Jira {report['jira_key']}")
                    issue_url = jira_issue_url(report["jira_key"])
                    if issue_url:
                        st.link_button("Open Jira issue", issue_url, use_container_width=True)
                elif report.get("status") == "review_approved":
                    st.success("Reviewed · Jira not enabled")
                elif report.get("status") == "rejected":
                    st.error("Not a production bug")
                else:
                    st.info("Awaiting review")


def render_review_queue() -> None:
    from smartissue.jira import jira_issue_url
    from smartissue.reports import approve_report, list_reports, reject_report

    st.markdown("<div class='eyebrow'>HUMAN APPROVAL GATE</div>", unsafe_allow_html=True)
    st.title("Review queue")
    st.markdown("Review locally saved reports when Jira is unavailable or an integration attempt needs follow-up.")
    st.caption("Configured Jira issues are created after the associate confirms the escalation.")
    reviewer = st.text_input("Reviewer name", value="Local reviewer", max_chars=120)
    reports = [report for report in list_reports() if report.get("status") == "awaiting_review"]
    if not reports:
        st.success("No reports are waiting for review.")
        return

    for report in reports:
        with st.container(border=True):
            st.caption(f"{report['report_id']} · {report['created_at'][:19].replace('T', ' ')} · {report['application']}")
            if report.get("customer_id"):
                st.caption(f"Customer reference · {report['customer_id']}")
            st.markdown(f"### {report['title']}")
            with st.expander("Issue details and evidence", expanded=True):
                st.text(report["issue_draft"])
                st.write("Support notes:", ", ".join(report.get("knowledge_ids", [])) or "None")
                st.write("Resolution steps tried:", ", ".join(report.get("attempted_steps", [])) or "None recorded")
                st.write("Diagnostics:", report.get("diagnostics") or "Not included")
                screenshot_name = report.get("screenshot_name")
                screenshot_path = ROOT / ".data" / "reports" / report["report_id"] / screenshot_name if screenshot_name else None
                if screenshot_path and screenshot_path.is_file():
                    st.image(str(screenshot_path), caption=screenshot_name, use_container_width=True)
                log_name = report.get("diagnostic_log_name")
                log_path = ROOT / ".data" / "reports" / report["report_id"] / log_name if log_name else None
                if log_path and log_path.is_file():
                    st.caption(f"Diagnostic log · {report.get('evidence_redactions', 0)} values redacted")
                    st.code(log_path.read_text(encoding="utf-8"), language="text")
            approve_col, reject_col = st.columns([1, 1])
            with approve_col:
                approve_clicked = st.button("Approve and create Jira bug", type="primary", key=f"approve_{report['report_id']}", disabled=not reviewer.strip())
            with reject_col:
                reason = st.text_input("Reason if rejected", key=f"reason_{report['report_id']}", placeholder="Optional reviewer note", max_chars=500)
                reject_clicked = st.button("Reject as not a bug", key=f"reject_{report['report_id']}", disabled=not reviewer.strip())
            if approve_clicked:
                try:
                    reviewed = approve_report(report["report_id"], reviewer)
                    if reviewed.get("jira_key"):
                        st.success(f"Approved and created Jira issue {reviewed['jira_key']}.")
                        issue_url = jira_issue_url(reviewed["jira_key"])
                        if issue_url:
                            st.link_button("Open Jira issue", issue_url)
                    else:
                        st.success("Approval recorded. Jira is disabled; no issue was created.")
                    if reviewed.get("jira_warning"):
                        st.warning(reviewed["jira_warning"])
                    st.rerun()
                except (OSError, RuntimeError, ValueError) as error:
                    st.error(f"Could not approve this report: {error}")
            if reject_clicked:
                try:
                    reject_report(report["report_id"], reviewer, reason)
                    st.success("Report rejected and recorded for audit.")
                    st.rerun()
                except (OSError, RuntimeError, ValueError) as error:
                    st.error(f"Could not reject this report: {error}")


def main() -> None:
    initialize_state()
    page = render_sidebar()
    render_customer_search()
    active_error = st.session_state.get("active_error") or {}
    customer_issue_requires_knowledge = (
        active_error.get("application") == "Customer services · Web"
        and (
            st.session_state.step == 3
            or (st.session_state.step == 2 and st.session_state.get("customer_resolution_requested"))
        )
    )
    if page == "Customer search":
        render_customer_search_landing()
    elif page == "Customer services" and not customer_issue_requires_knowledge:
        render_customer_services()
    else:
        try:
            with st.spinner("Preparing local knowledge search on first run…"):
                knowledge_source = ROOT / "data" / "knowledge_base.json"
                knowledge_source_hash = hashlib.sha256(knowledge_source.read_bytes()).hexdigest()
                knowledge_base, graph = get_agent_resources(knowledge_source_hash)
        except (OSError, RuntimeError, ValueError) as error:
            st.error(f"Local retrieval could not start: {error}")
            st.info("Check requirements.txt, model download access, and the configured model cache.")
            st.stop()
        if page == "Customer services":
            render_customer_services(knowledge_base, graph)
        elif page == "Payments workspace":
            render_issue_desk(knowledge_base, graph)
        elif page == "Knowledge base":
            render_knowledge_page(knowledge_base)
        elif page == "My reports":
            render_reports_page()
        else:
            render_review_queue()
    st.markdown("<footer class='app-footer'>© 2026 Accenture</footer>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()