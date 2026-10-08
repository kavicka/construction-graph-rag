# rag — lokálna knižnica dokumentov so znalostným grafom

Súkromný RAG nad firemnými dokumentmi: hľadá **súvislosti medzi dokumentmi**, odpovedá
s citáciami, ktoré otvoria presné miesto v PDF, a na požiadanie vytvorí **branded dokument**
(report, cenovú ponuku, memo) z tých istých zdrojov. Všetko beží lokálne.

Podrobný popis, architektúra a výsledky evalu: [README.md](README.md) (anglicky).
Plán a odôvodnenie rozhodnutí: [PLAN.md](PLAN.md). Roadmapa: [ROADMAP.md](ROADMAP.md).

## Spustenie

```bash
docker compose up -d --build        # http://localhost:8080
```

alebo natívne na macOS (rýchlejšie, Apple GPU):

```bash
brew install ollama tesseract tesseract-lang pango
make models
cd backend && uv sync && cd ../frontend && pnpm install
make dev                            # backend :8000, frontend :5173
```

## Čo vie

- **Dokumenty:** PDF (aj skeny), DOCX, PPTX, XLSX, HTML, Markdown; upload alebo priečinok
  `data/inbox` (nové sa zaindexujú, zmenené preindexujú, zmazané odstránia).
- **Chat:** citácie `[n]` otvoria PDF so zvýraznenou pasážou; overenie čísel aj viet voči
  zdrojom; doplňujúce otázky („a pokuta?“) sa prepíšu na samostatné.
- **Režimy:** presný filter nad tabuľkami („vzorky pod 38 MPa“), deep research (mikroskop
  pri vstupe), témy celej knižnice („aké sú hlavné témy dokumentov?“), tvorba dokumentu
  („Priprav report o …“) — vzhľad PDF preberá tému aplikácie (system-ui + JetBrains Mono), údaje firmy sú v **Nastaveniach**.
- **Prevádzka:** Traces (každý krok, tokeny, latencia), palec hore/dole, MCP server pre
  Claude Desktop/Code, voliteľné prihlasovanie so skupinami a prístupom k dokumentom.

## Testy a eval

```bash
make test        # pytest + build frontendu
make lint        # ruff, mypy --strict, oxlint
make eval        # ablácie retrievalu → backend/eval/REPORT.md
make eval-full   # + kvalita odpovedí (LLM judge) + prompt-injection útoky
```
