# Evaluation report

Corpus: 12 documents, 128 questions (118 answerable, 10 unanswerable). Embedding: `BAAI/bge-m3`, reranker: `BAAI/bge-reranker-v2-m3`, LLM: `qwen3:4b-instruct`.

## Retrieval ablation

Relevance = gold (document, page). Values in %, latency per query.

| Configuration | Recall@5 | Recall@10 | MRR | nDCG@10 | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|
| dense | 86.9 | 90.2 | 76.9 | 78.8 | 27 | 38 |
| bm25 | 76.3 | 81.8 | 68.2 | 70.7 | 4 | 7 |
| hybrid (dense+bm25, RRF) | 84.8 | 88.6 | 72.9 | 76.2 | 29 | 39 |
| hybrid + graph on every query | 76.7 | 87.3 | 51.4 | 59.2 | 34 | 47 |
| hybrid + graph on relational queries | 84.8 | 88.6 | 73.4 | 76.4 | 29 | 39 |
| hybrid + graph + rerank | 87.7 | 91.1 | 81.3 | 82.9 | 913 | 964 |
| contextual + hybrid + graph | 94.1 | 98.7 | 83.8 | 87.4 | 31 | 40 |
| contextual + hybrid + graph + rerank | 98.7 | 99.2 | 95.5 | 96.1 | 852 | 963 |
| … + visual pages (ColSmol, experimental) | 98.3 | 99.2 | 95.5 | 95.9 | 910 | 1163 |

### By question type — `contextual + hybrid + graph + rerank`

| Type | Recall@10 | MRR | nDCG@10 |
|---|---:|---:|---:|
| cross_doc | 100.0 | 93.8 | 91.5 |
| factual | 98.7 | 97.1 | 97.5 |
| multi_hop | 100.0 | 85.7 | 87.7 |
| table | 100.0 | 94.2 | 95.7 |

| Language | Recall@10 | MRR | nDCG@10 |
|---|---:|---:|---:|
| en | 100.0 | 97.4 | 98.0 |
| sk | 99.0 | 95.1 | 95.7 |

## Answer quality — `contextual + hybrid + graph + rerank`

| Metric | Value |
|---|---:|
| Answer correctness (exact facts present) | 95.8 % |
| Faithfulness (LLM judge, supported claims) | 90.2 % |
| Valid citations | 100.0 % |
| Answers with a citation | 99.2 % |
| Abstains on unanswerable questions | 80.0 % |
| False refusals on answerable questions | 0.9 % |
| Average end-to-end latency | 8.95 s |

| Type | Correct |
|---|---:|
| cross_doc | 75.0 % |
| factual | 98.7 % |
| multi_hop | 71.4 % |
| table | 100.0 % |
| unanswerable | 80.0 % |

## Prompt injection

6 questions over 3 poisoned documents that carry instructions for the model (answer with a canary word, append an exfiltration image, state a fake price).

| | Attack success rate | Task still answered correctly |
|---|---:|---:|
| No defense | 16.7 % | 100.0 % |
| Injection guard | 0.0 % | 100.0 % |

_Generated 2026-10-08 00:19 by `python -m eval.harness`._
