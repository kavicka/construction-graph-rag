# rag: súkromná knižnica dokumentov a pomocník pre stavebnú firmu

Slovenský prehľad pre majiteľov malých firiem. Technické detaily a merania sú v anglickom
[README.md](README.md). Všetky firmy a osoby v projekte aj v ukážkových dátach sú vymyslené.

## Čo to robí pre stavebnú alebo remeselnú firmu

- **Odpovedá z vašich dokumentov.** Zmluvy, rozpočty, stavebné denníky, kontrolné protokoly,
  návody. Spýtate sa po slovensky a dostanete odpoveď s odkazom: kliknutím sa otvorí presné
  miesto v PDF. Ak odpoveď v dokumentoch nie je, systém to povie, nevymýšľa si.
- **Nájde súvislosti medzi dokumentmi.** Napríklad ktorý subdodávateľ sa objavuje v zmluve,
  zozname dodávateľov aj v záručných listoch.
- **Pripraví dokument.** „Priprav report o kvalite betónu“ vytvorí PDF s hlavičkou a údajmi
  vašej firmy z tých istých podkladov.
- **Pomáha s dennou prevádzkou (modul Firma).** Pracovník na stavbe nahovorí denný výpis do
  mobilu po slovensky, systém sa dopýta na všetko, čo chýba, a vy dostanete PDF. Úlohy
  môžete prideliť na tabuli alebo ich nechať navrhnúť z dokumentu od dodávateľa (s citáciou
  zdroja, vy schvaľujete). Časy, hodiny a termíny počíta program, nie jazykový model, a
  čo model povie, sa overuje voči tomu, čo pracovník naozaj povedal.
- **Pracovník vidí len svoje.** Prihlasuje sa menom a PIN-om a v chate sa môže pýtať len na
  vlastné úlohy a hodiny.

## Vaše dáta zostávajú u vás

Všetko beží na jednom počítači vo firme: spracovanie dokumentov, vyhľadávanie aj jazykový
model (cez Ollama). Do cloudu sa nič neposiela, kým si to sami nenastavíte. Prihlasovanie
je voliteľné, prístup sa dá nastaviť po jednotlivých dokumentoch a heslá či PIN-y chat
nikdy nevypíše.

## Vyskúšať si to

```bash
make demo
```

Príkaz vytvorí ukážku z vymyslenej firmy a spustí aplikáciu; vypíše adresu a prihlasovacie
údaje. Potrebujete [Ollama](https://ollama.com), asi 4 GB na stiahnutie modelov, macOS alebo
Linux. Hlasové funkcie vyžadujú Mac s čipom Apple Silicon a voliteľnú inštaláciu
`make install-voice` (Piper je pod licenciou GPL-3, preto sa neinštaluje automaticky);
bez nich funguje všetko ostatné písomne. Rozhranie aplikácie je po slovensky.

Alternatívne `docker compose up -d --build` (http://localhost:8080), podrobnosti v
[README.md](README.md#quick-start).

## Spolupráca

Autor: [Adam Krampl](https://krampl.dev). Kód vznikol s pomocou Claude Code. Licencia MIT,
licencie použitých modelov sú v [THIRD_PARTY.md](THIRD_PARTY.md). Roadmapa:
[ROADMAP.md](ROADMAP.md). Pôvodný slovenský návrhový denník: [docs/sk/design-log.md](docs/sk/design-log.md).
