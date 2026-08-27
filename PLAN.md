# Lokálny Graph-RAG pipeline — implementačný plán

**Dátum:** 2026-08-27
**Cieľ:** Plne lokálny RAG systém nad PDF dokumentmi (technické/vedecké, časť skenovaných, SK+EN mix) s grafovou vrstvou, ktorá nachádza súvislosti medzi dokumentmi a stranami, a s korektným spracovaním veľkých číselných tabuliek.
**Hardvér:** Apple Silicon Mac, 8 GB RAM — všetky voľby podriadené tomuto limitu.

---

## 1. Požiadavky (z čoho plán vychádza)

| # | Požiadavka | Kde ju plán rieši |
|---|-----------|-------------------|
| R1 | Beží úplne lokálne, žiadne cloud API | §3 — všetky modely lokálne (Ollama, fastembed ONNX), žiadny externý server |
| R2 | PDF parsing cez Docling alebo ekvivalent | §3.1, §5 — Docling + OCR pre skenované |
| R3 | Spracovanie štruktúry dokumentu | §5 — DoclingDocument (sekcie, nadpisy, reading order, provenance) |
| R4 | Graph RAG | §7 — entitný graf, NetworkX + SQLite |
| R5 | Kontext veľkých číselných tabuliek | §6 — tabuľky ako first-class objekty, sumár → embedding, plná tabuľka → kontext |
| R6 | Súvislosti medzi PDFkami a stranami | §7.3 — SAME_AS / CITES hrany s provenance (doc_id, page_no) |
| R7 | React frontend + shadcn | §10 — Vite + React + TS + shadcn/ui |
| R8 | Python backend | §9 — FastAPI |
| R9 | Lokálne AI, malý model | §3.4 — Qwen3 1.7B/4B cez Ollama |
| R10 | Odôvodnenie každej voľby | §3 — tabuľky „voľba / prečo / zamietnuté alternatívy" |

---

## 2. Architektúra

```
                        ┌──────────────────────────────┐
                        │  Frontend (Vite+React+shadcn)│
                        │  Chat · Dokumenty · Graf ·   │
                        │  Tabuľky · PDF náhľad        │
                        └──────────────┬───────────────┘
                                       │ REST + SSE
                        ┌──────────────▼───────────────┐
                        │      FastAPI backend         │
                        └──┬──────────┬───────────┬────┘
              INGEST       │          │ QUERY     │
        ┌──────────────────▼──┐   ┌───▼────────────────────┐
        │ Docling (+OCR)      │   │ Retrieval pipeline     │
        │  → DoclingDocument  │   │ 1. hybrid (dense+BM25) │
        │  → HybridChunker    │   │ 2. graf expanzia       │
        │  → tabuľky zvlášť   │   │ 3. RRF fúzia           │
        │  → LLM extrakcia    │   │ 4. table routing       │
        │    entít (Qwen3)    │   │ 5. Qwen3 odpoveď (SSE) │
        └──┬────────┬─────┬───┘   └───┬──────┬──────┬──────┘
           │        │     │           │      │      │
     ┌─────▼──┐ ┌───▼───┐ ┌▼──────────▼─┐ ┌──▼──────▼─┐
     │ SQLite │ │LanceDB│ │  NetworkX   │ │  Ollama   │
     │ meta + │ │vektory│ │  graf (+    │ │ qwen3:4b  │
     │ tabuľky│ │ +FTS  │ │  SQLite     │ │ qwen3:1.7b│
     │ + joby │ │(BM25) │ │  persist)   │ │           │
     └────────┘ └───────┘ └─────────────┘ └───────────┘
```

Všetko in-process okrem Ollamy (lokálny daemon). Nula Dockera, nula serverov na správu.

---

## 3. Technologické voľby a odôvodnenie

### 3.1 PDF parsing — **Docling**

**Prečo:**
- **TableFormer** model — najlepšia open-source extrakcia štruktúry tabuliek (riadky, stĺpce, spany, hlavičky). Presne požiadavka R5 — čísla v tabuľke ostávajú v správnych bunkách, nie rozsypaný text.
- **Provenance** — každý element nesie číslo strany + bounding box → citácie na úroveň strany (R6) a zvýraznenie v PDF náhľade.
- **Reading order + hierarchia** — sekcie, nadpisy, zoznamy → štruktúrne vedomý chunking (R3).
- **OCR integrovaný** — pluggable engine pre skenované PDF.
- Plne lokálny, aktívne vyvíjaný (IBM Research), lossless JSON export (DoclingDocument) — parsujeme raz, uložíme, chunking môžeme meniť bez re-parsu.

**Zamietnuté alternatívy:**
- *PyMuPDF* — rýchly, ale tabuľky extrahuje slabo (heuristiky, nie ML model), žiadna hierarchia dokumentu.
- *unstructured* — slabšia štruktúra tabuliek, ťažšie závislosti.
- *marker* — dobrý na markdown, ale tabuľky horšie než TableFormer a vyššie nároky na GPU.
- *MinerU* — kvalitný, ale ťažší (viac VRAM/RAM) — nevhodný na 8 GB.

### 3.2 OCR (skenované PDF) — **ocrmac, fallback Tesseract**

**Prečo:**
- *ocrmac* = Apple Vision framework — natívny, rýchly, takmer nulová RAM navyše (beží v systémovom procese), na Apple Silicon jednoznačne najefektívnejší.
- **Overiť pri implementácii:** podpora slovenčiny vo Vision API na tvojej verzii macOS. Ak chýba → **Tesseract** (`slk`+`eng` traineddata cez brew) — ľahký (C++, bez PyTorch), na čistých skenoch dostatočný.
- *Zamietnuté:* EasyOCR (PyTorch závislosť, ~1 GB RAM navyše — na 8 GB neúnosné), RapidOCR (slabšie pokrytie SK diakritiky).

### 3.3 Embeddingy — **multilingual-e5-small cez fastembed (ONNX)**

**Prečo:**
- SK+EN mix → nutný multilingválny model so **spoločným vektorovým priestorom** (slovenská otázka nájde anglický chunk a naopak).
- e5-small: 118M parametrov, int8 ONNX ~120 MB RAM, 384 dim — na 8 GB ideál.
- **fastembed** = ONNX runtime, žiadny PyTorch v procese → úspora ~1 GB+ RAM oproti sentence-transformers.
- **Vedomý kompromis:** bge-m3 je kvalitnejší (a má 8k kontext), ale ~10× väčší. Plán: config prepínač `EMBEDDING_MODEL`, eval sada (§14) rozhodne, či e5-small stačí. Chunky režeme ≤ 450 tokenov, takže 512-token limit e5 nevadí.
- E5 vyžaduje prefixy `query:` / `passage:` — zabudované v embedderi, nie v aplikačnom kóde.

### 3.4 LLM — **Ollama + Qwen3-4B (odpovede) / Qwen3-1.7B (extrakcia)**

**Prečo Ollama:**
- Metal akcelerácia na Apple Silicon bez konfigurácie, OpenAI-kompatibilné API, správa modelov (`ollama pull`), automatické uvoľňovanie modelu z RAM (`keep_alive`) — kritické pri 8 GB.
- *Zamietnuté:* llama.cpp priamo (viac práce, rovnaký výkon — Ollama ho používa pod kapotou), LM Studio (GUI, horšia skriptovateľnosť), vLLM/TGI (serverové, VRAM-orientované).

**Prečo Qwen3:**
- Najlepší pomer kvalita/veľkosť v triede ≤4B (2025), slušná slovenčina, **spoľahlivý štruktúrovaný JSON output** — nutný pre extrakciu entít, a `/no_think` režim pre rýchlu extrakciu.
- Deľba rolí kvôli RAM: **1.7B q4 (~1,4 GB)** na extrakciu počas indexovania (beží popri Doclingu), **4B q4_K_M (~2,6 GB)** na odpovede (pri query Docling nebeží). Nikdy nie oba naraz.
- *Zamietnuté:* Gemma-3-4B (lepšia SK próza, ale horší JSON a nemá deľbu think/no_think), Llama-3.2-3B (slabšia SK aj JSON), Phi-4-mini (slabá multilingválnosť).

### 3.5 Vektorová DB — **LanceDB**

**Prečo:**
- **Embedded** (ako SQLite pre vektory) — in-process, nula serverov, dáta = súbory na disku.
- **Hybrid search zabudovaný**: dense vektory + tantivy full-text index (BM25) v jednej DB → presné zhody termínov/čísel + sémantika. Pre technické texty (názvy metód, veličiny, konštanty) je BM25 zložka nutná.
- Metadata filtre (doc_id, typ chunku, strana) priamo v query.
- *Zamietnuté:* Qdrant (výborný, ale server — zbytočná réžia na 8 GB, vyriešené voľbou používateľa), Chroma (slabší hybrid/FTS), FAISS (holý index bez metadát a persistence — všetko okolo by sme písali sami).

### 3.6 Graf — **NetworkX + SQLite persistencia**

**Prečo:**
- Očakávaný rozsah (stovky PDF → nízke desiatky tisíc uzlov) sa pohodlne zmestí do pamäte; traverzály v Pythone sú pri tejto veľkosti okamžité.
- Nula infra, nula RAM réžie mimo samotného grafu. Persist: uzly/hrany v SQLite (zdroj pravdy), NetworkX ako in-memory view pri štarte.
- Vizualizáciu nesie frontend (react-force-graph), nie DB — Neo4j Browser tým stráca hlavný argument.
- *Zamietnuté:* Neo4j (JVM ~1 GB + Docker VM ~1,5–2 GB na 8 GB stroji neúnosné; Cypher pri našej veľkosti grafu nepotrebný), Kuzu (projekt archivovaný 2025 — mŕtva závislosť), Memgraph/FalkorDB (opäť server).
- **Vedomý kompromis:** ak korpus prerastie ~1M hrán, výmena store vrstvy za server DB je izolovaná v `graph/store.py`.

### 3.7 Reranker — **RRF fúzia default, neurálny reranker ako opt-in**

**Prečo:**
- bge-reranker-v2-m3 (multilingválny cross-encoder) je kvalitný, ale ~2 GB RAM — na 8 GB pri bežiacom LLM neúnosné by default.
- Default: **Reciprocal Rank Fusion** (dense poradie + BM25 poradie + grafové skóre) — nula RAM, robustný, bez ladenia váh.
- Config flag `RERANKER=bge-reranker-v2-m3-int8` pre stroje s väčšou RAM; eval sada zmeria prínos.

### 3.8 Backend — **FastAPI + uv + SQLite**

**Prečo:**
- FastAPI: async → SSE streaming tokenov, Pydantic v2 schémy zdieľané medzi API a LLM extrakciou (jedna definícia entity), OpenAPI docs zadarmo.
- uv: rýchly resolver, lockfile, jednoduchý `uv run`.
- SQLite: registry dokumentov, stav jobov, chat história, tabuľky, graf persist — jedna súborová DB, nula správy. WAL mód.
- **Žiadny LangChain/LlamaIndex:** pipeline má ~5 krokov, ktoré chceme presne kontrolovať (tabuľky, grafová schéma, RAM manažment). Framework by pridal vrstvu abstrakcie presne tam, kde potrebuješ rozumieť každému kroku (tvoja požiadavka R10), a ťahá desiatky závislostí. Vlastný kód = porovnateľný rozsah, plná kontrola.

### 3.9 Frontend — **Vite + React + TS + Tailwind + shadcn/ui**

**Prečo:**
- Backend je Python → Next.js (Node server, SSR) nemá čo robiť; SPA cez Vite je najjednoduchšia korektná voľba. shadcn/ui funguje s Vite plnohodnotne.
- shadcn/ui (požiadavka R7): copy-in komponenty, plná kontrola kódu, žiadna runtime závislosť.
- Doplnky: TanStack Query (server state), react-router, **react-force-graph-2d** (canvas — plynulý aj pri tisícoch uzlov, na rozdiel od SVG riešení), **react-pdf** (pdf.js wrapper — citácia → klik → konkrétna strana), TanStack Table (zobrazenie extrahovaných tabuliek).

---

## 4. Dátový model

### SQLite (`data/app.db`)
```
documents(id, filename, sha256, status, pages_count, lang_mix, created_at, error)
pages(doc_id, page_no, has_ocr, text_len)
chunks(id, doc_id, page_from, page_to, section_path, type[text|table_summary], table_id?, token_count)
tables(id, doc_id, page_no, caption, n_rows, n_cols, markdown_path, parquet_path, summary)
graph_nodes(id, type[Document|Page|Section|Chunk|Table|Concept|Quantity|Reference], label, norm_key, props_json)
graph_edges(src, dst, type[CONTAINS|HAS_TABLE|MENTIONS|DEFINES|MEASURES|CITES|SAME_AS|NEXT|ON_PAGE], weight, evidence_chunk_id)
jobs(id, doc_id, stage, progress, started_at, finished_at, error)
chats(id, title, created_at) / messages(id, chat_id, role, content, sources_json)
```

### LanceDB (`data/lancedb/`)
```
chunks: vector(384), text, doc_id, page_from, page_to, section_path, type, table_id, chunk_id
  + FTS index (tantivy/BM25) nad `text`
```

### Súbory
```
data/pdfs/{doc_id}.pdf          — originál (servuje sa do pdf.js)
data/parsed/{doc_id}.json       — DoclingDocument (lossless, re-chunking bez re-parsu)
data/tables/{table_id}.md|.parquet — plná tabuľka (markdown pre LLM kontext, parquet pre presné čísla)
```

---

## 5. Ingestion pipeline (sekvenčná — RAM disciplína)

```
upload → [1 parse] → [2 tabuľky] → [3 chunking] → [4 embedding] → [5 extrakcia] → [6 graf] → done
```

1. **Parse (Docling):** auto-detekcia skenovaných strán → OCR len tam, kde treba. Výstup DoclingDocument JSON. *(Beží samostatne — špička 2–3 GB.)*
2. **Tabuľky:** každá tabuľka → markdown + parquet + metadáta (caption, rozmery, strana).
3. **Chunking (HybridChunker):** ≤ 450 tokenov, rešpektuje sekcie, metadáta = cesta nadpisov + strany. Tabuľky sa do textových chunkov **nedostávajú** — idú vetvou §6.
4. **Embedding (fastembed):** batch po 32, `passage:` prefix → LanceDB.
5. **Extrakcia entít:** najprv deterministická (regex: veličiny+jednotky, DOI/citácie, dátumy — presné, zadarmo), potom Qwen3-1.7B JSON extrakcia pojmov/metód/vzťahov per chunk. Pydantic validácia + 1 retry.
6. **Graf:** upsert uzlov/hrán, entity resolution (§7.2).

Stav joba per stage v SQLite → progress bar vo frontende. Stages nikdy paralelne (8 GB).

---

## 6. Stratégia veľkých číselných tabuliek (R5)

**Problém:** embedding surových čísel nemá sémantický signál — vektor z „1.42 3.87 12.4…" nenájde nič. Retrieval musí bežať nad *významom* tabuľky, presné čísla sa dodajú až do kontextu odpovede.

1. **Extrakcia:** TableFormer → presná mriežka (spany, hlavičky). Čísla pochádzajú z parsera, **nikdy neprechádzajú cez LLM** — nula halucinovaných číslic v indexe.
2. **Sumár:** Qwen3-1.7B vygeneruje popis tabuľky (čo meria, stĺpce, jednotky, rozsah, kľúčové extrémy) — **iba tento sumár sa embeduje** (`type=table_summary`).
3. **Query time (table routing):** ak retrieval vráti table_summary chunk → do kontextu LLM sa vloží **plná tabuľka v markdowne**. Ak tabuľka > ~1 500 tokenov → hlavička + relevantné row-groups (BM25 nad riadkami) + štatistiky stĺpcov.
4. **Presné čísla:** parquet kópia — frontend TableViewer číta presné hodnoty z nej, nie z LLM výstupu. (Stretch: NL→pandas dopyt nad parquetom pre agregácie.)

---

## 7. Graph RAG (R4, R6)

### 7.1 Schéma (technické/vedecké dokumenty)
- **Uzly:** Document, Page, Section, Chunk, Table + doménové: **Concept** (pojem/metóda/materiál), **Quantity** (veličina+jednotka+hodnota), **Reference** (citovaná práca/DOI/norma)
- **Hrany:** CONTAINS (doc→page→section→chunk), HAS_TABLE, NEXT (poradie chunkov), MENTIONS (chunk→Concept), DEFINES (chunk→Concept — kde je pojem definovaný), MEASURES (Table/Chunk→Quantity), CITES (Document→Reference→Document ak je referencia v korpuse), **SAME_AS** (Concept↔Concept naprieč dokumentmi)
- Každá doménová hrana nesie `evidence_chunk_id` → vždy vieš ukázať dôkaz (dokument + strana).

### 7.2 Entity resolution (jadro cross-PDF prepojení)
1. Normalizácia (lowercase, diakritika, singulár, rozvinutie skratiek z definícií „X (skratka)")
2. rapidfuzz similarity ≥ 0.9 → kandidát
3. Embedding kosínus ≥ 0.85 medzi kontextami výskytov → potvrdenie SAME_AS
4. SK↔EN páry cez multilingválny embedding priestor (krok 3 funguje cross-lingválne)

### 7.3 Súvislosti medzi PDFkami a stranami = traverzál
„Ktoré dokumenty riešia X a ako spolu súvisia?" → Concept(X) → SAME_AS klaster → MENTIONS/DEFINES chunky vo všetkých dokumentoch s presnými stranami → CITES hrany medzi nimi. Presne R6, s dôkazmi.

---

## 8. Retrieval pipeline (query time)

```
otázka
 → klasifikácia (heuristika: relačná? číselná/tabuľková? faktická?)
 → [A] dense top-24 (e5, query: prefix)   ┐
 → [B] BM25 top-24 (tantivy)              ├→ RRF fúzia
 → [C] graf expanzia: entity z top chunkov ┘   → top 8
       → SAME_AS/CITES susedia → ich chunky (cap 10)
 → table routing (§6.3)
 → kontext ≤ ~3k tokenov, num_ctx 8192 (KV cache disciplína na 8 GB)
 → Qwen3-4B → SSE stream + citácie [n] mapované na (doc, strana)
```

- Relačné otázky („súvis medzi A a B") → **graf-first**: entity match → subgraf → chunky ako dôkazy.
- Citácie: model dostane očíslovaný zoznam zdrojov, odkazuje [n]; mapovanie na dokument+stranu robí backend deterministicky — modelu sa čísla strán neveria.
- A/B flag `GRAPH_EXPANSION=on|off` → merateľný prínos grafu (§14).

---

## 9. API (FastAPI)

```
POST /api/documents            upload PDF → job
GET  /api/documents            zoznam + stav indexovania
GET  /api/documents/{id}       detail: strany, tabuľky, entity
GET  /api/documents/{id}/pdf   originál pre pdf.js
GET  /api/tables/{id}          markdown + presné dáta (z parquet)
POST /api/chat                 SSE stream (tokeny + sources event)
GET  /api/chats / {id}         história
GET  /api/graph?type=&doc=     subgraf pre vizualizáciu (JSON nodes/links)
GET  /api/graph/node/{id}      detail uzla + evidence chunky
GET  /api/health               Ollama up? modely stiahnuté? OCR engine?
```

## 10. Frontend (Vite + React + TS + shadcn/ui)

| Stránka | Obsah |
|---|---|
| **Chat** | streaming odpoveď, citačné chipy → klik → PDF náhľad na presnej strane (react-pdf), história |
| **Dokumenty** | dropzone upload, progress per stage, per-doc: strany/tabuľky/entity |
| **Graf** | react-force-graph-2d, filter typov uzlov, klik uzol → evidence (dokument+strana), doc-level pohľad „prepojenia medzi PDFkami" |
| **Tabuľky** | TanStack Table nad parquet dátami, link na stranu v PDF |

shadcn komponenty: button, card, dialog, dropzone, progress, tabs, badge, sheet, scroll-area, sonner (toasty), skeleton.

---

## 11. RAM rozpočet (8 GB) — najtvrdší constraint

| Fáza | Beží súčasne | Odhad |
|---|---|---|
| Indexovanie: parse | Docling (+OCR via Vision mimo procesu) | ~2,5–3 GB |
| Indexovanie: embed | fastembed ONNX int8 | ~0,3 GB |
| Indexovanie: extrakcia | qwen3:1.7b q4 | ~1,5 GB |
| Query | qwen3:4b q4 (num_ctx 8192) + fastembed | ~3,2 GB |

Pravidlá: stages sekvenčne; Ollama `keep_alive` krátky pri indexovaní; Docling modely sa uvoľňujú po parse; nikdy 4B model počas parsovania. Config `LOW_RAM=true` (default) toto celé riadi; na väčšom stroji vypneš a povolíš bge-m3 + reranker + 8B model.

---

## 12. Fázy implementácie — každá končí `/verify-changes` pasom

Verifikačný protokol po každej fáze: (1) re-read zmenených súborov, (2) trace logiky end-to-end, (3) kontrola regresií, (4) porovnanie s požiadavkami R1–R10, (5) `pytest` + `ruff` + `mypy` / `tsc` + `vitest` + build, (6) e2e smoke test fázy.

| Fáza | Deliverable | Smoke test |
|---|---|---|
| 0. Scaffold | uv backend + FastAPI /health; Vite+shadcn; skript `ollama pull`; Makefile `dev` | oba servery bežia, health zelený |
| 1. Ingest | upload → Docling parse → JSON + strany + tabuľky uložené | 1 skenované SK PDF + 1 digitálne EN PDF prejdú |
| 2. Index | chunking + tabuľkové sumáre + LanceDB (dense+FTS) | vyhľadanie známej vety aj termínu |
| 3. Baseline RAG | hybrid retrieval + RRF + SSE chat + citácie + PDF náhľad | otázka → odpoveď s korektnou citáciou strany |
| 4. Graf build | deterministické + LLM extraktory, NetworkX+SQLite, SAME_AS resolution | entita zdieľaná 2 PDFkami má SAME_AS hranu |
| 5. Graf retrieval | expanzia + graf-first režim + table routing | relačná otázka vytiahne chunky z 2 dokumentov; číselná otázka dostane plnú tabuľku |
| 6. Graf UI + tabuľky | force-graph explorer + TableViewer | klik na uzol ukáže dôkazy so stranami |
| 7. Eval + polish | golden Q&A sada, hit-rate skript, README | eval beží, čísla zapísané |

Poradie zdôvodnenie: fáza 3 = použiteľný RAG čo najskôr; graf (4–5) sa pridáva na funkčný základ a jeho prínos je merateľný A/B flagom.

---

## 13. Riziká a mitigácie

| Riziko | Mitigácia |
|---|---|
| 8 GB nestačí pri veľkom PDF | sekvenčné stages, OCR len na skenované strany, batch embedding, `LOW_RAM` režim |
| Vision OCR nepodporuje SK | runtime check pri štarte → automatický fallback Tesseract `slk+eng` |
| e5-small kvalita nestačí na odborné texty | eval sada rozhodne; config prepnutie na bge-m3 (ONNX int8) |
| 1.7B extrakcia šumí | deterministické extraktory nesú čísla/citácie (presné); LLM len pojmy/vzťahy; Pydantic validácia + retry; hrany pod prahom confidence sa zahodia |
| Obrovská tabuľka > kontext | row-groups s opakovanou hlavičkou + štatistiky stĺpcov; presné čísla vždy z parquet |
| SAME_AS false positives | dvojitý prah (fuzzy + embedding), hrany s confidence, UI umožní hranu zmazať |

## 14. v2 — analytická vrstva (fázy 8–11, dostavané po náleze s jedálničkom)

Nález: single-shot RAG principiálne nezvláda katalógové filter/agregačné otázky
(„všetky obedy do 870 kcal") — top-k retrieval nedáva úplné pokrytie a malý LLM
nespoľahlivo porovnáva čísla. Riešenie: čísla LLM úplne zobrať.

**Records store** (`app/ingest/records.py`): riadky tabuliek → SQLite záznamy
(kategória, názov, číselné atribúty cez deterministický parser jednotiek — kcal,
b/s/t makrá, g/ml/%…). Kategória tabuľky = najbližší nadpis nad ňou v reading
order (`_headings_before_tables` — dizajnové PDF majú kategóriu mimo tabuľky).

**Query router** (`app/retrieve/router.py`): heuristiky (op-slová do/pod/nad/aspoň
+ čísla + atribútové náznaky) + kategórie data-driven z DB + LLM fallback (JSON
schema). Módy: `lookup` → klasický RAG; `filter` → SQL nad records
(`records_query.py`), LLM výsledok len formuluje; `compose` → agent.

**Agent slučka** (`app/retrieve/agent.py`): Ollama tool-calling, max 6 krokov,
nástroje `query_records`, `pick_items` (exaktný kombinatorický výber do rozpočtu,
`solver.py`), `search_docs`. Každé číslo v odpovedi pochádza z nástroja. Kroky sa
streamujú do UI (`step` eventy).

**Self-check** (`app/retrieve/selfcheck.py`): čísla ≥13 v odpovedi musia
existovať v podkladoch (alebo byť súčtom dvojice) — SSE `verify` event, FE warning.

Sumáre tabuliek generuje answer model (1.7b dával generické nerozlíšiteľné texty).
Reindex tlačidlo na dokumente aplikuje v2 ingest na staré dokumenty.

## 15. Eval

`eval/golden.yaml`: ~20 otázok (SK aj EN, faktické + tabuľkové + relačné cross-doc). Skript meria: retrieval hit-rate@8 (je zdrojový chunk v kontexte?), citačná presnosť (správny dokument+strana?), latencia. Beh s `GRAPH_EXPANSION=on/off` → tvrdé číslo, čo graf pridáva.
