from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import chromadb
import requests
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_PATH = ROOT / "data" / "knowledge_base.json"
MODEL_ID = os.getenv("EMBEDDING_MODEL_ID", "sentence-transformers/all-MiniLM-L6-v2")
CONTEXT_BUDGET = int(os.getenv("MAX_CONTEXT_TOKENS", "420"))
MAX_RESULTS = 4
MIN_RETRIEVAL_SCORE = float(os.getenv("MIN_RETRIEVAL_SCORE", "0.42"))
OPENROUTER_DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
INGEST_BATCH_SIZE = 64
CHROMA_READ_PAGE_SIZE = 1000
JSON_CHUNK_SCHEMA_VERSION = 1


def redact_sensitive_text(value: str) -> str:
    value = re.sub(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[redacted email]", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:\d[ -]?){8,19}\b", "[redacted number]", value)
    return re.sub(r"\b(?:\+?\d[\d(). -]{6,}\d)\b", "[redacted phone]", value)


def clean_text(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", redact_sensitive_text(value)).strip()[:limit]


def normalize_knowledge_articles(raw_articles: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_articles, list):
        raise ValueError("Knowledge base JSON must contain an array of articles.")

    articles = []
    seen_ids: set[str] = set()
    for index, raw_article in enumerate(raw_articles):
        if not isinstance(raw_article, dict):
            raise ValueError(f"Knowledge base article {index} must be a JSON object.")
        for field in ("id", "title", "summary", "category", "updated"):
            if not isinstance(raw_article.get(field), str) or not raw_article[field].strip():
                raise ValueError(f"Knowledge base article {index} requires a non-empty '{field}' string.")
        raw_steps = raw_article.get("steps")
        if not isinstance(raw_steps, list) or any(not isinstance(step, str) for step in raw_steps):
            raise ValueError(f"Knowledge base article {index} requires a 'steps' array of strings.")

        article_id = clean_text(raw_article["id"], 120)
        if article_id in seen_ids:
            raise ValueError(f"Knowledge base contains duplicate article id '{article_id}'.")
        seen_ids.add(article_id)
        normalized = {
            "id": article_id,
            "title": clean_text(raw_article["title"], 160),
            "summary": clean_text(raw_article["summary"], 1200),
            "category": clean_text(raw_article["category"], 100),
            "updated": clean_text(raw_article["updated"], 100),
            "steps": [],
        }
        if any(not normalized[field] for field in ("id", "title", "summary", "category", "updated")):
            raise ValueError(f"Knowledge base article {index} has a required field that is empty after sanitization.")
        for step in raw_steps:
            cleaned_step = clean_text(step, 800)
            if cleaned_step:
                normalized["steps"].append(cleaned_step)
        articles.append(normalized)
    return articles


def chunk_knowledge_article(article: dict[str, Any]) -> list[dict[str, Any]]:
    article_hash = hashlib.sha256(
        json.dumps(article, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    safe_id = hashlib.sha256(article["id"].encode("utf-8")).hexdigest()[:24]
    shared_fields = {
        "source_id": article["id"],
        "title": article["title"],
        "category": article["category"],
        "updated": article["updated"],
    }
    chunks = [
        {
            "id": f"{safe_id}:overview",
            "document": json.dumps(
                {
                    "chunk_type": "article_overview",
                    "chunk_schema_version": JSON_CHUNK_SCHEMA_VERSION,
                    **shared_fields,
                    "summary": article["summary"],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "metadata": {
                "article_id": article["id"],
                "chunk_type": "article_overview",
                "chunk_index": 0,
                "article_hash": article_hash,
                "title": article["title"],
                "summary": article["summary"],
                "category": article["category"],
                "updated": article["updated"],
            },
        }
    ]
    for step_index, step in enumerate(article["steps"]):
        chunks.append(
            {
                "id": f"{safe_id}:step:{step_index:05d}",
                "document": json.dumps(
                    {
                        "chunk_type": "resolution_step",
                        "chunk_schema_version": JSON_CHUNK_SCHEMA_VERSION,
                        **shared_fields,
                        "step_index": step_index,
                        "step": step,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "metadata": {
                    "article_id": article["id"],
                    "chunk_type": "resolution_step",
                    "chunk_index": step_index + 1,
                    "step_index": step_index,
                    "article_hash": article_hash,
                    "title": article["title"],
                    "summary": article["summary"],
                    "category": article["category"],
                    "updated": article["updated"],
                },
            }
        )
    for chunk in chunks:
        chunk["metadata"]["chunk_hash"] = hashlib.sha256(chunk["document"].encode("utf-8")).hexdigest()
    return chunks


class LocalKnowledgeBase:
    def __init__(
        self,
        *,
        knowledge_path: Path | None = None,
        model: Any | None = None,
        tokenizer: Any | None = None,
        client: Any | None = None,
    ) -> None:
        allow_downloads = os.getenv("ALLOW_MODEL_DOWNLOADS", "true").lower() == "true"
        model_cache = Path(os.getenv("MODEL_CACHE_PATH", str(ROOT / ".data" / "models")))
        if model is None or tokenizer is None:
            model_cache.mkdir(parents=True, exist_ok=True)
        self.model = model or SentenceTransformer(
            MODEL_ID,
            device="cpu",
            cache_folder=str(model_cache),
            local_files_only=not allow_downloads,
        )
        self.tokenizer = tokenizer or AutoTokenizer.from_pretrained(
            MODEL_ID,
            cache_dir=str(model_cache),
            local_files_only=not allow_downloads,
        )
        if client is None:
            chroma_path = Path(os.getenv("CHROMA_PATH", str(ROOT / ".data" / "chroma")))
            chroma_path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=str(chroma_path),
                settings=Settings(anonymized_telemetry=False),
            )
        self.client = client
        self.knowledge_path = knowledge_path or KNOWLEDGE_PATH
        self.articles: dict[str, dict[str, Any]] = {}
        model_hash = hashlib.sha256(MODEL_ID.encode("utf-8")).hexdigest()[:10]
        collection_name = f"support_json_v{JSON_CHUNK_SCHEMA_VERSION}_{model_hash}"
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={
                "hnsw:space": "cosine",
                "embedding_model": MODEL_ID,
                "chunk_schema_version": JSON_CHUNK_SCHEMA_VERSION,
            },
        )
        self.last_ingest_stats = self.ingest()

    def _read_articles(self) -> list[dict[str, Any]]:
        try:
            source = json.loads(self.knowledge_path.read_text(encoding="utf-8"))
        except OSError as error:
            raise RuntimeError(f"Could not read knowledge base at {self.knowledge_path}.") from error
        except json.JSONDecodeError as error:
            raise ValueError(f"Knowledge base JSON is invalid at line {error.lineno}, column {error.colno}.") from error
        return normalize_knowledge_articles(source)

    def _existing_chunks(self) -> dict[str, dict[str, Any]]:
        chunks: dict[str, dict[str, Any]] = {}
        offset = 0
        while True:
            page = self.collection.get(
                limit=CHROMA_READ_PAGE_SIZE,
                offset=offset,
                include=["metadatas"],
            )
            ids = page.get("ids", [])
            metadatas = page.get("metadatas", [])
            chunks.update(zip(ids, metadatas, strict=False))
            if len(ids) < CHROMA_READ_PAGE_SIZE:
                return chunks
            offset += len(ids)

    def ingest(self) -> dict[str, int]:
        articles = self._read_articles()
        if not articles:
            raise ValueError("Knowledge base JSON must contain at least one article; refusing to clear the index.")
        self.articles = {article["id"]: article for article in articles}
        chunks = [chunk for article in articles for chunk in chunk_knowledge_article(article)]
        existing = self._existing_chunks()
        chunk_ids = {chunk["id"] for chunk in chunks}
        changed_chunks = [
            chunk
            for chunk in chunks
            if existing.get(chunk["id"], {}).get("chunk_hash") != chunk["metadata"]["chunk_hash"]
        ]

        for offset in range(0, len(changed_chunks), INGEST_BATCH_SIZE):
            batch = changed_chunks[offset : offset + INGEST_BATCH_SIZE]
            encoded = self.model.encode(
                [chunk["document"] for chunk in batch],
                normalize_embeddings=True,
                batch_size=INGEST_BATCH_SIZE,
            )
            embeddings = encoded.tolist() if hasattr(encoded, "tolist") else encoded
            self.collection.upsert(
                ids=[chunk["id"] for chunk in batch],
                documents=[chunk["document"] for chunk in batch],
                embeddings=embeddings,
                metadatas=[chunk["metadata"] for chunk in batch],
            )

        stale_ids = [chunk_id for chunk_id in existing if chunk_id not in chunk_ids]
        for offset in range(0, len(stale_ids), INGEST_BATCH_SIZE):
            self.collection.delete(ids=stale_ids[offset : offset + INGEST_BATCH_SIZE])

        return {
            "source_articles": len(articles),
            "total_chunks": len(chunks),
            "upserted_chunks": len(changed_chunks),
            "deleted_chunks": len(stale_ids),
        }

    def count_tokens(self, value: str) -> int:
        return len(self.tokenizer.encode(value, add_special_tokens=True))

    def search(
        self,
        query: str,
        limit: int = MAX_RESULTS,
        *,
        category: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        query = clean_text(query, 900)
        if not query or limit <= 0:
            return [], 0
        collection_count = self.collection.count()
        if collection_count == 0:
            return [], 0
        query_vector = self.model.encode([query], normalize_embeddings=True)
        query_embeddings = query_vector.tolist() if hasattr(query_vector, "tolist") else query_vector
        query_options: dict[str, Any] = {
            "query_embeddings": query_embeddings,
            "n_results": min(collection_count, min(64, max(limit, limit * 8))),
            "include": ["metadatas", "distances"],
        }
        if category:
            query_options["where"] = {"category": clean_text(category, 100)}
        result = self.collection.query(**query_options)
        ids = result.get("ids", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        grouped: dict[str, dict[str, Any]] = {}

        for chunk_id, metadata, distance in zip(ids, metadatas, distances, strict=False):
            score = max(0.0, min(1.0, 1.0 - float(distance)))
            if score < MIN_RETRIEVAL_SCORE:
                continue
            article_id = metadata["article_id"]
            if article_id not in self.articles:
                continue
            match = grouped.setdefault(
                article_id,
                {"score": score, "chunk_ids": [], "matched_chunks": set(), "step_indices": set()},
            )
            match["score"] = max(match["score"], score)
            match["chunk_ids"].append(chunk_id)
            match["matched_chunks"].add(metadata["chunk_type"])
            if metadata["chunk_type"] == "resolution_step":
                match["step_indices"].add(int(metadata["step_index"]))

        candidates = sorted(grouped.items(), key=lambda item: item[1]["score"], reverse=True)
        matches: list[dict[str, Any]] = []
        context_tokens = 0

        for article_id, match in candidates:
            article = self.articles[article_id]
            matched_steps = sorted(match["step_indices"])
            steps = (
                [article["steps"][index] for index in matched_steps]
                if matched_steps
                else article["steps"]
            )
            context = json.dumps(
                {
                    "source_id": article_id,
                    "title": article["title"],
                    "summary": article["summary"],
                    "steps": steps,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            token_count = self.count_tokens(context)
            if context_tokens + token_count > CONTEXT_BUDGET:
                continue
            context_tokens += token_count
            matches.append(
                {
                    "id": article_id,
                    "source_id": article_id,
                    "title": article["title"],
                    "summary": article["summary"],
                    "category": article["category"],
                    "updated": article["updated"],
                    "steps": steps,
                    "score": round(match["score"], 4),
                    "chunk_ids": match["chunk_ids"],
                    "matched_chunks": sorted(match["matched_chunks"]),
                    "token_count": token_count,
                }
            )
            if len(matches) >= limit:
                break

        return matches, context_tokens


@lru_cache(maxsize=1)
def get_knowledge_base(knowledge_source_hash: str = "") -> LocalKnowledgeBase:
    """Cache the local index against the source JSON fingerprint."""
    return LocalKnowledgeBase()


def build_issue_draft(state: dict[str, Any], knowledge_base: LocalKnowledgeBase) -> tuple[str, str]:
    attempted_steps = [clean_text(step, 300) for step in state.get("attempted_steps", []) if step.strip()]
    knowledge_sources = [
        {
            "source_id": match.get("source_id", match.get("id", "")),
            "title": match.get("title", ""),
            "summary": match.get("summary", ""),
            "relevant_steps": match.get("steps", []),
        }
        for match in state.get("matches", [])
    ]
    user_facts = json.dumps(
        {
            "title": state.get("title", ""),
            "description": state.get("description", ""),
            "attempted_steps": attempted_steps,
            "application": state.get("application", "Payments web application"),
            "workflow": state.get("workflow", ""),
            "error_code": state.get("error_code", ""),
            "application_event_id": state.get("application_event_id", ""),
            "retrieved_support_sources": knowledge_sources,
        },
        ensure_ascii=False,
    )

    openrouter_failure = ""
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if state.get("hosted_model_consent") and openrouter_api_key:
        model_id = os.getenv("OPENROUTER_MODEL", OPENROUTER_DEFAULT_MODEL).strip() or OPENROUTER_DEFAULT_MODEL
        base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:8501"),
                    "X-Title": os.getenv("OPENROUTER_APP_NAME", "SmartIssue"),
                },
                json={
                    "model": model_id,
                    "temperature": 0,
                    "max_tokens": 500,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Draft a concise production bug report using only the supplied facts. "
                                "Do not invent behavior, identifiers, expected results, or resolution steps. "
                                "Use headings Summary, Issue details, Steps tried, Expected, Actual, "
                                "Environment, and Related support references. Preserve support source IDs. "
                                "Clearly state when expected behavior is unknown."
                            ),
                        },
                        {"role": "user", "content": user_facts},
                    ],
                },
                timeout=30,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if isinstance(content, str) and content.strip():
                return content.strip(), f"OpenRouter ({model_id})"
            raise ValueError("OpenRouter returned an empty issue draft.")
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as error:
            status_code = getattr(getattr(error, "response", None), "status_code", None)
            openrouter_failure = f"HTTP {status_code}" if status_code else type(error).__name__
            logging.getLogger(__name__).warning(
                "OpenRouter issue drafting failed (%s); using a configured local or fact-only fallback.",
                openrouter_failure,
            )

    if os.getenv("OLLAMA_MODEL"):
        try:
            from langchain_ollama import ChatOllama

            model = ChatOllama(
                model=os.environ["OLLAMA_MODEL"],
                base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                temperature=0,
                num_predict=260,
            )
            response = model.invoke(
                "Create a concise production bug report using only the supplied facts. "
                "Do not guess missing details. Preserve retrieved support reference IDs. "
                "Use headings Summary, Issue details, Steps tried, Expected, Actual, "
                "Environment, and Related support references.\n\n"
                f"Facts and retrieved references: {user_facts}"
            )
            content = response.content
            if isinstance(content, str) and content.strip():
                return content.strip(), "Ollama (local)"
        except (OSError, RuntimeError, ValueError, TimeoutError) as error:
            logging.getLogger(__name__).warning(
                "Local Ollama issue drafting failed (%s); using a fact-only fallback.",
                type(error).__name__,
            )

    steps_text = "\n".join(f"- {step}" for step in attempted_steps) or "- No resolution steps marked as tried."
    draft = (
        f"Summary\n{state['title']}\n\n"
        f"Issue details\n{state['description']}\n\n"
        f"Steps tried\n{steps_text}\n\n"
        "Expected\nExpected behavior was not supplied by the associate; confirm it with the application owner.\n\n"
        "Actual\nThe associate reports the behavior described above.\n\n"
        f"Environment\n{state.get('application', 'Payments web application')}\n"
        f"Workflow\n{state.get('workflow') or 'Not provided'}\n"
        f"Error code\n{state.get('error_code') or 'Not provided'}\n"
        f"Application event\n{state.get('application_event_id') or 'Not provided'}\n\n"
        f"Related support references\n{', '.join(source['source_id'] for source in knowledge_sources if source['source_id']) or 'None'}"
    )
    provider = f"Fact-only template (OpenRouter {openrouter_failure})" if openrouter_failure else "Fact-only template"
    return draft[: max(1200, knowledge_base.count_tokens(draft) * 5)], provider