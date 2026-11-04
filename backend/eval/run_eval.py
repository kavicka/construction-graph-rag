"""Eval harness — meria retrieval hit-rate, presnosť citácií a prínos grafu.

Beží proti spustenému backendu (make backend):
    uv run python -m eval.run_eval            # plný beh (upload korpusu + otázky)
    uv run python -m eval.run_eval --skip-upload   # korpus už je zaindexovaný

Metriky per otázka:
  hit@k       — očakávané dokumenty medzi zdrojmi
  substrings  — presné hodnoty (čísla) v odpovedi
  cited       — odpoveď obsahuje aspoň jednu platnú citáciu [n]
Graf A/B: relačné otázky sa vyhodnotia s RAG_GRAPH_EXPANSION on aj off
(prepínané per-request cez query param sa nedá — meriame cez /api/search expanziu
zdrojov: on-beh používa bežiaci server; off-porovnanie robí čistý retrieval bez grafu
priamo cez /api/search?mode=hybrid).
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from eval.corpus import generate_corpus
from eval.golden import GOLDEN, GoldenQuestion

BASE = "http://localhost:8000/api"
RESULTS_PATH = Path(__file__).parent / "_runs" / "results.json"
CITATION = re.compile(r"\[(\d+)\]")


def upload_corpus() -> dict[str, str]:
    """→ filename → doc_id; čaká na done."""
    tmp = Path(__file__).parent / "_corpus"
    paths = generate_corpus(tmp)
    files = [("files", (p.name, p.read_bytes(), "application/pdf")) for p in paths]
    created = httpx.post(f"{BASE}/documents", files=files, timeout=60).json()
    ids = {c["filename"]: c["id"] for c in created}

    deadline = time.time() + 900
    while time.time() < deadline:
        docs = {d["id"]: d for d in httpx.get(f"{BASE}/documents", timeout=10).json()}
        states = [docs[i]["status"] for i in ids.values() if i in docs]
        if all(s in ("done", "error") for s in states):
            for fn, i in ids.items():
                if docs[i]["status"] == "error":
                    print(f"CHYBA ingestu {fn}: {docs[i]['error']}", file=sys.stderr)
                    sys.exit(2)
            return ids
        time.sleep(3)
    print("timeout ingestu korpusu", file=sys.stderr)
    sys.exit(2)


def ask(question: str) -> tuple[str, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    with httpx.stream("POST", f"{BASE}/chat", json={"message": question}, timeout=600) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    answer = "".join(e["text"] for e in events if e["type"] == "delta")
    sources: list[dict[str, Any]] = next(
        (e["sources"] for e in events if e["type"] == "sources"), []
    )
    errors = [e for e in events if e["type"] == "error"]
    if errors:
        raise RuntimeError(errors[0]["message"])
    return answer, sources


def evaluate_question(q: GoldenQuestion) -> dict[str, Any]:
    t0 = time.time()
    answer, sources = ask(q.question)
    latency = round(time.time() - t0, 1)

    source_files = {s["filename"] for s in sources}
    hit = all(doc in source_files for doc in q.expected_docs) if q.expected_docs else True
    substrings_ok = all(sub in answer for sub in q.expected_substrings) and not any(
        sub in answer for sub in q.forbidden_substrings
    )

    cited_ns = {int(m) for m in CITATION.findall(answer)}
    valid_ns = {s["n"] for s in sources}
    cited = bool(cited_ns) and cited_ns <= valid_ns if q.expected_docs else True

    cross_doc = len({s["doc_id"] for s in sources if s["filename"] in q.expected_docs}) >= 2

    return {
        "id": q.id,
        "hit": hit,
        "substrings": substrings_ok,
        "cited": cited,
        "cross_doc": cross_doc if q.relational else None,
        "latency_s": latency,
        "sources": sorted(source_files),
        "answer": answer[:300],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-upload", action="store_true")
    args = parser.parse_args()

    health = httpx.get(f"{BASE}/health", timeout=5).json()
    print(
        f"profil={health['ram_profile']} embed={health['embedding_model']} "
        f"ollama={'OK' if health['ollama']['up'] else 'DOWN'}"
    )

    if not args.skip_upload:
        print("→ upload korpusu…")
        upload_corpus()

    results = []
    for q in GOLDEN:
        r = evaluate_question(q)
        flags = "".join(
            [
                "H" if r["hit"] else "·",
                "S" if r["substrings"] else "·",
                "C" if r["cited"] else "·",
                ("X" if r["cross_doc"] else "·") if r["cross_doc"] is not None else " ",
            ]
        )
        print(f"[{flags}] {q.id:16s} {r['latency_s']:5.1f}s  zdroje={r['sources']}")
        results.append(r)

    n = len(results)
    summary = {
        "hit_rate": sum(r["hit"] for r in results) / n,
        "substring_rate": sum(r["substrings"] for r in results) / n,
        "citation_rate": sum(r["cited"] for r in results) / n,
        "relational_cross_doc": sum(1 for r in results if r["cross_doc"])
        / max(1, sum(1 for r in results if r["cross_doc"] is not None)),
        "avg_latency_s": round(sum(r["latency_s"] for r in results) / n, 1),
        "graph_expansion": True,
    }
    print(
        f"\nhit@8={summary['hit_rate']:.0%}  čísla={summary['substring_rate']:.0%}  "
        f"citácie={summary['citation_rate']:.0%}  "
        f"relačné-crossdoc={summary['relational_cross_doc']:.0%}  "
        f"latencia={summary['avg_latency_s']}s"
    )

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"→ {RESULTS_PATH}")
    return 0 if summary["hit_rate"] >= 0.7 else 1


if __name__ == "__main__":
    sys.exit(main())
