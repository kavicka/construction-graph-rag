# Eval — chat nad firemnými údajmi a bezpečnostná brána

Dátum: 2026-10-08 · model odpovedí/klasifikátora: `qwen3:4b-instruct` · `uv run python -m eval.firma_eval`

## Summary (English)

Evaluation of the chat over company data (tasks, attendance, hours) and of the gate in front of it, on a synthetic company. Tables below are in Slovak.

- Owner questions with the expected facts and question type: 15/15.
- Access control (a worker sees only their own data, a plain member sees nothing): 8/8.
- Red-team (PIN and password requests, injection, a secret inside a document, conversation history): 21/21.
- Off-purpose questions answered with a fixed refusal: 7/7.
- False refusals of document questions: 0/6.
- Leaked canary secrets (PINs, hashes, e-mails, passwords): 0.

| Sada | Výsledok |
|---|---|
| Zlaté otázky majiteľa (fakty + typ otázky) | 15/15 |
| Prístup (pracovník len seba, member nič) | 8/8 |
| Red-team (PIN, heslá, injekcia, tajomstvo v dokumente, história) | 21/21 |
| Mimo účelu → pevné odmietnutie | 7/7 |
| Falošné odmietnutie otázok na dokumenty | 0/6 |
| **Úniky kanárikov (PIN, hash, e-maily, heslá)** | **0** |
| Latencia firemnej odpovede (medián) | 0.01 s |
