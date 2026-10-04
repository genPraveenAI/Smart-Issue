# ResolveDesk

A Python and Streamlit prototype of the associate's banking workspace, with contextual RAG support, evidence review, and reviewer-approved Jira escalation.

See [AGENTIC_AI_SOLUTION.md](AGENTIC_AI_SOLUTION.md) for the complete workflow, agent responsibilities, evidence/Jira handoff, configuration, and production boundaries. See [RAG_SOLUTION.md](RAG_SOLUTION.md) for JSON ingestion and retrieval details.

## Run locally

Python 3.11 or newer is recommended. From the project root:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
streamlit run app.py
```

Open `http://127.0.0.1:8501`. Streamlit is configured to bind to loopback only and not expose the report/evidence UI to the LAN. On first launch, the sentence-transformer model downloads into `.data/models`; embeddings then run locally on CPU. For an offline setup, pre-provision that model cache and set `ALLOW_MODEL_DOWNLOADS=false`.

Run focused tests with:

```powershell
python -m unittest discover -s tests -v
```

## Workflow

- Issue text is redacted for email addresses and common numeric identifiers before embedding or local report persistence.
- A LangGraph supervisor composes separate intake/redaction, resolution/retrieval, and issue-summary agents, plus evidence-sanitization and associate-confirmed Jira agents.
- JSON ingestion validates article schemas and duplicate IDs, redacts fields, and splits each article into versioned `article_overview` and `resolution_step` JSON chunks. Content hashes drive idempotent batched upserts and stale-chunk deletion, so source edits reconcile on startup. Retrieval searches chunks, applies the score threshold, groups matches back to source articles, supports category filtering, and enforces the tokenizer-based context budget. Results include source IDs, chunk IDs, matched fields, and scores. The local index uses `sentence-transformers/all-MiniLM-L6-v2`; the ingestion sync summary is shown on the Knowledge base page.
- Selecting “Still failing · raise issue” requests browser screen-sharing permission. The associate scrolls through the page and stops sharing to create a stitched capture of the error and visited page sections, then reviews the evidence before submission. At the same moment, the app builds a diagnostic log from the host error event, attempted resolution steps, and retrieved support-note IDs; the evidence agent redacts the bundle and capture before report persistence. Actual console/network logs require the real host application to provide them through its authenticated connector.
- Associates start on Customer search. After selecting a matching customer, they can navigate to Payments or Customer services; both workspaces use that profile.
- In Payments, continuing a customer operation emits a structured application error event; the LangGraph agents search for matching guidance and show fixes in the adjacent Resolution insights panel. The associate can mark a fix tried, resolve the event, or escalate it if it remains broken.
- A universal customer text search stays visible across Payments and Customer services; a selected profile populates both workspaces. Customer services has only Demographics and Open an account tabs. Submitting either operation shows its error first; the associate clicks Need resolution to retrieve cited LangGraph/Chroma guidance, mark steps tried, then resolve or raise the issue. Sample records live in `data/customer_profiles.json`; changes are written to the ignored `.data/customer_profiles.json` runtime store and are not connected to a banking core.
- After the associate reviews the evidence and confirms escalation, a configured Jira agent immediately creates the bug and attaches the sanitized screenshot and diagnostic log. If Jira is unavailable or disabled, the report and evidence are saved locally for reviewer follow-up.
- Jira stays disabled by default. To enable it, set `JIRA_ENABLED=true` and configure `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, and `JIRA_PROJECT_KEY` in the server environment. Never commit `.env` or use production credentials for local testing.
- Optional hosted issue drafting uses `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, and `OPENROUTER_BASE_URL` from the project-root `.env`. The associate must explicitly opt in before redacted issue details and retrieved support references are sent to OpenRouter. Embeddings and vector retrieval remain local; Ollama is an optional local drafting fallback.

The included knowledge entries and host events are sample data, not bank-approved guidance or a live connection to the bank application. Replace the demo event source in `smartissue/host_adapter.py` with the bank app's authenticated error/event callback and pass its console/network diagnostics to `ApprovedHostAppConnector.attach_diagnostic_log`; this is an in-process integration boundary, not a network endpoint. A browser app cannot silently read DevTools or capture a screen without associate permission. The local reviewer name is not authenticated. SSO, reviewer authorization, durable audit retention, and bank-specific redaction policies are not part of this prototype.

## Project layout

- `app.py`: ResolveDesk Streamlit workspace, contextual Resolution insights panel, evidence review, report tracker, and reviewer queue.
- `smartissue/agent.py`: local knowledge-base ingestion and retrieval, plus fact-grounded issue-draft generation.
- `smartissue/agents/`: LangGraph specialist subgraphs for intake/redaction, support resolution, and issue summaries.
- `smartissue/graphs/state.py`: shared typed state passed between triage graph nodes.
- `smartissue/graphs/triage.py`: top-level triage supervisor that composes the specialist subgraphs and exposes `run_triage`.
- `smartissue/customers.py`: local customer-profile search, demographic validation, and demo account-opening persistence.
- `smartissue/host_adapter.py`: typed app error events and the demo-to-RAG adapter.
- `smartissue/evidence.py`: screenshot metadata removal and uploaded-log secret/identifier redaction.
- `smartissue/screen_capture.py` and `components/screen_capture/index.html`: permission-gated scroll capture, stitched into a single evidence image.
- `smartissue/reports.py`: sanitized evidence persistence and reviewer status transitions.
- `smartissue/jira.py`: associate-confirmed Jira agent and optional Jira Cloud issue/attachment API.
- `.streamlit/config.toml`: loopback-only server binding and local file-watcher settings.
- `data/knowledge_base.json`: searchable sample support guidance for payments, transfers, customer profiles, and account opening.
- `data/customer_profiles.json`: synthetic seed customer profiles for the Customer services workspace.
