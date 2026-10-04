# RAG Solution

## Overview

ResolveDesk implements a local retrieval-augmented knowledge lookup for associate support. Knowledge articles are read from `data/knowledge_base.json`, normalized and redacted, split into typed JSON chunks, embedded with `sentence-transformers/all-MiniLM-L6-v2`, and stored in persistent Chroma. At query time, matching chunks are grouped back into their source articles and shown in the Resolution insights and Knowledge base views.

The main implementation is in `smartissue/agent.py`. The knowledge base is created by `get_knowledge_base()` and cached for the running Streamlit process using the source JSON fingerprint, so source changes are synchronized on the next app rerun.

## Data Ingestion

Each article must be a JSON object with non-empty string fields `id`, `title`, `summary`, `category`, and `updated`, plus a `steps` array containing strings. Ingestion rejects malformed records, duplicate IDs, and an empty source file. Text is normalized, length-limited, and passed through the existing email and numeric-identifier redaction before embedding.

Each article is converted into versioned, field-aware JSON chunks:

```json
{
  "chunk_type": "article_overview",
  "chunk_schema_version": 1,
  "source_id": "KB-1042",
  "title": "Card payment declined after verification",
  "category": "Payments",
  "updated": "2026-10-01",
  "summary": "Review a declined card payment without creating duplicate authorisations or holds."
}
```

Each resolution step is a separate `resolution_step` chunk with the same source metadata and a `step_index`. Keeping overview and step chunks separate allows retrieval to match either the article summary or a specific resolution instruction. Search results retain the source article ID and matched chunk IDs for traceability.

The Chroma collection name includes the chunk schema version and embedding-model hash. Chunk IDs are stable for a given article and chunk position. SHA-256 hashes detect content changes. Ingestion batches changed chunks into `upsert` operations and deletes chunks no longer present in the source. Re-ingesting unchanged data therefore does not re-embed it. An empty JSON source is rejected to avoid accidentally clearing a populated index.

The app hashes the knowledge-base JSON to key its cached resource. After editing the JSON file, the next app rerun loads the new source and synchronizes changed or removed chunks into Chroma.

## Retrieval

1. Normalize and redact the associate's query.
2. Embed the query with the same model used for ingestion.
3. Query Chroma using cosine distance. The query over-fetches candidates (up to 64 chunks) so multiple chunks from one article do not consume the full result limit.
4. Convert distance to a similarity score and discard results below `MIN_RETRIEVAL_SCORE` (default `0.42`).
5. Optionally filter by article category, then group chunk hits by source article and rank each article by its best chunk score.
6. Return matched resolution steps when step chunks matched; otherwise return the article's steps. Results include source ID, chunk IDs, matched chunk types, score, and token count.
7. Enforce `MAX_CONTEXT_TOKENS` (default `420`) and `MAX_RESULTS` (default `4`) before returning results.

The application displays the retrieved support articles and steps to the associate. The issue-summary agent drafts a factual bug report from the associate's issue details and attempted steps; it does not currently use retrieved knowledge as generation context. This is retrieval-backed support guidance, not a generated answer chain.

## Configuration

Relevant settings are in `.env` (see `.env.example`):

- `EMBEDDING_MODEL_ID`: embedding model; defaults to `sentence-transformers/all-MiniLM-L6-v2`.
- `ALLOW_MODEL_DOWNLOADS`: allow model download on first run; set to `false` when the model is pre-provisioned for offline use.
- `MODEL_CACHE_PATH`: local model cache; defaults to `.data/models`.
- `CHROMA_PATH`: persistent Chroma directory; defaults to `.data/chroma`.
- `MAX_CONTEXT_TOKENS`: context budget; defaults to `420`.
- `MIN_RETRIEVAL_SCORE`: minimum similarity score; defaults to `0.42`.

The embedding model and vector index run locally on CPU in this prototype. Chroma is configured for cosine distance and local persistence.

## Validation

Run the workflow and RAG tests with:

```powershell
python -m unittest discover -s tests -v
```

RAG tests use an isolated in-memory Chroma client and a deterministic fake embedder. They verify JSON chunk shape, idempotent ingestion, update and deletion reconciliation, retrieval grouping, category filtering, duplicate-ID rejection, and empty-source protection. They validate pipeline behavior, not semantic relevance of the production embedding model against a labeled support-query dataset.

## Production Boundaries

This is a production-oriented local implementation, not a complete bank-scale RAG service. Before deployment in a regulated or multi-instance environment, add and validate:

- A labeled retrieval evaluation set with relevance metrics and a controlled process for tuning the score threshold.
- Operational monitoring for ingestion failures, embedding latency, retrieval scores, empty-result rates, and index health.
- Durable, backed-up vector storage and an explicit deployment strategy for concurrent workers and index updates.
- Authentication, authorization, and tenant or application scoping for knowledge sources and retrieval filters.
- A source-refresh operation or scheduled ingestion job if knowledge must update while the Streamlit process remains running.
- Approved data-retention, redaction, and audit policies for the actual support corpus.

The new versioned JSON collection is separate from the earlier monolithic collection. Existing Chroma data is not deleted automatically.
Configure OpenRouter credentials only in the ignored project-root `.env` file; never include API keys in documentation.