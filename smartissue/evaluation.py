from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "tests" / "eval" / "queries.json"


def evaluate(search: Callable[[str], list[dict[str, Any]]], cases: list[dict[str, Any]], k: int = 4) -> dict[str, Any]:
    """Score a retriever. A case with an empty `expected` list must return no results (abstain)."""
    positives = [case for case in cases if case["expected"]]
    negatives = [case for case in cases if not case["expected"]]
    hits = 0
    reciprocal_ranks = 0.0
    failures = []
    for case in positives:
        ids = [match["id"] for match in search(case["query"])][:k]
        rank = next((position for position, article_id in enumerate(ids, 1) if article_id in case["expected"]), None)
        if rank:
            hits += 1
            reciprocal_ranks += 1 / rank
        else:
            failures.append({"query": case["query"], "expected": case["expected"], "got": ids})
    false_positives = []
    for case in negatives:
        ids = [match["id"] for match in search(case["query"])][:k]
        if ids:
            false_positives.append({"query": case["query"], "got": ids})
    return {
        "k": k,
        "positive_cases": len(positives),
        "negative_cases": len(negatives),
        "recall_at_k": hits / len(positives) if positives else None,
        "mrr": reciprocal_ranks / len(positives) if positives else None,
        "abstain_rate": 1 - len(false_positives) / len(negatives) if negatives else None,
        "missed": failures,
        "false_positives": false_positives,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate retrieval against labeled queries.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("-k", type=int, default=4)
    parser.add_argument("--min-recall", type=float, default=0.0)
    parser.add_argument("--min-abstain", type=float, default=0.0)
    args = parser.parse_args()

    from smartissue.agent import LocalKnowledgeBase

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    knowledge_base = LocalKnowledgeBase()
    report = evaluate(lambda query: knowledge_base.search(query)[0], cases, args.k)
    print(json.dumps(report, indent=2))
    failed = (report["recall_at_k"] or 0) < args.min_recall or (report["abstain_rate"] or 0) < args.min_abstain
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
