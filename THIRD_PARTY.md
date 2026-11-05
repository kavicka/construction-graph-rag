# Third-party licences

The code in this repository is MIT-licensed (see [LICENSE](LICENSE)). It depends on, bundles or
downloads the third-party components below. Only notable licences are listed; the full set of
Python and npm dependencies is in `backend/uv.lock` and `frontend/pnpm-lock.yaml`.
Licences were checked on 2026-11-04 against the linked model cards and package metadata.

## Bundled in this repository

| Component | Licence | Where | Source |
|---|---|---|---|
| JetBrains Mono (font) | SIL Open Font License 1.1 | `backend/app/docgen/fonts/` (full text in `OFL.txt`) | https://github.com/JetBrains/JetBrainsMono |

## Optional voice extra (opt-in: `make install-voice`; `make install`, `make dev` and Docker never add it)

| Component | Licence | Notes | Source |
|---|---|---|---|
| Piper TTS (`piper-tts`) | GPL-3.0-or-later | Kept out of the core dependencies so the MIT code is not combined with GPL code by default. Installing the extra means the running app links GPL code. | https://pypi.org/project/piper-tts/ |
| Piper voice `sk_SK-lili-medium` | Repository: MIT; dataset: CC0 | Downloaded at runtime into `data/voices`, not shipped. The model card says it was fine-tuned from `en_US-lessac-medium`, whose training data (Lessac / Blizzard 2013) is licensed for research only and excludes commercial use, so treat this voice as non-commercial. | https://huggingface.co/rhasspy/piper-voices/blob/main/sk/sk_SK/lili/medium/MODEL_CARD, https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/lessac/medium/MODEL_CARD |
| `mlx-whisper` | MIT | macOS on Apple Silicon only. | https://pypi.org/project/mlx-whisper/ |
| Whisper large-v3-turbo (`mlx-community/whisper-large-v3-turbo`) | MIT (upstream `openai/whisper-large-v3-turbo`) | The MLX conversion's card has no licence field, so the upstream OpenAI licence applies. Downloaded at runtime. | https://huggingface.co/openai/whisper-large-v3-turbo |

## Models downloaded at runtime (by RAM profile)

| Role | `low` profile | `high` profile |
|---|---|---|
| Embeddings | `intfloat/multilingual-e5-small`: MIT | `BAAI/bge-m3`: MIT |
| Reranker | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`: Apache-2.0 | `BAAI/bge-reranker-v2-m3`: Apache-2.0 |
| LLM (Ollama) | `qwen3:4b-instruct` (Qwen3-4B-Instruct-2507): Apache-2.0; `qwen3:1.7b`: Apache-2.0 | same |
| Speech-to-text (voice extra) | Whisper large-v3-turbo: MIT | same |

Model cards:

- https://huggingface.co/intfloat/multilingual-e5-small
- https://huggingface.co/BAAI/bge-m3
- https://huggingface.co/cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
- https://huggingface.co/BAAI/bge-reranker-v2-m3
- https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507
- https://huggingface.co/Qwen/Qwen3-1.7B

The `low` reranker used to be `jinaai/jina-reranker-v2-base-multilingual`. It is licensed
CC-BY-NC-4.0, so it is no longer the default. You can still select it with
`RAG_RERANKER_MODEL`, but only for non-commercial use.

The experimental visual retrieval extra (`uv sync --extra visual`) downloads
`vidore/colSmol-256M`, which is MIT-licensed (https://huggingface.co/vidore/colSmol-256M).
