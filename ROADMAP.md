# Roadmap

Goal: a local, private RAG that **finds connections between documents** and answers from them with
verifiable citations — and turns those answers into branded documents on request.

Stack stays Python (FastAPI) + React. Everything runs locally (Ollama, LanceDB, SQLite); hosted
LLM APIs are optional.

Status: ✅ done · 🚧 in progress · ⬜ planned

## Done before this roadmap

- Docling parsing with OCR (Apple Vision / Tesseract), table extraction to markdown + parquet
- Hybrid retrieval: dense + BM25 + knowledge-graph expansion, fused with RRF
- Query router: `lookup` (RAG), `filter` (SQL over table records), `compose` (plan + exact solver,
  agent fallback with tool calling)
- Number self-check: every number in an answer must exist in the evidence
- Streaming chat with `[n]` citations that open the PDF at the cited page; graph explorer

## Document generation ✅

- ✅ Company settings page (shadcn): company details, header/footer text, live + PDF preview;
  document look comes from the app theme (system-ui + JetBrains Mono, orange accent), not per company
  (address, IČO, DIČ, IČ DPH, IBAN, contact), live preview and exact PDF preview
- ✅ `generate` route: "Priprav report…", "Vytvor cenovú ponuku…" in chat → outline (JSON schema)
  → sections written one by one from retrieved sources → branded PDF (WeasyPrint)
- ✅ Templates: free-form, report, offer, memo, document summary (SK + EN)
- ✅ Citations in the PDF link to a source list; numbers are checked against the evidence
- ✅ Sandboxed renderer: LLM output cannot load remote URLs or local files into the PDF

## Tier 1 — measurable quality

1. ✅ **Evaluation suite**
   - Synthetic but non-trivial corpus: a dozen interlinked construction-company documents
     (contracts, budgets, site diaries, inspection reports) plus near-duplicate distractors
   - 100+ golden questions with gold document + page labels: factual, table, cross-document,
     multi-hop, unanswerable
   - Retrieval metrics: recall@k, MRR, nDCG@10. Generation metrics: faithfulness and answer
     relevance (LLM-as-judge), citation validity, abstention on unanswerable questions
   - **Ablation table** in the README: dense → hybrid → +graph → +rerank → +contextual,
     with latency per configuration
   - Regression gate: fails when a metric drops below the committed baseline
2. ✅ **Reranker** — multilingual cross-encoder over the fused top candidates (flag `RAG_RERANK`)
3. ✅ **Contextual retrieval** — an LLM writes a one-line context for every chunk at ingest;
   it is prepended for both embeddings and BM25 (Anthropic's technique)
4. ✅ **MCP server** — `search_documents`, `find_connections`, `ask`, `get_document`,
   `list_documents`, `query_records`; stdio and streamable HTTP
5. ✅ **Observability** — OpenTelemetry spans with GenAI semantic conventions for every stage
   (route, retrieve, rerank, LLM calls with token counts); built-in trace viewer, optional OTLP
   export to Langfuse / Phoenix
6. ✅ **Runnable demo** — Docker Compose, LLM provider abstraction (Ollama or any
   OpenAI-compatible API), English README with architecture diagram, CI (tests, lint, types,
   build)

## Tier 2 — visible quality

7. ✅ **Conversation memory** — follow-up questions are rewritten into standalone queries
8. ✅ **Exact citation highlight** — Docling bounding boxes stored per chunk; the PDF viewer
   highlights the cited passage
9. ✅ **Sentence-level verification** — every sentence is checked against its cited sources;
   unsupported sentences are flagged in the UI
10. ✅ **More formats and document management** — DOCX, PPTX, HTML, Markdown via Docling; delete
    documents; watched inbox folder for incremental sync
11. ✅ **Feedback loop** — thumbs up/down linked to the trace; bad answers export to the golden set

## Tier 3 — enterprise and extras

12. ✅ **Access control** — users and workspaces, permission filtering inside the vector/BM25
    query, prompt-injection test set and defenses for retrieved content
13. ✅ **Deep research mode** — plan sub-questions, retrieve in parallel, synthesize a cited
    report (optionally exported as a branded PDF)
14. ✅ **Corpus-level questions** — concept communities over the knowledge graph with summaries
    (LazyGraphRAG-style) for "what are the main themes across all documents"
15. ✅ **Generic records** — the `filter`/`compose` path works with any numeric table attributes,
    not only the meal-plan demo domain
16. ✅ **Visual retrieval (experimental)** — ColQwen page-image embeddings for slide decks and
    scanned drawings, opt-in because of model size

## Company module — tasks, document import, voice attendance ✅

- ✅ Collapsible sidebar navigation; worker-only mobile shell
- ✅ Workers with PIN login (lockout, long-lived mobile session); owner sees everything,
  workers only their daily report (`WorkerGuard`)
- ✅ Sites, tasks with date ranges, dependencies and assignees; kanban board with one column
  per worker (drag to move, Alt+drag to add)
- ✅ Task import from supplier documents: local LLM proposes tasks with verified source quotes
  (exact PDF highlight), deterministic Slovak date parsing, dependency linking, assignment
  suggestion (named person → profession → on site → workload); owner approves; new document
  versions are diffed into new / changed / cancelled / unchanged
- ✅ Voice daily report: Whisper (MLX) → structured report; every time and number from the LLM
  is checked against what the worker said; missing data triggers follow-up questions (read
  aloud by Piper TTS, answered by voice or text, buttons after two unclear answers); submission
  is blocked server-side until nothing is missing
- ✅ Branded PDF per worker report and a daily company summary (auto at 19:00)
- ✅ Model eval (`eval/workforce_eval.py`): Whisper WER, report-extraction accuracy, import recall
