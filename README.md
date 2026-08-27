# rag — lokálny Graph-RAG nad PDF

Plne lokálny RAG systém: PDF (aj skenované, SK+EN) → štruktúrny parsing → hybrid
vyhľadávanie + znalostný graf → odpovede malého lokálneho LLM s citáciami na strany.
Nula cloudu, nula Dockera. Architektúra a odôvodnenie každej voľby: [PLAN.md](PLAN.md).

## Stack

| Vrstva | Technológia |
|---|---|
| PDF parsing + tabuľky | Docling (TableFormer) + OCR (ocrmac / Tesseract `slk`) |
| Chunking | Docling HybridChunker (štruktúrne vedomý) |
| Vektory + BM25 | LanceDB (embedded) — dense + natívny FTS |
| Embeddingy | bge-m3 (high profil) / multilingual-e5-small (low profil) |
| Graf | NetworkX + SQLite — koncepty, veličiny, citácie, SAME_AS cross-doc |
| LLM | Ollama: qwen3:4b-instruct (odpovede), qwen3:1.7b (extrakcia, sumáre) |
| Backend | FastAPI + uv, SSE streaming |
| Frontend | Vite + React + TS + shadcn/ui, react-force-graph, react-pdf |

## Setup

```bash
brew install ollama tesseract tesseract-lang   # tesseract = OCR fallback so slovenčinou
make models                                    # stiahne qwen3 modely (~4 GB)
cd backend && uv sync && cd ../frontend && pnpm install
```

## Beh

```bash
make dev        # backend :8000 + frontend :5173 (+ ollama serve ak nebeží)
```

Otvor http://localhost:5173 — nahraj PDF v **Dokumenty**, pýtaj sa v **Chat**
(citácie klikajú na stranu PDF), prepojenia skúmaj v **Graf**.

## Konfigurácia (env / `backend/.env`, prefix `RAG_`)

| Premenná | Default | Význam |
|---|---|---|
| `RAG_RAM_PROFILE` | `auto` | `low` (8–16 GB) / `high` (32 GB+): embeddingy, TableFormer režim |
| `RAG_EMBEDDING_MODEL` | podľa profilu | override embedding modelu (zmena = reindex) |
| `RAG_ANSWER_MODEL` | `qwen3:4b-instruct` | model odpovedí |
| `RAG_EXTRACT_MODEL` | `qwen3:1.7b` | model extrakcie entít a sumárov tabuliek |
| `RAG_GRAPH_EXPANSION` | `true` | A/B vypínač grafovej vrstvy retrievalu |
| `RAG_CHUNK_MAX_TOKENS` | `450` | veľkosť chunkov |

## Testy a eval

```bash
make test                      # pytest + frontend build
make lint                      # ruff + mypy (strict)
RUN_DOCLING=1 uv run pytest backend/tests/test_ingest_real.py   # reálny Docling parse
make eval                      # golden sada proti bežiacemu backendu (make backend)
```

Eval meria hit-rate@8, presnosť čísel v odpovediach, platnosť citácií a
cross-doc pokrytie relačných otázok; výsledky v `backend/eval/results.json`.
Golden sada beží nad generovaným vzorkovým korpusom — po nasadení vlastných PDF
nahraď otázky v `backend/eval/golden.py`.

## Ako to funguje (skratka)

1. **Ingest**: Docling → DoclingDocument JSON (štruktúra + provenance strana/bbox);
   tabuľky zvlášť (markdown + parquet — čísla nikdy nejdú cez LLM).
2. **Index**: štruktúrne chunky + LLM sumáre tabuliek → bge-m3 vektory + BM25 v LanceDB.
3. **Graf**: deterministické extraktory (veličiny, DOI/normy) + qwen3:1.7b (pojmy,
   structured output) → NetworkX; SAME_AS spája koncepty naprieč PDF (aj SK↔EN).
4. **Query**: dense + BM25 + grafová expanzia → RRF fúzia → table routing (plná
   tabuľka do kontextu) → qwen3:4b-instruct streamuje odpoveď s citáciami [n],
   ktoré backend deterministicky mapuje na dokument + stranu.
