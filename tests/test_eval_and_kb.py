import os
import unittest
from unittest import mock

from smartissue.evaluation import evaluate
from smartissue.jira_kb import build_article, extract_rca

RCA = "Root cause: Callback timed out.\nResolution:\n- Re-trigger validation\n- Escalate if pending"


def issue(**overrides):
    base = {
        "key": "MC-7",
        "summary": "Transfer pending",
        "resolved": "2026-10-05",
        "labels": ["kb-approved"],
        "description": "",
        "comments": [RCA],
        "comment_authors": ["acct-1"],
    }
    return {**base, **overrides}


class EvaluationMetricsTests(unittest.TestCase):
    def test_recall_mrr_and_abstention(self):
        results = {"a": [{"id": "KB-2"}, {"id": "KB-1"}], "b": [{"id": "KB-9"}], "c": [], "d": [{"id": "KB-3"}]}
        cases = [
            {"query": "a", "expected": ["KB-1"]},
            {"query": "b", "expected": ["KB-1"]},
            {"query": "c", "expected": []},
            {"query": "d", "expected": []},
        ]
        report = evaluate(lambda query: results[query], cases, k=4)
        self.assertEqual(report["recall_at_k"], 0.5)
        self.assertEqual(report["mrr"], 0.25)
        self.assertEqual(report["abstain_rate"], 0.5)
        self.assertEqual(report["false_positives"][0]["query"], "d")


class JiraApprovalGateTests(unittest.TestCase):
    def test_resolution_heading_does_not_truncate_root_cause(self):
        cause, steps = extract_rca(issue(comments=["Root cause: Data issues\nResolution: Search with validate customer id"]))
        self.assertEqual(cause, "Data issues")
        self.assertEqual(steps, ["Search with validate customer id"])

    def test_unapproved_issue_is_not_indexed(self):
        with mock.patch.dict(os.environ, {"JIRA_KB_APPROVAL_LABEL": "kb-approved", "JIRA_KB_TRUSTED_AUTHORS": ""}):
            self.assertIsNone(build_article(issue(labels=["bug"])))
            article = build_article(issue())
        self.assertEqual(article["id"], "KB-JIRA-MC-7")
        self.assertIn("approved", article["category"])

    def test_gate_can_be_disabled_and_is_labelled_unreviewed(self):
        with mock.patch.dict(os.environ, {"JIRA_KB_APPROVAL_LABEL": "", "JIRA_KB_TRUSTED_AUTHORS": ""}):
            article = build_article(issue(labels=[]))
        self.assertIn("unreviewed", article["category"])

    def test_only_trusted_authors_comments_count(self):
        env = {"JIRA_KB_APPROVAL_LABEL": "kb-approved", "JIRA_KB_TRUSTED_AUTHORS": "acct-trusted"}
        with mock.patch.dict(os.environ, env):
            self.assertIsNone(build_article(issue(description=RCA, comments=[RCA], comment_authors=["acct-other"])))
            self.assertIsNotNone(build_article(issue(comments=[RCA], comment_authors=["acct-trusted"])))


if __name__ == "__main__":
    unittest.main()
