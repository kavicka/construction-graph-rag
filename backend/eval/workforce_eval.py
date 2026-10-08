"""Eval modulu Firma — výber lokálnych modelov podľa merania, nie podľa dojmu.

    uv run python -m eval.workforce_eval                 # STT + extrakcia + import
    uv run python -m eval.workforce_eval --only stt      # len prepis reči
    uv run python -m eval.workforce_eval --models qwen3:14b,gemma3:12b

STT: slovenské vety stavbárov → `say -v Laura` (macOS) a Piper `sk_SK-lili-medium`
→ Whisper turbo vs large-v3, WER na normalizovaných slovách + čas.

Extrakcia výpisu: prepisy s ručne overenými hodnotami (časy, hodiny, stavy úloh). Pre každý
model: koľko polí trafil (po validácii), koľko by bez validácie prešlo zle (LLM tvrdil iné
číslo než človek povedal) a koľko polí ostalo prázdnych (→ doplňujúca otázka).

Import úloh: texty od dodávateľov s očakávanými úlohami a termínmi; zhoda názvov (kľúčové
slová), správne termíny, odfiltrované halucinácie (úloha bez citátu v texte).

Výstup: eval/WORKFORCE.md + eval/results-workforce.json.
"""

import argparse
import json
import os
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_MODELS = ["qwen3:30b-a3b-instruct-2507-q4_K_M", "qwen3:14b", "gemma3:12b"]
STT_MODELS = ["mlx-community/whisper-large-v3-turbo", "mlx-community/whisper-large-v3-mlx"]

# --- STT ---------------------------------------------------------------------------------

STT_SENTENCES = [
    "Dnes som prišiel na stavbu o pol siedmej a odišiel o štvrtej.",
    "Murovali sme priečky na druhom podlaží, sú hotové asi na šesťdesiat percent.",
    "Tri hodiny som pripravoval debnenie na základovú dosku.",
    "Ráno nedoviezli piesok, tak sme hodinu čakali.",
    "Prestávka bola pol hodiny, obedovali sme na stavbe.",
    "Osádzal som okenné rámy v obývačke, tie sú hotové.",
    "Pokazila sa miešačka, Fero ju dve hodiny opravoval.",
    "Na hydroizolácii sme dnes nerobili, pršalo celé poobedie.",
    "Natiahol som káble do rozvádzača a osadil krabice v kuchyni.",
    "Zajtra treba objednať dvadsať vriec lepidla na obklad.",
]
STT_PROMPT = "Stavby: Rovinka, Pezinok. Úlohy: murovanie priečok, debnenie, hydroizolácia."


def _normalize_words(text: str) -> list[str]:
    from app.workforce.sk_numbers import normalize

    return normalize(text).split()


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = _normalize_words(reference), _normalize_words(hypothesis)
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
            prev, d[j] = d[j], cur
    return d[len(hyp)] / max(1, len(ref))


def _clips(tmp: Path) -> list[tuple[str, str, Path]]:
    from app.workforce.speech import _voice

    clips = []
    for i, sentence in enumerate(STT_SENTENCES):
        say = tmp / f"say{i}.aiff"
        subprocess.run(["say", "-v", "Laura", "-o", str(say), sentence], check=True)
        clips.append(("Laura (macOS)", sentence, say))
        piper = tmp / f"piper{i}.wav"
        with wave.open(str(piper), "wb") as wf:
            _voice().synthesize_wav(sentence, wf)
        clips.append(("Piper lili", sentence, piper))
    return clips


def eval_stt() -> list[dict[str, Any]]:
    from app.workforce import speech
    from app.workforce.config import get_workforce_settings

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        clips = _clips(Path(tmp))
        for model in STT_MODELS:
            os.environ["RAG_STT_MODEL"] = model
            get_workforce_settings.cache_clear()
            speech.transcribe(clips[0][2], STT_PROMPT)  # zahriatie (načítanie modelu)
            for voice in ("Laura (macOS)", "Piper lili"):
                errs, secs = [], 0.0
                for v, sentence, path in clips:
                    if v != voice:
                        continue
                    t0 = time.perf_counter()
                    text = speech.transcribe(path, STT_PROMPT)
                    secs += time.perf_counter() - t0
                    errs.append(wer(sentence, text))
                rows.append({"model": model.split("/")[-1], "voice": voice,
                             "wer": sum(errs) / len(errs), "seconds": secs / len(errs)})
                print(f"STT {model.split('/')[-1]:28} {voice:14} WER {rows[-1]['wer']:.1%} "
                      f"{rows[-1]['seconds']:.2f} s/vetu")
    os.environ.pop("RAG_STT_MODEL", None)
    return rows


# --- extrakcia výpisu --------------------------------------------------------------------

TASKS = [
    {"id": "t1", "title": "Murovanie priečok 2. NP", "site_name": "RD Rovinka",
     "description": ""},
    {"id": "t2", "title": "Osadenie okenných rámov", "site_name": "RD Rovinka",
     "description": ""},
    {"id": "t3", "title": "Betonáž základovej dosky", "site_name": "RD Rovinka",
     "description": "debnenie, armovanie, betón C25/30"},
]


@dataclass
class Case:
    text: str
    expected: dict[str, Any]  # pole → hodnota; tasks: id → (status, hours, progress)
    tasks: dict[str, tuple[str | None, float | None, int | None]] = field(default_factory=dict)


CASES = [
    Case(
        "Dnes som prišiel o pol siedmej. Do obeda sme s Ferom murovali priečky na druhom "
        "podlaží, štyri hodiny, sú hotové tak na šesťdesiat percent. Poobede som tri hodiny "
        "pripravoval debnenie na základovú dosku. Odišiel som o pol štvrtej.",
        {"arrival": "06:30", "departure": "15:30", "break_minutes": None},
        {"t1": ("in_progress", 4, 60), "t3": ("in_progress", 3, None)},
    ),
    Case(
        "Začal som o siedmej, skončil o štvrtej, prestávka pol hodiny. Celý deň som osádzal "
        "okenné rámy, osem a pol hodiny, rámy sú hotové.",
        {"arrival": "07:00", "departure": "16:00", "break_minutes": 30},
        {"t2": ("done", 8.5, 100)},
    ),
    Case(
        "Od siedmej do pol štvrtej. Bez prestávky. Na priečkach sme dnes nerobili, chýbali "
        "tehly. Robil som betonáž dosky, je hotová, osem hodín.",
        {"arrival": "07:00", "departure": "15:30", "break_minutes": 0},
        {"t1": ("not_started", None, None), "t3": ("done", 8, 100)},
    ),
    Case(
        "Prišiel som o štvrť na sedem a odišiel o tri štvrte na štyri. Mali sme hodinu obed. "
        "Muroval som priečky päť hodín, sú tak na polovicu.",
        {"arrival": "06:15", "departure": "15:45", "break_minutes": 60},
        {"t1": ("in_progress", 5, 50)},
    ),
    Case(
        "Dnes som nebol v práci, mal som dovolenku.",
        {"absent": True},
        {},
    ),
    Case(
        "Ráno o šiestej som otvoril stavbu, potom som šesť hodín armoval dosku, je asi na "
        "tridsať percent. Okná dnes neprišli, takže rámy sa nerobili. Domov o pol tretej, "
        "prestávka dvadsať minút.",
        {"arrival": "06:00", "departure": "14:30", "break_minutes": 20},
        {"t3": ("in_progress", 6, 30), "t2": ("not_started", None, None)},
    ),
]


def _score_case(case: Case, data: Any, raw: Any) -> dict[str, int]:
    """correct / wrong (prešlo zle) / missing (→ otázka) / blocked (validácia zastavila zlé)."""
    s = {"correct": 0, "wrong": 0, "missing": 0, "blocked": 0}
    for key, want in case.expected.items():
        got = getattr(data, key)
        raw_val = getattr(raw, key, None) if raw is not None else None
        if got == want:
            s["correct"] += 1
        elif got is None:
            s["missing"] += 1
            if raw_val is not None and raw_val != want:
                s["blocked"] += 1
        else:
            s["wrong"] += 1
    for task_id, (status, hours, progress) in case.tasks.items():
        entry = data.entry(task_id)
        for name, want in (("status", status), ("hours", hours), ("progress", progress)):
            if want is None:
                continue
            got = getattr(entry, name) if entry else None
            same = got == want if name == "status" else (
                got is not None and float(got) == float(want))
            if same:
                s["correct"] += 1
            elif got is None:
                s["missing"] += 1
            else:
                s["wrong"] += 1
    return s


def eval_extraction(models: list[str]) -> list[dict[str, Any]]:
    from app.workforce import extract
    from app.workforce.config import get_workforce_settings
    from app.workforce.report_model import ReportData

    rows = []
    for model in models:
        os.environ["RAG_WORKFORCE_MODEL"] = model
        get_workforce_settings.cache_clear()
        raw_holder: dict[str, Any] = {}
        original = extract._llm_json

        def spy(prompt: str, system: str, schema: Any, _orig: Any = original,
                _holder: dict[str, Any] = raw_holder) -> Any:
            out = _orig(prompt, system, schema)
            _holder["raw"] = out
            return out

        extract._llm_json = spy  # type: ignore[assignment]
        try:
            extract.extract_report("Prišiel som o siedmej.", TASKS, ReportData(), "2026-10-08")
            total = {"correct": 0, "wrong": 0, "missing": 0, "blocked": 0}
            secs = 0.0
            for case in CASES:
                raw_holder.clear()
                t0 = time.perf_counter()
                data = extract.extract_report(case.text, TASKS, ReportData(), "2026-10-08")
                secs += time.perf_counter() - t0
                for k, v in _score_case(case, data, raw_holder.get("raw")).items():
                    total[k] += v
        finally:
            extract._llm_json = original  # type: ignore[assignment]
        fields = total["correct"] + total["wrong"] + total["missing"]
        rows.append({"model": model, **total, "fields": fields,
                     "accuracy": total["correct"] / fields, "seconds": secs / len(CASES)})
        print(f"EXTRAKCIA {model:36} správne {total['correct']}/{fields} "
              f"zle {total['wrong']} chýba {total['missing']} (zastavené {total['blocked']}) "
              f"{secs / len(CASES):.1f} s/výpis")
    os.environ.pop("RAG_WORKFORCE_MODEL", None)
    return rows


# --- import úloh -------------------------------------------------------------------------

DOCS = [
    (
        "Dobrý deň, posielam zhrnutie z kontrolného dňa 5. 10. 2026. Betonáž základovej dosky "
        "treba dokončiť do 20. 10. 2026. Po dokončení základov sa začne murovanie obvodových "
        "stien, termín do konca mesiaca. Elektroinštaláciu v 1. NP urobí Peter Kováč v termíne "
        "2.–6. 11. 2026. Hydroizoláciu spodnej stavby treba urobiť budúci týždeň. Faktúry "
        "uhraďte do 14 dní. S pozdravom, Ing. Novák",
        [("betonaz", "2026-10-20"), ("murovanie", "2026-10-31"), ("elektro", "2026-11-06"),
         ("hydroizol", "2026-10-16")],
    ),
    (
        "Harmonogram prác RD Pezinok:\n| Práca | Termín |\n|---|---|\n"
        "| Pokládka strešnej krytiny | do 24. 10. 2026 |\n"
        "| Klampiarske prvky a žľaby | 26.–28. 10. 2026 |\n"
        "| Sadrokartónové podhľady v podkroví | v 45. týždni |\n"
        "Poznámka: lešenie demontovať až po dokončení žľabov.",
        [("kryti", "2026-10-24"), ("zlab", "2026-10-28"), ("podhlad", "2026-11-06"),
         ("lesen", None)],
    ),
]


def eval_import(models: list[str]) -> list[dict[str, Any]]:
    from app.workforce import importer
    from app.workforce.config import get_workforce_settings
    from app.workforce.importer import Segment, Window
    from app.workforce.sk_dates import parse_term
    from app.workforce.sk_numbers import normalize

    professions = ["murár", "betonár", "elektrikár", "izolatér", "pokrývač", "klampiar",
                   "sadrokartonista", "pomocný robotník"]
    rows = []
    for model in models:
        os.environ["RAG_WORKFORCE_MODEL"] = model
        get_workforce_settings.cache_clear()
        found = expected = dates_ok = extra = 0
        secs = 0.0
        for text, want in DOCS:
            window = Window([Segment(text, 1)])
            t0 = time.perf_counter()
            items, doc_date = importer.extract_drafts(
                {"filename": "zadanie.pdf", "doc_id": None}, [window], professions)
            secs += time.perf_counter() - t0
            items = importer.dedupe(items)
            ref = doc_date or date(2026, 10, 8)
            matched: set[int] = set()
            for key, want_to in want:
                expected += 1
                hit = next((i for i, it in enumerate(items)
                            if key in normalize(it["title"]) and i not in matched), None)
                if hit is None:
                    continue
                matched.add(hit)
                found += 1
                term = parse_term(importer.expand_term(items[hit]["date_text"],
                                                       items[hit]["quote"]), ref)
                got_to = term.date_to.isoformat() if term.date_to else None
                dates_ok += got_to == want_to
            extra += len(items) - len(matched)
        rows.append({"model": model, "found": found, "expected": expected,
                     "dates_ok": dates_ok, "extra": extra, "seconds": secs / len(DOCS)})
        print(f"IMPORT {model:36} úlohy {found}/{expected} termíny {dates_ok}/{expected} "
              f"navyše {extra} {secs / len(DOCS):.1f} s/dokument")
    os.environ.pop("RAG_WORKFORCE_MODEL", None)
    return rows


def write_report(stt: list[dict[str, Any]], ext: list[dict[str, Any]],
                 imp: list[dict[str, Any]]) -> None:
    # čiastočný beh (--only) ponechá ostatné sekcie z minulého behu
    previous_path = EVAL_DIR / "results-workforce.json"
    if previous_path.exists():
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        stt = stt or previous.get("stt", [])
        ext = ext or previous.get("extraction", [])
        imp = imp or previous.get("import", [])
    lines =["# Eval modulu Firma (dochádzka, import úloh)", "",
             f"Spustené {date.today().isoformat()} na lokálnom stroji "
             "(`uv run python -m eval.workforce_eval`).", ""]
    if stt:
        lines += ["## Prepis reči (Whisper, slovenčina, 10 viet stavbárov)", "",
                  "| Model | Hlas | WER | s / veta |", "|---|---|---:|---:|"]
        lines += [f"| {r['model']} | {r['voice']} | {r['wer']:.1%} | {r['seconds']:.2f} |"
                  for r in stt]
        lines.append("")
    if ext:
        lines += ["## Extrakcia denného výpisu (6 prepisov, overené polia)", "",
                  "*správne* = pole po validácii sedí; *zle* = prešla nesprávna hodnota; "
                  "*chýba* = systém sa dopýta; *zastavené* = LLM tvrdil iné číslo, než "
                  "zaznelo, a validácia ho zahodila.", "",
                  "| Model | Správne | Zle | Chýba | Zastavené | s / výpis |",
                  "|---|---:|---:|---:|---:|---:|"]
        lines += [f"| {r['model']} | {r['correct']}/{r['fields']} ({r['accuracy']:.0%}) | "
                  f"{r['wrong']} | {r['missing']} | {r['blocked']} | {r['seconds']:.1f} |"
                  for r in ext]
        lines.append("")
    if imp:
        lines += ["## Import úloh (2 dokumenty, 8 očakávaných úloh)", "",
                  "| Model | Nájdené úlohy | Správny termín | Navyše | s / dokument |",
                  "|---|---:|---:|---:|---:|"]
        lines += [f"| {r['model']} | {r['found']}/{r['expected']} | "
                  f"{r['dates_ok']}/{r['expected']} | {r['extra']} | {r['seconds']:.1f} |"
                  for r in imp]
        lines.append("")
    (EVAL_DIR / "WORKFORCE.md").write_text("\n".join(lines), encoding="utf-8")
    (EVAL_DIR / "results-workforce.json").write_text(
        json.dumps({"stt": stt, "extraction": ext, "import": imp}, indent=2,
                   ensure_ascii=False), encoding="utf-8")
    print(f"→ {EVAL_DIR / 'WORKFORCE.md'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["stt", "extraction", "import"])
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    args = parser.parse_args()
    models = [m for m in args.models.split(",") if m]
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["RAG_DATA_DIR"] = tmp
        os.environ["RAG_TRACING"] = "false"  # dočasná DB nemá tabuľku spanov
        voices = Path(__file__).resolve().parents[2] / "data" / "voices"
        if voices.exists():
            (Path(tmp) / "voices").symlink_to(voices)
        stt = eval_stt() if args.only in (None, "stt") else []
        ext = eval_extraction(models) if args.only in (None, "extraction") else []
        imp = eval_import(models) if args.only in (None, "import") else []
    write_report(stt, ext, imp)


if __name__ == "__main__":
    main()
