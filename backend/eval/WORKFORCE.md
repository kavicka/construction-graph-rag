# Eval modulu Firma (dochádzka, import úloh)

Spustené 2026-10-08 na lokálnom stroji (`uv run python -m eval.workforce_eval`).

## Prepis reči (Whisper, slovenčina, 10 viet stavbárov)

| Model | Hlas | WER | s / veta |
|---|---|---:|---:|
| whisper-large-v3-turbo | Laura (macOS) | 5.3% | 2.01 |
| whisper-large-v3-turbo | Piper lili | 31.7% | 2.00 |
| whisper-large-v3-mlx | Laura (macOS) | 10.8% | 2.94 |
| whisper-large-v3-mlx | Piper lili | 33.3% | 3.77 |

## Extrakcia denného výpisu (6 prepisov, overené polia)

*správne* = pole po validácii sedí; *zle* = prešla nesprávna hodnota; *chýba* = systém sa dopýta; *zastavené* = LLM tvrdil iné číslo, než zaznelo, a validácia ho zahodila.

| Model | Správne | Zle | Chýba | Zastavené | s / výpis |
|---|---:|---:|---:|---:|---:|
| qwen3:30b-a3b-instruct-2507-q4_K_M | 33/35 (94%) | 0 | 2 | 2 | 43.0 |
| qwen3:14b | 33/35 (94%) | 0 | 2 | 2 | 8.1 |
| gemma3:12b | 33/35 (94%) | 0 | 2 | 2 | 14.3 |

## Import úloh (2 dokumenty, 8 očakávaných úloh)

| Model | Nájdené úlohy | Správny termín | Navyše | s / dokument |
|---|---:|---:|---:|---:|
| qwen3:30b-a3b-instruct-2507-q4_K_M | 7/8 | 7/8 | 0 | 32.2 |
| qwen3:14b | 7/8 | 7/8 | 0 | 124.2 |
| gemma3:12b | 7/8 | 6/8 | 0 | 44.6 |
