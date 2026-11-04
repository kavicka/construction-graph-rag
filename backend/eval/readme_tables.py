"""Vloží súhrn a tabuľky z eval/results-v2.json do README.md (značky EVAL_SUMMARY/EVAL_TABLES).

uv run python -m eval.readme_tables
"""

import json
import math
import re
from pathlib import Path
from typing import Any

from eval.harness import DEFAULT_ABLATION, RESULTS_PATH

README = Path(__file__).resolve().parents[2] / "README.md"


def _pct(v: float | None) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v * 100:.1f}"


def _by_name(results: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((r for r in results["retrieval"] if r["name"] == name), None)


def summary(results: dict[str, Any]) -> str:
    base = _by_name(results, "hybrid (dense+bm25, RRF)")
    best = _by_name(results, DEFAULT_ABLATION)
    parts = []
    if base and best:
        parts.append(
            f"**Retrieval MRR {_pct(base['summary']['mrr'])} → {_pct(best['summary']['mrr'])} %** "
            f"and nDCG@10 {_pct(base['summary']['ndcg@10'])} → "
            f"{_pct(best['summary']['ndcg@10'])} % (plain hybrid → production pipeline)"
        )
    if gen := results.get("generation"):
        s = gen["summary"]
        parts.append(
            f"**{_pct(s['answer_correctness'])} %** of answers contain the exact expected facts, "
            f"**{_pct(s['abstention_on_unanswerable'])} %** abstain when the corpus has no answer"
        )
    if inj := results.get("injection"):
        parts.append(
            f"prompt-injection attack success **{_pct(inj['off']['attack_success_rate'])} → "
            f"{_pct(inj['on']['attack_success_rate'])} %** with the guard"
        )
    if not parts:
        return ""
    return (
        "> Measured on a 128-question golden set with a local 4B model: "
        + "; ".join(parts)
        + ". See [Evaluation](#evaluation)."
    )


def tables(results: dict[str, Any]) -> str:
    lines = [
        "| Retrieval configuration | Recall@10 | MRR | nDCG@10 | p50 latency |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in results["retrieval"]:
        s = r["summary"]
        name = f"**{r['name']}**" if r["name"] == DEFAULT_ABLATION else r["name"]
        lines.append(
            f"| {name} | {_pct(s['recall@10'])} | {_pct(s['mrr'])} | {_pct(s['ndcg@10'])} | "
            f"{s['p50_ms']:.0f} ms |"
        )
    if gen := results.get("generation"):
        s = gen["summary"]
        lines += [
            "",
            f"Answer quality (`{gen['name']}`, `{results['answer_model']}`):",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Exact expected facts in the answer | {_pct(s['answer_correctness'])} % |",
            f"| Faithfulness — claims supported by sources (LLM judge) | "
            f"{_pct(s['faithfulness'])} % |",
            f"| Valid citations | {_pct(s['citation_validity'])} % |",
            f"| Abstains on unanswerable questions | {_pct(s['abstention_on_unanswerable'])} % |",
            f"| False refusals | {_pct(s['false_refusal_rate'])} % |",
            f"| Average end-to-end latency | {s['avg_latency_s']} s |",
        ]
    if inj := results.get("injection"):
        off, on = inj["off"], inj["on"]
        lines += [
            "",
            "Prompt injection (6 attacks hidden in 3 poisoned documents):",
            "",
            "| | Attack success | Task still answered |",
            "|---|---:|---:|",
            f"| No defense | {_pct(off['attack_success_rate'])} % | "
            f"{_pct(off['task_correctness'])} % |",
            f"| Injection guard | {_pct(on['attack_success_rate'])} % | "
            f"{_pct(on['task_correctness'])} % |",
        ]
    return "\n".join(lines)


def _replace(text: str, marker: str, content: str) -> str:
    block = f"<!-- {marker} -->\n{content}\n<!-- /{marker} -->"
    pattern = re.compile(rf"<!-- {marker} -->(?:.*?<!-- /{marker} -->)?", re.DOTALL)
    return pattern.sub(lambda _: block, text, count=1)


def main() -> None:
    results = json.loads(RESULTS_PATH.read_text())
    text = README.read_text(encoding="utf-8")
    text = _replace(text, "EVAL_SUMMARY", summary(results))
    text = _replace(text, "EVAL_TABLES", tables(results))
    README.write_text(text, encoding="utf-8")
    print(f"→ {README}")


if __name__ == "__main__":
    main()
