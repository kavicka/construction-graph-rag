"""Texts of follow-up questions — templates, not the LLM: predictable, short and easy to read aloud.
Wording is gender-neutral („O koľkej sa ti začal pracovný deň?“ — what time did your day start?).

attempt 0 = first question, 1 = more specific after an unclear answer, 2+ = choice (buttons)."""

from typing import Any

from app.workforce.report_model import Gap, ReportData


def _fmt_hours(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _task_label(task: dict[str, Any] | None) -> str:
    if task is None:
        return "úloha"
    return f"„{task['title']}“"


def question_text(
    gap: Gap, attempt: int, data: ReportData, tasks: dict[str, dict[str, Any]]
) -> str:
    task = tasks.get(gap.task_id or "")
    label = _task_label(task)
    site = f" na stavbe {task['site_name']}" if task and task.get("site_name") else ""
    choices = attempt >= 2

    match gap.kind:
        case "absence":
            return (
                "Vyber dôvod neprítomnosti."
                if choices
                else "Z akého dôvodu dnes nebola práca? Dovolenka, choroba alebo niečo iné?"
            )
        case "arrival":
            if choices:
                return "Vyber čas začiatku pracovného dňa."
            if attempt == 1:
                return "Povedz prosím presný čas začiatku, napríklad o siedmej alebo 7:30."
            return "O koľkej sa ti dnes začal pracovný deň?"
        case "departure":
            if gap.detail == "order":
                return (
                    "Koniec pracovného dňa vychádza skôr ako začiatok. "
                    "O koľkej sa ti dnes skončil pracovný deň?"
                )
            if choices:
                return "Vyber čas konca pracovného dňa."
            if attempt == 1:
                return "Povedz prosím presný čas konca, napríklad o štvrtej alebo 16:30."
            return "O koľkej sa ti dnes skončil pracovný deň?"
        case "break":
            if choices:
                return "Vyber dĺžku prestávky."
            if attempt == 1:
                return "Koľko minút trvala prestávka? Ak nebola, stačí povedať nie."
            return "Bola dnes prestávka? Ak áno, ako dlhá?"
        case "task":
            if choices:
                return f"Úloha {label}{site}: vyber stav a počet hodín."
            parts = set(gap.detail.split(","))
            if "status" in parts:
                if attempt == 1:
                    return (
                        f"Úloha {label}: je hotová, rozpracovaná, alebo sa na nej dnes nerobilo? "
                        "A koľko hodín zabrala?"
                    )
                return (
                    f"K úlohe {label}{site} nemám žiadne údaje. Robilo sa na nej dnes? "
                    "Ak áno, koľko hodín a je hotová alebo rozpracovaná?"
                )
            if "reason" in parts:
                return f"Prečo sa dnes na úlohe {label} nerobilo?"
            if parts == {"hours"}:
                if attempt == 1:
                    return f"Stačí číslo — koľko hodín zabrala úloha {label}? Napríklad tri."
                return f"Koľko hodín ti dnes zabrala úloha {label}?"
            if parts == {"progress"}:
                if attempt == 1:
                    return (
                        f"Odhadni, koľko z úlohy {label} je hotové — napríklad polovica "
                        "alebo 30 percent."
                    )
                return f"Na koľko percent je úloha {label} hotová?"
            return f"Pri úlohe {label} chýba počet hodín a na koľko percent je hotová."
        case "extra":
            idx = gap.extra_index or 0
            desc = data.extra_work[idx].description if idx < len(data.extra_work) else "práca"
            if choices:
                return f"Vyber počet hodín: {desc}."
            return f"Koľko hodín zabrala práca: {desc}?"
        case "problems":
            if choices:
                return "Boli dnes problémy? Vyber alebo napíš."
            return (
                "Boli dnes nejaké problémy alebo prekážky? Napríklad počasie, chýbajúci "
                "materiál, pokazený stroj alebo úraz."
            )
        case "hours":
            worked = data.worked_hours or 0
            total = data.reported_hours
            if choices:
                return (
                    f"Rozdeľ odpracovaných {_fmt_hours(worked)} h medzi úlohy (súčet musí sedieť)."
                )
            if total > worked:
                return (
                    f"Pri úlohách je spolu {_fmt_hours(total)} h, ale pracovný čas vychádza "
                    f"len na {_fmt_hours(worked)} h. Ktorý údaj treba opraviť?"
                )
            return (
                f"Pracovný čas vychádza na {_fmt_hours(worked)} h, ale pri úlohách je spolu "
                f"{_fmt_hours(total)} h. Čo sa robilo vo zvyšnom čase?"
            )
    return "Doplň prosím chýbajúci údaj."
