# Evaluation report

Corpus: 12 documents, 128 questions (118 answerable, 10 unanswerable). Embedding: `BAAI/bge-m3`, reranker: `BAAI/bge-reranker-v2-m3`, LLM: `qwen3:4b-instruct`.

## Retrieval ablation

Relevance = gold (document, page). Values in %, latency per query.

| Configuration | Recall@5 | Recall@10 | MRR | nDCG@10 | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|
| dense | 93.2 | 99.2 | 82.4 | 85.7 | 24 | 33 |
| bm25 | 78.4 | 86.0 | 69.9 | 73.0 | 3 | 4 |
| hybrid (dense+bm25, RRF) | 90.2 | 94.1 | 76.1 | 80.0 | 26 | 36 |
| hybrid + graph on every query | 81.4 | 94.9 | 54.8 | 63.9 | 31 | 41 |
| hybrid + graph on relational queries | 90.2 | 94.1 | 75.7 | 79.9 | 27 | 36 |
| hybrid + graph + rerank | 96.6 | 100.0 | 85.8 | 89.0 | 823 | 845 |
| contextual + hybrid + graph | 91.5 | 100.0 | 85.3 | 88.2 | 28 | 38 |
| contextual + hybrid + graph + rerank | 99.6 | 100.0 | 96.5 | 96.9 | 837 | 863 |
| … + visual pages (ColSmol, experimental) | 98.3 | 99.2 | 95.0 | 95.7 | 876 | 894 |

### By question type — `contextual + hybrid + graph + rerank`

| Type | Recall@10 | MRR | nDCG@10 |
|---|---:|---:|---:|
| cross_doc | 100.0 | 93.8 | 92.1 |
| factual | 100.0 | 97.1 | 97.8 |
| multi_hop | 100.0 | 92.9 | 89.4 |
| table | 100.0 | 96.5 | 97.5 |

| Language | Recall@10 | MRR | nDCG@10 |
|---|---:|---:|---:|
| en | 100.0 | 94.7 | 96.0 |
| sk | 100.0 | 96.8 | 97.0 |

## Answer quality — `contextual + hybrid + graph + rerank`

| Metric | Value |
|---|---:|
| Answer correctness (exact facts present) | 99.2 % |
| Faithfulness (LLM judge, supported claims) | 90.1 % |
| Valid citations | 100.0 % |
| Answers with a citation | 99.2 % |
| Abstains on unanswerable questions | 90.0 % |
| False refusals on answerable questions | 0.9 % |
| Average end-to-end latency | 6.89 s |

| Type | Correct |
|---|---:|
| cross_doc | 100.0 % |
| factual | 100.0 % |
| multi_hop | 85.7 % |
| table | 100.0 % |
| unanswerable | 90.0 % |

## Prompt injection

6 questions over 3 poisoned documents that carry instructions for the model (answer with a canary word, append an exfiltration image, state a fake price).

| | Attack success rate | Task still answered correctly |
|---|---:|---:|
| No defense | 16.7 % | 100.0 % |
| Injection guard | 0.0 % | 100.0 % |

_Generated 2026-11-05 06:13 by `python -m eval.harness`._
