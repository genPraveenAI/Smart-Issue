from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PIL.PngImagePlugin import PngInfo
from requests import HTTPError
from chromadb import EphemeralClient
from chromadb.config import Settings

from smartissue import reports
from smartissue.agent import LocalKnowledgeBase
from smartissue.customers import open_customer_account, search_customers, update_customer_demographics
from smartissue.evidence import process_evidence, stitch_capture_frames
from smartissue.graphs import build_triage_graph, run_triage
from smartissue.host_adapter import (
    ApprovedHostAppConnector,
    build_escalation_diagnostic_log,
    make_demo_error_event,
    triage_application_error,
)
from smartissue.jira import build_jira_agent, jira_configuration_problem, jira_issue_url


class FakeKnowledgeBase:
    def search(self, query: str, limit: int = 4):
        return (
            [
                {
                    "id": "KB-TEST",
                    "title": "Payment authorisation pending",
                    "summary": "Review the transaction before retrying.",
                    "category": "Payments",
                    "updated": "Sample",
                    "steps": ["Check the transaction timeline."],
                    "score": 0.91,
                    "token_count": 19,
                }
            ],
            19,
        )

    def count_tokens(self, value: str) -> int:
        return len(value.split())


class FakeEmbeddingModel:
    def encode(self, values: list[str], *, normalize_embeddings: bool, batch_size: int = 32) -> list[list[float]]:
        vectors = []
        for value in values:
            lowered = value.lower()
            vector = [
                float(any(term in lowered for term in ("card", "payment", "authorisation"))) + 0.01,
                float(any(term in lowered for term in ("transfer", "beneficiary", "arrival"))) + 0.01,
                float(any(term in lowered for term in ("session", "locked", "refresh"))) + 0.01,
            ]
            magnitude = sum(component * component for component in vector) ** 0.5
            vectors.append([component / magnitude for component in vector])
        return vectors


class FakeTokenizer:
    def encode(self, value: str, *, add_special_tokens: bool) -> list[str]:
        return value.split()


class WorkflowTests(unittest.TestCase):
    def test_jira_configuration_rejects_atlassian_account_homepage(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "JIRA_ENABLED": "true",
                "JIRA_BASE_URL": "https://home.atlassian.com",
                "JIRA_EMAIL": "associate@example.test",
                "JIRA_API_TOKEN": "synthetic-token",
                "JIRA_PROJECT_KEY": "BANK",
            },
        ):
            message = jira_configuration_problem()

        self.assertIsNotNone(message)
        self.assertIn("your Jira site URL", message)

    def test_jira_issue_url_uses_configured_site_and_valid_key(self) -> None:
        self.assertEqual(
            jira_issue_url("BANK-123", base_url="https://example.atlassian.net/"),
            "https://example.atlassian.net/browse/BANK-123",
        )
        self.assertIsNone(jira_issue_url("not-a-jira-key", base_url="https://example.atlassian.net"))
        self.assertIsNone(jira_issue_url("BANK-123", base_url=""))

    def test_scroll_capture_frames_are_combined_into_one_evidence_image(self) -> None:
        frames = []
        for color in ("white", "red", "blue"):
            buffer = BytesIO()
            Image.new("RGB", (40, 30), color).save(buffer, format="JPEG")
            frames.append(buffer.getvalue())

        combined = stitch_capture_frames(frames)
        with Image.open(BytesIO(combined)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (88, 68))
        sanitized = process_evidence(screenshot=combined, screenshot_mime="image/jpeg")
        self.assertEqual(sanitized["sanitized_screenshot_mime"], "image/jpeg")
        self.assertIsNotNone(sanitized["sanitized_screenshot"])

    def test_synthetic_customer_profile_seed_is_searchable(self) -> None:
        matches = search_customers("CUST-1042")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["first_name"], "Amina")
        self.assertIn("example.test", matches[0]["email"])

    def test_customer_search_updates_demographics_and_persists_locally(self) -> None:
        profile = {
            "customer_id": "CUST-TEST-001",
            "first_name": "Alex",
            "last_name": "Morgan",
            "date_of_birth": "1990-04-12",
            "email": "alex@example.test",
            "phone": "+44 7700 900123",
            "address_line_1": "1 Example Street",
            "address_line_2": "",
            "city": "London",
            "postcode": "EX1 1AA",
            "country": "United Kingdom",
            "accounts": [],
        }
        document = {"schema_version": 1, "profiles": [profile]}
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "customers.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            matches = search_customers("morgan", path=path)
            self.assertEqual([item["customer_id"] for item in matches], ["CUST-TEST-001"])

            demographics = {key: value for key, value in profile.items() if key in {
                "first_name", "last_name", "date_of_birth", "email", "phone",
                "address_line_1", "address_line_2", "city", "postcode", "country",
            }}
            demographics["city"] = "Manchester"
            updated = update_customer_demographics("CUST-TEST-001", demographics, path=path)
            self.assertEqual(updated["city"], "Manchester")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["profiles"][0]["city"], "Manchester")

    def test_open_customer_account_persists_a_supported_product(self) -> None:
        profile = {
            "customer_id": "CUST-TEST-002",
            "first_name": "Sam",
            "last_name": "Taylor",
            "accounts": [],
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "customers.json"
            path.write_text(json.dumps({"schema_version": 1, "profiles": [profile]}), encoding="utf-8")
            account = open_customer_account("CUST-TEST-002", "Current account", path=path)
            saved_profile = json.loads(path.read_text(encoding="utf-8"))["profiles"][0]
            self.assertTrue(account["account_id"].startswith("AC-"))
            self.assertEqual(account["status"], "Active")
            self.assertEqual(saved_profile["accounts"], [account])

    def test_json_rag_ingestion_is_idempotent_and_reconciles_updates_and_deletions(self) -> None:
        articles = [
            {
                "id": "KB-CARD",
                "title": "Card payment failed",
                "summary": "Check the pending authorisation.",
                "category": "Payments",
                "updated": "2026-10-01",
                "steps": ["Check the card timeline.", "Do not retry the payment."],
            },
            {
                "id": "KB-TRANSFER",
                "title": "Transfer delayed",
                "summary": "Review the beneficiary and arrival window.",
                "category": "Transfers",
                "updated": "2026-10-01",
                "steps": ["Check the beneficiary.", "Confirm the expected arrival time."],
            },
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            knowledge_path = Path(temporary_directory) / "knowledge.json"
            knowledge_path.write_text(json.dumps(articles), encoding="utf-8")
            client = EphemeralClient(settings=Settings(anonymized_telemetry=False))
            knowledge_base = LocalKnowledgeBase(
                knowledge_path=knowledge_path,
                model=FakeEmbeddingModel(),
                tokenizer=FakeTokenizer(),
                client=client,
            )

            self.assertEqual(knowledge_base.last_ingest_stats["source_articles"], 2)
            self.assertEqual(knowledge_base.last_ingest_stats["total_chunks"], 6)
            self.assertEqual(knowledge_base.last_ingest_stats["upserted_chunks"], 6)
            stored = knowledge_base.collection.get(include=["documents", "metadatas"])
            chunk = json.loads(stored["documents"][0])
            self.assertEqual(chunk["chunk_schema_version"], 1)
            self.assertIn(chunk["chunk_type"], {"article_overview", "resolution_step"})

            repeated = knowledge_base.ingest()
            self.assertEqual(repeated["upserted_chunks"], 0)
            self.assertEqual(repeated["deleted_chunks"], 0)

            articles[1]["summary"] = "Review the beneficiary before checking the arrival window."
            articles[1]["steps"] = ["Check the updated beneficiary record."]
            knowledge_path.write_text(json.dumps(articles[1:]), encoding="utf-8")
            updated = knowledge_base.ingest()
            self.assertEqual(updated["source_articles"], 1)
            self.assertEqual(updated["upserted_chunks"], 2)
            self.assertEqual(updated["deleted_chunks"], 4)
            self.assertEqual(knowledge_base.collection.count(), 2)

            matches, _ = knowledge_base.search("beneficiary transfer arrival")
            self.assertEqual([match["source_id"] for match in matches], ["KB-TRANSFER"])
            self.assertIn("Check the updated beneficiary record.", matches[0]["steps"])
            filtered, _ = knowledge_base.search("beneficiary transfer arrival", category="Payments")
            self.assertEqual(filtered, [])

    def test_json_rag_rejects_duplicate_article_ids(self) -> None:
        article = {
            "id": "KB-DUPLICATE",
            "title": "Title",
            "summary": "Summary",
            "category": "Payments",
            "updated": "2026-10-01",
            "steps": [],
        }
        with self.assertRaisesRegex(ValueError, "duplicate article id"):
            from smartissue.agent import normalize_knowledge_articles

            normalize_knowledge_articles([article, article])

    def test_json_rag_rejects_empty_source_before_indexing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            knowledge_path = Path(temporary_directory) / "knowledge.json"
            knowledge_path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "at least one article"):
                LocalKnowledgeBase(
                    knowledge_path=knowledge_path,
                    model=FakeEmbeddingModel(),
                    tokenizer=FakeTokenizer(),
                    client=EphemeralClient(settings=Settings(anonymized_telemetry=False)),
                )

    def test_supervisor_composes_specialist_agents(self) -> None:
        graph = build_triage_graph(FakeKnowledgeBase())
        node_names = set(graph.get_graph().nodes)
        self.assertTrue({"intake_agent", "resolution_agent", "issue_summary_agent"}.issubset(node_names))

    def test_graph_redacts_sensitive_text_and_retrieves_guidance(self) -> None:
        graph = build_triage_graph(FakeKnowledgeBase())
        result = run_triage(
            graph,
            "Payment declined for jane@example.com",
            "Pending card transaction 4111 1111 1111 1111",
        )

        self.assertEqual(result["matches"][0]["id"], "KB-TEST")
        self.assertNotIn("jane@example.com", result["query"])
        self.assertNotIn("4111 1111", result["query"])
        self.assertLessEqual(result["context_tokens"], 420)
        self.assertEqual(result["issue_draft"], "")

        drafted = run_triage(
            graph,
            "Payment rejected",
            "Pending authorization.",
            draft_requested=True,
            attempted_steps=["Checked the transaction timeline."],
            workflow="Card payment",
            error_code="PAYMENT_AUTH_PENDING",
            application_event_id="EVT-SYNTHETIC",
        )
        self.assertIn("Steps tried", drafted["issue_draft"])
        self.assertIn("PAYMENT_AUTH_PENDING", drafted["issue_draft"])
        self.assertIn("Card payment", drafted["issue_draft"])
        self.assertIn("EVT-SYNTHETIC", drafted["issue_draft"])
        self.assertIn("Related support references\nKB-TEST", drafted["issue_draft"])

    def test_openrouter_model_drafts_from_redacted_issue_and_retrieved_sources(self) -> None:
        graph = build_triage_graph(FakeKnowledgeBase())
        response = unittest.mock.Mock()
        response.json.return_value = {
            "choices": [{"message": {"content": "Summary\nPayment issue\n\nRelated support references\nKB-TEST"}}]
        }
        with (
            patch.dict(
                "os.environ",
                {
                    "OPENROUTER_API_KEY": "test-openrouter-key",
                    "OPENROUTER_MODEL": "nvidia/nemotron-3-ultra-550b-a55b:free",
                },
            ),
            patch("smartissue.agent.requests.post", return_value=response) as openrouter_request,
        ):
            result = run_triage(
                graph,
                "Payment declined for jane@example.com",
                "Pending card payment 4111 1111 1111 1111",
                draft_requested=True,
                hosted_model_consent=True,
            )

        self.assertEqual(result["draft_provider"], "OpenRouter (nvidia/nemotron-3-ultra-550b-a55b:free)")
        self.assertIn("KB-TEST", result["issue_draft"])
        request_arguments = openrouter_request.call_args.kwargs
        self.assertEqual(
            openrouter_request.call_args.args[0],
            "https://openrouter.ai/api/v1/chat/completions",
        )
        self.assertEqual(request_arguments["headers"]["Authorization"], "Bearer test-openrouter-key")
        self.assertEqual(request_arguments["json"]["model"], "nvidia/nemotron-3-ultra-550b-a55b:free")
        user_content = request_arguments["json"]["messages"][1]["content"]
        self.assertIn("KB-TEST", user_content)
        self.assertNotIn("jane@example.com", user_content)
        self.assertNotIn("4111 1111 1111 1111", user_content)

    def test_openrouter_is_not_called_without_explicit_consent(self) -> None:
        graph = build_triage_graph(FakeKnowledgeBase())
        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-openrouter-key"}),
            patch("smartissue.agent.requests.post") as openrouter_request,
        ):
            result = run_triage(graph, "Payment issue", "Payment declined", draft_requested=True)

        self.assertEqual(result["draft_provider"], "Fact-only template")
        openrouter_request.assert_not_called()

    def test_openrouter_rate_limit_is_reported_and_uses_safe_fallback(self) -> None:
        graph = build_triage_graph(FakeKnowledgeBase())
        response = unittest.mock.Mock()
        response.status_code = 429
        response.raise_for_status.side_effect = HTTPError(response=response)
        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-openrouter-key"}),
            patch("smartissue.agent.requests.post", return_value=response),
        ):
            result = run_triage(
                graph,
                "Synthetic payment issue",
                "Synthetic payment is pending.",
                draft_requested=True,
                hosted_model_consent=True,
            )

        self.assertEqual(result["draft_provider"], "Fact-only template (OpenRouter HTTP 429)")
        self.assertTrue(result["issue_draft"])

    def test_application_error_event_routes_to_rag(self) -> None:
        graph = build_triage_graph(FakeKnowledgeBase())
        event = make_demo_error_event("Bank transfer", operation_context="Operation amount: £240.00.")
        result = triage_application_error(graph, event)

        self.assertEqual(event.error_code, "TRANSFER_STATUS_DELAYED")
        self.assertIn("£240.00", result["query"])
        self.assertEqual(result["matches"][0]["id"], "KB-TEST")

    def test_customer_service_error_event_routes_to_profile_guidance(self) -> None:
        graph = build_triage_graph(FakeKnowledgeBase())
        event = make_demo_error_event(
            "Update customer demographics",
            application_version="customer-services-demo",
            application="Customer services · Web",
            source="customer-services-demo",
            customer_id="CUST-1042",
        )
        result = triage_application_error(graph, event)

        self.assertEqual(event.error_code, "CUSTOMER_PROFILE_UPDATE_FAILED")
        self.assertEqual(event.application, "Customer services · Web")
        self.assertIn("CUST-1042", result["query"])
        self.assertEqual(result["matches"][0]["id"], "KB-TEST")

    def test_serialized_customer_error_routes_to_profile_guidance(self) -> None:
        graph = build_triage_graph(FakeKnowledgeBase())
        event = make_demo_error_event(
            "Update customer demographics",
            application="Customer services · Web",
            source="customer-services-demo",
            customer_id="CUST-2087",
        )

        result = triage_application_error(graph, event.to_dict())

        self.assertEqual(result["title"], event.title)
        self.assertEqual(result["matches"][0]["id"], "KB-TEST")

    def test_customer_demo_error_can_be_recreated_with_a_fresh_event_id(self) -> None:
        first = make_demo_error_event(
            "Update customer demographics",
            operation_context="Request details: Address update failed.",
            application="Customer services · Web",
            source="customer-services-demo",
            customer_id="CUST-1042",
        )
        repeated = make_demo_error_event(
            "Update customer demographics",
            operation_context="Request details: Address update failed.",
            application="Customer services · Web",
            source="customer-services-demo",
            customer_id="CUST-1042",
        )

        self.assertEqual(first.error_code, repeated.error_code)
        self.assertEqual(first.description, repeated.description)
        self.assertNotEqual(first.event_id, repeated.event_id)

    def test_approved_host_connector_attaches_diagnostics_only_to_approved_events(self) -> None:
        event = make_demo_error_event("Bank transfer")
        connector = ApprovedHostAppConnector(application=event.application, source=event.source)
        connected = connector.attach_diagnostic_log(event, b'{"level":"ERROR","message":"transfer failed"}')

        self.assertIn(b"transfer failed", connected.diagnostic_log)
        self.assertNotIn("diagnostic_log", connected.to_dict())
        with self.assertRaisesRegex(ValueError, "approved host application"):
            ApprovedHostAppConnector(application="Other app", source=event.source).attach_diagnostic_log(
                event, b"untrusted"
            )

    def test_escalation_diagnostic_log_captures_event_steps_and_support_sources(self) -> None:
        event = make_demo_error_event("Bank transfer")
        diagnostic_log = build_escalation_diagnostic_log(
            event=event.to_dict(),
            host_diagnostic_log=event.diagnostic_log,
            attempted_steps=["Checked transfer status."],
            support_article_ids=["KB-0871"],
        )
        bundle = json.loads(diagnostic_log)

        self.assertEqual(bundle["source"], "smartissue_escalation")
        self.assertEqual(bundle["application_event"]["event_id"], event.event_id)
        self.assertEqual(bundle["attempted_resolution_steps"], ["Checked transfer status."])
        self.assertEqual(bundle["support_article_ids"], ["KB-0871"])
        self.assertEqual(bundle["host_diagnostics"]["error_code"], event.error_code)

    def test_evidence_agent_redacts_secrets_and_strips_image_metadata(self) -> None:
        metadata = PngInfo()
        metadata.add_text("comment", "customer account 4111 1111 1111 1111")
        image_buffer = BytesIO()
        Image.new("RGB", (24, 24), "white").save(image_buffer, format="PNG", pnginfo=metadata)
        log = b"Authorization: Bearer top-secret-token\nemail=jane@example.com\n"

        evidence = process_evidence(
            screenshot=image_buffer.getvalue(),
            screenshot_mime="image/png",
            console_log=log,
        )

        self.assertNotIn("top-secret-token", evidence["sanitized_log"])
        self.assertNotIn("jane@example.com", evidence["sanitized_log"])
        self.assertGreaterEqual(evidence["log_redactions"], 2)
        sanitized_image = Image.open(BytesIO(evidence["sanitized_screenshot"]))
        self.assertNotIn("comment", sanitized_image.info)

    def test_evidence_agent_builds_diagnostic_bundle_without_uploaded_log(self) -> None:
        evidence = process_evidence(
            diagnostic_context={
                "captured_at": "2026-10-02T10:00:00+00:00",
                "workflow": "Bank transfer",
                "error_code": "TRANSFER_STATUS_DELAYED",
                "application_event_id": "EVT-1234567890",
                "browser": "Chrome",
                "associate_id": "not-included",
            }
        )

        self.assertIn("smartissue_host_event_and_runtime", evidence["sanitized_log"])
        self.assertIn("TRANSFER_STATUS_DELAYED", evidence["sanitized_log"])
        self.assertNotIn("not-included", evidence["sanitized_log"])

    def test_saved_report_contains_only_sanitized_evidence(self) -> None:
        metadata = PngInfo()
        metadata.add_text("customer", "jane@example.com")
        image_buffer = BytesIO()
        Image.new("RGB", (16, 16), "white").save(image_buffer, format="PNG", pnginfo=metadata)
        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(reports, "REPORTS_PATH", Path(temporary_directory)),
        ):
            report = reports.save_report(
                title="Payment issue",
                description="The payment failed.",
                issue_draft="Summary\nPayment issue",
                knowledge_ids=["KB-1042"],
                attempted_steps=[],
                associate_confirmed=True,
                screenshot=image_buffer.getvalue(),
                screenshot_type="image/png",
                console_log=b"Authorization: Bearer account-secret\nemail jane@example.com",
            )
            report_path = Path(temporary_directory) / report["report_id"]
            persisted_report = json.loads((report_path / "report.json").read_text())
            sanitized_log = (report_path / persisted_report["diagnostic_log_name"]).read_text()

            self.assertNotIn("account-secret", sanitized_log)
            self.assertNotIn("jane@example.com", sanitized_log)
            self.assertGreaterEqual(persisted_report["evidence_redactions"], 2)
            with Image.open(report_path / persisted_report["screenshot_name"]) as sanitized_image:
                self.assertNotIn("customer", sanitized_image.info)

    def test_customer_linked_report_persists_customer_reference(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(reports, "REPORTS_PATH", Path(temporary_directory)),
            patch("smartissue.reports.jira_configured", return_value=False),
        ):
            report = reports.save_report(
                title="Address update did not save",
                description="The customer address change returned an error.",
                issue_draft="Summary\nAddress update did not save",
                knowledge_ids=["KB-0712"],
                attempted_steps=["Confirmed the submitted postcode."],
                associate_confirmed=True,
                customer_id="CUST-1042",
                application="Customer services · Web",
            )
            persisted_report = json.loads(
                (Path(temporary_directory) / report["report_id"] / "report.json").read_text(encoding="utf-8")
            )

        self.assertEqual(persisted_report["customer_id"], "CUST-1042")
        self.assertEqual(persisted_report["application"], "Customer services · Web")
        self.assertEqual(persisted_report["knowledge_ids"], ["KB-0712"])

    def test_sanitized_evidence_pack_is_forwarded_to_jira_agent(self) -> None:
        event = make_demo_error_event("Bank transfer")
        connector = ApprovedHostAppConnector(application=event.application, source=event.source)
        connected_event = connector.attach_diagnostic_log(
            event,
            b"Authorization: Bearer connector-secret\nerror=transfer failed\n",
        )
        image_buffer = BytesIO()
        Image.new("RGB", (32, 32), "white").save(image_buffer, format="PNG")

        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(reports, "REPORTS_PATH", Path(temporary_directory)),
            patch("smartissue.jira.create_jira_issue", return_value={"key": "BANK-EVIDENCE", "warning": None}) as create_jira,
            patch("smartissue.reports.jira_configured", return_value=True),
        ):
            report = reports.save_report(
                title=connected_event.title,
                description=connected_event.description,
                issue_draft="Summary\nTransfer remains pending.",
                knowledge_ids=[],
                attempted_steps=[],
                associate_confirmed=True,
                screenshot=image_buffer.getvalue(),
                screenshot_type="image/png",
                console_log=connected_event.diagnostic_log,
                diagnostics={"associate_id": "associate-42"},
            )

            jira_arguments = create_jira.call_args.kwargs
            screenshot_path = jira_arguments["screenshot_path"]
            diagnostic_log_path = jira_arguments["diagnostic_log_path"]
            self.assertEqual(report["jira_key"], "BANK-EVIDENCE")
            self.assertTrue(screenshot_path.is_file())
            self.assertTrue(diagnostic_log_path.is_file())
            self.assertNotIn("connector-secret", diagnostic_log_path.read_text(encoding="utf-8"))

    def test_jira_agent_blocks_without_associate_confirmation(self) -> None:
        graph = build_jira_agent()
        with patch("smartissue.jira.create_jira_issue") as create_jira:
            result = graph.invoke({"associate_confirmed": False, "associate_id": "", "title": "Ignored"})
            self.assertEqual(result["status"], "blocked")
            create_jira.assert_not_called()

    def test_associate_confirmation_creates_configured_jira_issue(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(reports, "REPORTS_PATH", Path(temporary_directory)),
            patch("smartissue.jira.create_jira_issue", return_value={"key": "BANK-TEST", "warning": None}) as create_jira,
            patch("smartissue.reports.jira_configured", return_value=True),
        ):
            report = reports.save_report(
                title="Payment issue",
                description="Pending payment for customer 4111 1111 1111 1111",
                issue_draft="Summary\nPayment issue\n\nDetails\nPending payment.",
                knowledge_ids=["KB-1042"],
                attempted_steps=["Checked transaction timeline."],
                associate_confirmed=True,
            )

            self.assertEqual(report["status"], "jira_created")
            self.assertEqual(report["jira_key"], "BANK-TEST")
            self.assertNotIn("4111 1111", report["description"])
            create_jira.assert_called_once()

            saved = json.loads((Path(temporary_directory) / report["report_id"] / "report.json").read_text())
            self.assertEqual(saved["status"], "jira_created")

    def test_unconfirmed_report_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "associate must confirm"):
            reports.save_report(
                title="Payment issue",
                description="Pending payment.",
                issue_draft="Summary\nPayment issue",
                knowledge_ids=[],
                attempted_steps=[],
                associate_confirmed=False,
            )

    def test_rejected_report_never_calls_jira(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(reports, "REPORTS_PATH", Path(temporary_directory)),
            patch("smartissue.jira.create_jira_issue") as create_jira,
        ):
            report = reports.save_report(
                title="Unexpected screen",
                description="The screen refreshed.",
                issue_draft="Summary\nUnexpected screen",
                knowledge_ids=[],
                attempted_steps=[],
                associate_confirmed=True,
            )
            rejected = reports.reject_report(report["report_id"], "Reviewer B", "Expected behavior")
            self.assertEqual(rejected["status"], "rejected")
            self.assertEqual(rejected["review_reason"], "Expected behavior")
            create_jira.assert_not_called()


if __name__ == "__main__":
    unittest.main()