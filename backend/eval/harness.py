"""Eval v2 — runs in-process, independent of a running server.

    uv run python -m eval.harness                    # retrieval ablations
    uv run python -m eval.harness --generate         # + generation metrics (Ollama, ~10 min)
    uv run python -m eval.harness --gate             # compare with eval/baseline.json
    uv run python -m eval.harness --update-baseline  # save the current numbers as the baseline

The corpus (eval/construction.py) is indexed into eval/_runs/<variant>/data — each
ingest variant (without / with contextual retrieval) has its own SQLite + LanceDB. The index is
reused until the corpus or the ingest code changes (hash in manifest.json).
Output: eval/_runs/results-v2.json (not committed) and eval/REPORT.md (tables for the README).
"""

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import shutil
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import db
from app.config import get_settings
from app.graph.build import build_document_graph
from app.index import lancedb_store
from app.ingest.docling_parse import parse_document
from app.ingest.service import _index_document
from app.retrieve.pipeline import RetrievalConfig, rank_chunks
from eval.construction import QUESTIONS, EvalQuestion, generate_construction_corpus
from eval.metrics import (
    RetrievedChunk,
    citation_validity,
    contains_expected,
    is_refusal,
    mean,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)

logger = logging.getLogger("eval")

EVAL_DIR = Path(__file__).resolve().parent
RUNS_DIR = EVAL_DIR / "_runs"
RESULTS_PATH = RUNS_DIR / "results-v2.json"
REPORT_PATH = EVAL_DIR / "REPORT.md"
BASELINE_PATH = EVAL_DIR / "baseline.json"
APP_DIR = EVAL_DIR.parent / "app"

GATE_TOLERANCE = 0.02  # a drop of more than 2 points = regression
INGEST_VARIANTS: dict[str, dict[str, str]] = {
    "base": {"RAG_CONTEXTUAL_CHUNKS": "false"},
    "contextual": {"RAG_CONTEXTUAL_CHUNKS": "true"},
    # production setup + 3 poisoned documents (eval/injection.py)
    "injection": {"RAG_CONTEXTUAL_CHUNKS": "true"},
}


@dataclass(frozen=True)
class Ablation:
    name: str
    variant: str
    cfg: RetrievalConfig


ABLATIONS: list[Ablation] = [
    Ablation("dense", "base", RetrievalConfig(fts=False, graph=False)),
    Ablation("bm25", "base", RetrievalConfig(dense=False, graph=False)),
    Ablation("hybrid (dense+bm25, RRF)", "base", RetrievalConfig(graph=False)),
    Ablation(
        "hybrid + graph on every query",
        "base",
        RetrievalConfig(graph=True, graph_relational_only=False),
    ),
    Ablation("hybrid + graph on relational queries", "base", RetrievalConfig(graph=True)),
    Ablation("hybrid + graph + rerank", "base", RetrievalConfig(graph=True, rerank=True)),
    Ablation("contextual + hybrid + graph", "contextual", RetrievalConfig(graph=True)),
    Ablation(
        "contextual + hybrid + graph + rerank",
        "contextual",
        RetrievalConfig(graph=True, rerank=True),
    ),
    Ablation(
        "… + visual pages (ColSmol, experimental)",
        "contextual",
        RetrievalConfig(graph=True, rerank=True, visual=True),
    ),
]
DEFAULT_ABLATION = "contextual + hybrid + graph + rerank"  # = production setup


# --- environment ------------------------------------------------------------------------


def _activate(variant: str) -> Path:
    """Switches settings to the variant data dir (singletons bound to data_dir are discarded)."""
    data_dir = RUNS_DIR / variant / "data"
    os.environ["RAG_DATA_DIR"] = str(data_dir)
    for key, value in INGEST_VARIANTS[variant].items():
        os.environ[key] = value
    get_settings.cache_clear()
    lancedb_store._connect.cache_clear()
    settings = get_settings()
    settings.ensure_dirs()
    db.init_db()
    return data_dir


def _source_hash() -> str:
    """Corpus + ingest code — any change to either = a new index."""
    from eval.construction import CORPUS
    from eval.injection import POISONED

    h = hashlib.sha256()
    # document contents, not the whole file — editing expected answers must not force a re-ingest
    h.update(
        json.dumps(
            [(d.filename, d.title, d.pages) for d in CORPUS + POISONED], ensure_ascii=False
        ).encode()
    )
    files = [EVAL_DIR / "pdfgen.py"]
    for sub in ("ingest", "index", "graph"):
        files += sorted((APP_DIR / sub).glob("*.py"))
    for f in files:
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def ingest_variant(variant: str, *, force: bool = False) -> None:
    run_dir = RUNS_DIR / variant
    settings_snapshot = {
        "source": _source_hash(),
        "flags": INGEST_VARIANTS[variant],
        "embedding": None,
        "answer_model": None,
    }
    _activate(variant)
    settings = get_settings()
    settings_snapshot["embedding"] = settings.effective_embedding_model
    settings_snapshot["answer_model"] = settings.answer_model
    manifest = run_dir / "manifest.json"
    if not force and manifest.exists() and json.loads(manifest.read_text()) == settings_snapshot:
        print(f"[{variant}] index aktuálny — preskakujem ingest")
        return

    print(f"[{variant}] ingest korpusu…")
    shutil.rmtree(run_dir, ignore_errors=True)
    _activate(variant)
    t0 = time.time()
    pdfs = generate_construction_corpus(run_dir / "source")
    if variant == "injection":
        from eval.injection import generate_poisoned

        pdfs += generate_poisoned(run_dir / "source")
    for pdf in pdfs:
        doc_id = uuid.uuid4().hex
        shutil.copy(pdf, settings.pdf_dir / f"{doc_id}.pdf")
        db.insert_document(doc_id, pdf.name, hashlib.sha256(pdf.read_bytes()).hexdigest())
        parse_document(doc_id)
        chunks = _index_document(doc_id)
        build_document_graph(doc_id)
        db.set_document_status(doc_id, "done", progress=1.0)
        print(f"  {pdf.name}: {chunks} chunkov")
    manifest.write_text(json.dumps(settings_snapshot, indent=1))
    print(f"[{variant}] hotovo za {time.time() - t0:.0f} s")


# --- retrieval -------------------------------------------------------------------------


def _ranked(question: str, cfg: RetrievalConfig, names: dict[str, str]) -> list[RetrievedChunk]:
    rows = rank_chunks(question, k=10, cfg=cfg)
    return [
        RetrievedChunk(names.get(r["doc_id"], r["doc_id"]), r["page_from"], r["page_to"])
        for r in rows
    ]


def _ensure_visual_index() -> None:
    from app.index.visual import index_pages, visual_dir

    settings = get_settings()
    for d in db.list_documents():
        if not any(visual_dir().glob(f"{d['id']}__*.npy")):
            index_pages(d["id"], settings.pdf_dir / f"{d['id']}.pdf")


def eval_retrieval(ablation: Ablation, questions: list[EvalQuestion]) -> dict[str, Any]:
    _activate(ablation.variant)
    if ablation.cfg.visual:
        print("  vizuálny index strán…")
        _ensure_visual_index()
    names = {d["id"]: d["filename"] for d in db.list_documents()}
    answerable = [q for q in questions if q.gold]
    _ranked(answerable[0].question, ablation.cfg, names)  # warm-up: model loading

    per_q: list[dict[str, Any]] = []
    for q in answerable:
        t0 = time.perf_counter()
        ranked = _ranked(q.question, ablation.cfg, names)
        latency = (time.perf_counter() - t0) * 1000
        per_q.append(
            {
                "id": q.id,
                "kind": q.kind,
                "lang": q.lang,
                "recall@5": recall_at_k(ranked, q.gold, 5),
                "recall@10": recall_at_k(ranked, q.gold, 10),
                "mrr": reciprocal_rank(ranked, q.gold),
                "ndcg@10": ndcg_at_k(ranked, q.gold, 10),
                "latency_ms": round(latency, 1),
            }
        )

    def agg(rows: list[dict[str, Any]]) -> dict[str, float]:
        return {
            m: round(mean([r[m] for r in rows]), 4)
            for m in ("recall@5", "recall@10", "mrr", "ndcg@10")
        }

    latencies = [r["latency_ms"] for r in per_q]
    by_kind = {
        k: agg([r for r in per_q if r["kind"] == k]) for k in sorted({r["kind"] for r in per_q})
    }
    by_lang = {
        lang: agg([r for r in per_q if r["lang"] == lang])
        for lang in sorted({r["lang"] for r in per_q})
    }
    return {
        "name": ablation.name,
        "summary": {
            **agg(per_q),
            "p50_ms": round(statistics.median(latencies), 1),
            "p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 1),
        },
        "by_kind": by_kind,
        "by_lang": by_lang,
        "questions": per_q,
    }


# --- generation -----------------------------------------------------------------------


def eval_generation(ablation: Ablation, questions: list[EvalQuestion]) -> dict[str, Any]:
    from app.retrieve.orchestrator import answer_question
    from eval.judge import judge_faithfulness

    _activate(ablation.variant)
    os.environ["RAG_GRAPH_EXPANSION"] = str(ablation.cfg.graph).lower()
    os.environ["RAG_RERANK"] = str(ablation.cfg.rerank).lower()
    os.environ["RAG_GRAPH_RELATIONAL_ONLY"] = str(ablation.cfg.graph_relational_only).lower()
    get_settings.cache_clear()

    async def run_all() -> list[dict[str, Any]]:
        rows = []
        for i, q in enumerate(questions, start=1):
            t0 = time.perf_counter()
            result = await answer_question(q.question)
            latency = time.perf_counter() - t0
            answer = result.answer
            refused = is_refusal(answer)
            correct = (
                (
                    all(contains_expected(answer, e) for e in q.expected)
                    and not any(contains_expected(answer, f) for f in q.forbidden)
                    and not refused
                )
                if q.kind != "unanswerable"
                else refused
            )
            faithfulness = None
            if q.kind != "unanswerable" and not refused:
                sources_text = "\n\n".join(f"[{s.n}] {s.text}" for s in result.sources)
                faithfulness = await asyncio.to_thread(judge_faithfulness, answer, sources_text)
            rows.append(
                {
                    "id": q.id,
                    "kind": q.kind,
                    "lang": q.lang,
                    "mode": result.mode,
                    "correct": correct,
                    "refused": refused,
                    "citations_valid": citation_validity(answer, len(result.sources)),
                    "faithfulness": faithfulness,
                    "latency_s": round(latency, 2),
                    "answer": answer[:400],
                }
            )
            mark = "✓" if correct else "✗"
            print(f"  {mark} [{i:3d}/{len(questions)}] {q.id:24s} {latency:5.1f}s")
        return rows

    rows = asyncio.run(run_all())
    answerable = [r for r in rows if r["kind"] != "unanswerable"]
    unanswerable = [r for r in rows if r["kind"] == "unanswerable"]
    cited = [r["citations_valid"] for r in answerable if r["citations_valid"] is not None]
    faith = [r["faithfulness"] for r in answerable if r["faithfulness"] is not None]
    return {
        "name": ablation.name,
        "summary": {
            "answer_correctness": round(mean([float(r["correct"]) for r in answerable]), 4),
            "faithfulness": round(mean(faith), 4) if faith else math.nan,
            "citation_validity": round(mean([float(c) for c in cited]), 4) if cited else math.nan,
            "answered_with_citation": round(
                sum(r["citations_valid"] is not None for r in answerable) / len(answerable), 4
            ),
            "abstention_on_unanswerable": round(
                mean([float(r["refused"]) for r in unanswerable]), 4
            )
            if unanswerable
            else math.nan,
            "false_refusal_rate": round(mean([float(r["refused"]) for r in answerable]), 4),
            "avg_latency_s": round(mean([r["latency_s"] for r in rows]), 2),
        },
        "by_kind": {
            k: round(mean([float(r["correct"]) for r in rows if r["kind"] == k]), 4)
            for k in sorted({r["kind"] for r in rows})
        },
        "questions": rows,
    }


# --- report and gate ---------------------------------------------------------------------


def _pct(v: float) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v * 100:.1f}"


def write_report(results: dict[str, Any]) -> None:
    lines = [
        "# Evaluation report",
        "",
        f"Corpus: {len({d for q in QUESTIONS for d, _ in q.gold})} documents, "
        f"{len(QUESTIONS)} questions "
        f"({sum(q.kind != 'unanswerable' for q in QUESTIONS)} answerable, "
        f"{sum(q.kind == 'unanswerable' for q in QUESTIONS)} unanswerable). "
        f"Embedding: `{results['embedding']}`, reranker: `{results['reranker']}`, "
        f"LLM: `{results['answer_model']}`.",
        "",
        "## Retrieval ablation",
        "",
        "Relevance = gold (document, page). Values in %, latency per query.",
        "",
        "| Configuration | Recall@5 | Recall@10 | MRR | nDCG@10 | p50 ms | p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results["retrieval"]:
        s = r["summary"]
        lines.append(
            f"| {r['name']} | {_pct(s['recall@5'])} | {_pct(s['recall@10'])} | "
            f"{_pct(s['mrr'])} | {_pct(s['ndcg@10'])} | {s['p50_ms']:.0f} | {s['p95_ms']:.0f} |"
        )
    best = max(results["retrieval"], key=lambda r: r["summary"]["ndcg@10"])
    lines += [
        "",
        f"### By question type — `{best['name']}`",
        "",
        "| Type | Recall@10 | MRR | nDCG@10 |",
        "|---|---:|---:|---:|",
    ]
    for kind, s in best["by_kind"].items():
        lines.append(
            f"| {kind} | {_pct(s['recall@10'])} | {_pct(s['mrr'])} | {_pct(s['ndcg@10'])} |"
        )
    lines += ["", "| Language | Recall@10 | MRR | nDCG@10 |", "|---|---:|---:|---:|"]
    for lang, s in best["by_lang"].items():
        lines.append(
            f"| {lang} | {_pct(s['recall@10'])} | {_pct(s['mrr'])} | {_pct(s['ndcg@10'])} |"
        )

    if gen := results.get("generation"):
        s = gen["summary"]
        lines += [
            "",
            f"## Answer quality — `{gen['name']}`",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Answer correctness (exact facts present) | {_pct(s['answer_correctness'])} % |",
            f"| Faithfulness (LLM judge, supported claims) | {_pct(s['faithfulness'])} % |",
            f"| Valid citations | {_pct(s['citation_validity'])} % |",
            f"| Answers with a citation | {_pct(s['answered_with_citation'])} % |",
            f"| Abstains on unanswerable questions | {_pct(s['abstention_on_unanswerable'])} % |",
            f"| False refusals on answerable questions | {_pct(s['false_refusal_rate'])} % |",
            f"| Average end-to-end latency | {s['avg_latency_s']} s |",
            "",
            "| Type | Correct |",
            "|---|---:|",
        ]
        lines += [f"| {k} | {_pct(v)} % |" for k, v in gen["by_kind"].items()]
    if inj := results.get("injection"):
        off, on = inj["off"], inj["on"]
        lines += [
            "",
            "## Prompt injection",
            "",
            f"{len(on['questions'])} questions over 3 poisoned documents that carry instructions "
            "for the model (answer with a canary word, append an exfiltration image, state a "
            "fake price).",
            "",
            "| | Attack success rate | Task still answered correctly |",
            "|---|---:|---:|",
            f"| No defense | {_pct(off['attack_success_rate'])} % | "
            f"{_pct(off['task_correctness'])} % |",
            f"| Injection guard | {_pct(on['attack_success_rate'])} % | "
            f"{_pct(on['task_correctness'])} % |",
        ]
    lines += ["", f"_Generated {results['created_at']} by `python -m eval.harness`._", ""]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


GATED_RETRIEVAL = ("recall@10", "mrr", "ndcg@10")
GATED_GENERATION = ("answer_correctness", "abstention_on_unanswerable", "citation_validity")


def _gate_values(results: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in results["retrieval"]:
        for m in GATED_RETRIEVAL:
            out[f"retrieval/{r['name']}/{m}"] = r["summary"][m]
    if gen := results.get("generation"):
        for m in GATED_GENERATION:
            out[f"generation/{gen['name']}/{m}"] = gen["summary"][m]
    return out


def check_gate(results: dict[str, Any]) -> list[str]:
    if not BASELINE_PATH.exists():
        return ["chýba eval/baseline.json — spusti s --update-baseline"]
    baseline: dict[str, float] = json.loads(BASELINE_PATH.read_text())
    current = _gate_values(results)
    failures = []
    for key, base in baseline.items():
        if key not in current:
            continue  # the metric was not measured in this run (e.g. without --generate)
        if current[key] < base - GATE_TOLERANCE:
            failures.append(f"{key}: {current[key]:.3f} < baseline {base:.3f}")
    return failures


def _merge_previous(results: dict[str, Any]) -> dict[str, Any]:
    """A run with only some ablations keeps the others from the previous run (same corpus/code)."""
    if not RESULTS_PATH.exists():
        return results
    previous = json.loads(RESULTS_PATH.read_text())
    if previous.get("source") != results["source"]:
        return results
    fresh = {r["name"] for r in results["retrieval"]}
    order = [a.name for a in ABLATIONS]
    merged = results["retrieval"] + [
        r for r in previous.get("retrieval", []) if r["name"] not in fresh and r["name"] in order
    ]
    results["retrieval"] = sorted(merged, key=lambda r: order.index(r["name"]))
    for key in ("generation", "injection"):
        if key not in results and key in previous:
            results[key] = previous[key]
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--reingest", action="store_true", help="vynúť nový ingest korpusu")
    parser.add_argument("--configs", help="čiarkou oddelené názvy ablácií (default všetky)")
    parser.add_argument("--generate", action="store_true", help="aj generačné metriky")
    parser.add_argument(
        "--gen-config", default=None, help=f"ablácia pre generovanie (default {DEFAULT_ABLATION!r})"
    )
    parser.add_argument("--limit", type=int, default=None, help="len prvých N otázok")
    parser.add_argument(
        "--injection",
        action="store_true",
        help="prompt-injection útoky s obranou a bez nej (Ollama)",
    )
    parser.add_argument("--gate", action="store_true", help="porovnaj s baseline")
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    ablations = ABLATIONS
    if args.configs:
        wanted = {c.strip() for c in args.configs.split(",")}
        ablations = [a for a in ABLATIONS if a.name in wanted]
    questions = QUESTIONS[: args.limit] if args.limit else QUESTIONS

    for variant in sorted({a.variant for a in ablations}):
        ingest_variant(variant, force=args.reingest)

    retrieval = []
    for ablation in ablations:
        print(f"→ retrieval: {ablation.name}")
        r = eval_retrieval(ablation, questions)
        s = r["summary"]
        print(
            f"  R@5={_pct(s['recall@5'])} R@10={_pct(s['recall@10'])} MRR={_pct(s['mrr'])} "
            f"nDCG@10={_pct(s['ndcg@10'])} p50={s['p50_ms']:.0f}ms"
        )
        retrieval.append(r)

    settings = get_settings()
    results: dict[str, Any] = {
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
        "embedding": settings.effective_embedding_model,
        "reranker": settings.effective_reranker_model,
        "answer_model": settings.answer_model,
        "retrieval": retrieval,
    }
    if args.generate:
        name = args.gen_config or DEFAULT_ABLATION
        ablation = next(a for a in ABLATIONS if a.name == name)
        print(f"→ generovanie: {ablation.name}")
        results["generation"] = eval_generation(ablation, questions)
        print(f"  {results['generation']['summary']}")

    results["source"] = _source_hash()
    results = _merge_previous(results)
    if args.injection:
        from eval.injection import run_attacks

        ingest_variant("injection", force=args.reingest)
        _activate("injection")
        os.environ["RAG_RERANK"] = "true"
        print("→ prompt injection: bez obrany / s obranou")
        results["injection"] = {"off": run_attacks(guard=False), "on": run_attacks(guard=True)}

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    write_report(results)
    print(f"→ {REPORT_PATH.relative_to(EVAL_DIR.parent)}")

    if args.update_baseline:
        BASELINE_PATH.write_text(json.dumps(_gate_values(results), indent=1))
        print(f"→ baseline uložená ({BASELINE_PATH.name})")
    if args.gate:
        failures = check_gate(results)
        for f in failures:
            print(f"REGRESIA {f}", file=sys.stderr)
        return 1 if failures else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
