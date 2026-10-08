# Eval — chat nad firemnými údajmi a bezpečnostná brána

Dátum: 2026-10-08 · model odpovedí/klasifikátora: `qwen3:4b-instruct` · `uv run python -m eval.firma_eval`

| Sada | Výsledok |
|---|---|
| Zlaté otázky majiteľa (fakty + typ otázky) | 15/15 |
| Prístup (pracovník len seba, member nič) | 8/8 |
| Red-team (PIN, heslá, injekcia, tajomstvo v dokumente, história) | 21/21 |
| Mimo účelu → pevné odmietnutie | 7/7 |
| Falošné odmietnutie otázok na dokumenty | 0/6 |
| **Úniky kanárikov (PIN, hash, e-maily, heslá)** | **0** |
| Latencia firemnej odpovede (medián) | 0.01 s |
