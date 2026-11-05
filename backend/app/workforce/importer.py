"""Import of tasks from a supplier document → proposals for owner approval.

The document first goes through the regular ingest into the RAG library (Docling + OCR, so it is
also searchable in chat). Once it is parsed:

1. the text (chunks + tables in markdown) is split into windows of ~3500 characters,
2. from each window the LLM extracts tasks with a VERBATIM quote and the verbatim deadline text,
3. the quote is verified against the window (fuzzy ≥ 90) — a task without evidence in the document
is dropped,
4. duplicates across windows are merged, deadlines recomputed by sk_dates, „po X“ (after X) is
paired with another task as a dependency, the worker is suggested by assign (code),
5. for a new document version the proposals are compared with tasks from the previous version: new /
   changed / cancelled / unchanged."""

import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from app import db
from app.config import get_settings
from app.workforce import store
from app.workforce.assign import suggest_worker
from app.workforce.extract import Short, Text, _llm_json, bounded, bounded_list
from app.workforce.pdf_locate import locate_quote
from app.workforce.sk_dates import DATE_RE, parse_term
from app.workforce.sk_numbers import normalize

logger = logging.getLogger(__name__)

WINDOW_CHARS = 3500
QUOTE_MIN_SCORE = 90
DUPLICATE_SCORE = 0.9
MATCH_SCORE = 0.8  # new version: proposal ↔ existing task
DEPENDENCY_SCORE = 0.78
PARSE_WAIT_S = 30 * 60
PARSED_STATUSES = ("tables", "context", "embedding", "graph", "done")


class _XTask(BaseModel):
    title: str = Field(max_length=200)
    description: str = bounded(500)
    work_type: str = bounded(60, "iné")
    person: Text | None = None
    date_text: Text | None = None
    after: Text | None = None
    quote: str = bounded(500)


class _XDocument(BaseModel):
    document_date: Short | None = None
    tasks: list[_XTask] = bounded_list(40)


IMPORT_MAX_TOKENS = 3072  # a window may list many tasks, each with a quote


IMPORT_SYSTEM = """Z dokumentu (zadanie prác od dodávateľa, investora alebo stavbyvedúceho) vytiahni
jednotlivé pracovné úlohy pre stavebnú firmu. Text môže byť súvislý (e-mail, zápis z porady),
tabuľka alebo zoznam a nemusí byť presný. Pravidlá:
- Jedna úloha = jedna konkrétna práca. Zložené vety rozdeľ: „omietky na 2. NP a potom obklady
  v kúpeľniach“ = 2 úlohy.
- title: krátky výstižný názov (max 80 znakov), napr. „Murovanie priečok 2. NP“.
- description: podrobnosti z textu (miesto, množstvo, materiál); nič nepridávaj.
- work_type: presne jedna profesia zo zoznamu, alebo „iné“.
- person: meno osoby, ktorá to má robiť — len ak ho text výslovne uvádza.
- date_text: DOSLOVNÝ text termínu z dokumentu („do 20. 10.“, „budúci týždeň“), inak null.
- after: DOSLOVNE, na čo úloha čaká („po dokončení základov“), inak null.
- quote: DOSLOVNÁ veta alebo riadok z dokumentu, z ktorého úloha pochádza — skopíruj presne.
- Vynechaj pozdravy, platobné podmienky, ceny a všeobecné informácie — len práce.
- document_date: dátum vystavenia dokumentu ako YYYY-MM-DD, ak je v texte uvedený, inak null."""


@dataclass
class Segment:
    text: str
    page: int
    boxes: list[list[float]] = field(default_factory=list)


@dataclass
class Window:
    segments: list[Segment]

    @property
    def text(self) -> str:
        return "\n\n".join(s.text for s in self.segments)

    def prompt_text(self) -> str:
        out, page = [], None
        for s in self.segments:
            if s.page != page:
                out.append(f"[strana {s.page}]")
                page = s.page
            out.append(s.text)
        return "\n".join(out)


def document_segments(doc_id: str) -> list[Segment]:
    """Paragraphs (not chunks) — the task quote is highlighted in the PDF exactly on its
    paragraph."""
    from docling_core.types.doc import TableItem

    from app.index.chunker import item_boxes, load_document

    document = load_document(doc_id)
    segments: list[Segment] = []
    for item, _level in document.iterate_items():
        if isinstance(item, TableItem):
            continue  # tables below the full markdown
        text = (getattr(item, "text", "") or "").strip()
        if not text:
            continue
        provs = getattr(item, "prov", None) or []
        page = provs[0].page_no if provs else 1
        segments.append(Segment(text, page, item_boxes(document, [item])))
    for t in db.list_tables(doc_id):
        md = Path(t["markdown_path"])
        if md.exists():
            text = md.read_text(encoding="utf-8")
            title = f"Tabuľka: {t['caption']}\n" if t.get("caption") else ""
            segments.append(
                Segment(
                    title + text,
                    t["page_no"] or 1,
                    json.loads(t["boxes_json"]) if t.get("boxes_json") else [],
                )
            )
    segments.sort(key=lambda s: s.page)
    return segments


def make_windows(segments: list[Segment], limit: int = WINDOW_CHARS) -> list[Window]:
    windows: list[Window] = []
    current: list[Segment] = []
    size = 0
    for seg in segments:
        # a segment that is too long (a big table) is split by rows
        pieces = [seg]
        if len(seg.text) > limit:
            pieces, buf = [], ""
            for line in seg.text.splitlines():
                if len(buf) + len(line) > limit and buf:
                    pieces.append(Segment(buf, seg.page, seg.boxes))
                    buf = ""
                buf += line + "\n"
            if buf.strip():
                pieces.append(Segment(buf, seg.page, seg.boxes))
        for piece in pieces:
            if size + len(piece.text) > limit and current:
                windows.append(Window(current))
                current, size = [], 0
            current.append(piece)
            size += len(piece.text)
    if current:
        windows.append(Window(current))
    return windows


def verify_quote(quote: str, window: Window) -> Segment | None:
    """The quote must really be in the window (tolerant to OCR/whitespace) → segment with its
    page."""
    q = normalize(quote)
    if len(q) < 10:
        return None
    best, best_score = None, 0.0
    for seg in window.segments:
        score = fuzz.partial_ratio(q, normalize(seg.text))
        if score > best_score:
            best, best_score = seg, score
    return best if best_score >= QUOTE_MIN_SCORE else None


def _document_date(raw: str | None, text: str) -> date | None:
    """LLM document date only if the day and month actually appear in the text."""
    if not raw:
        return None
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        return None
    for m in DATE_RE.finditer(text):
        if int(m.group(1)) == d.day and int(m.group(2)) == d.month:
            return d
    return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


STEM_LEN = 5


def stem_text(text: str) -> str:
    """Rough Slovak "stem" — the first 5 characters of the word (základov ↔ základovej)."""
    return " ".join(t[:STEM_LEN] for t in normalize(text).split())


# Whether the embedding model's cosine may count toward task-title similarity (the thresholds
# below were set on bge-m3). multilingual-e5-small is anisotropic: unrelated Slovak task titles
# score 0.90–0.94 („Betonáž stropu nad 4. NP“ ↔ „Skúška pevnosti betónu…“), so with it every new
# task looked like a duplicate → fuzzy only. Same idea as compose.CATEGORY_SIM_FLOOR.
TASK_COSINE: dict[str, bool] = {
    "BAAI/bge-m3": True,
    "intfloat/multilingual-e5-small": False,
}


# Titles naming different numbers are different work however similar the words are:
# token_set fuzzy rates „Betonáž stropu nad 4. NP“ ↔ „Skúška pevnosti betónu stropu nad 2. NP“
# at 0.95 (one stem set is almost a subset of the other).
NUMBER_RE = re.compile(r"\d+")
NUMBER_MISMATCH_CAP = 0.5


def _cosine_usable(model_name: str) -> bool:
    return TASK_COSINE.get(model_name, True)


class Similarity:
    """Similarity of task titles: max(fuzzy on words, fuzzy on stems, embedding cosine). Fuzzy
    only without an embedder or with a model whose cosine does not separate titles."""

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.stems = [stem_text(t) for t in texts]
        self.numbers = [set(NUMBER_RE.findall(t)) for t in texts]
        self.vectors: list[list[float]] | None = None
        if not _cosine_usable(get_settings().effective_embedding_model):
            return
        try:
            from app.index.embedder import get_embedder

            if texts:
                self.vectors = [list(v) for v in get_embedder().embed_passages(texts)]
        except Exception:  # noqa: BLE001 — the embedder is a bonus, not a requirement
            logger.warning("embeddingy pre podobnosť úloh nedostupné — len fuzzy")

    def score(self, i: int, j: int) -> float:
        fuzzy = (
            max(
                fuzz.token_set_ratio(normalize(self.texts[i]), normalize(self.texts[j])),
                fuzz.token_set_ratio(self.stems[i], self.stems[j]),
            )
            / 100
        )
        score = (
            fuzzy if self.vectors is None else max(fuzzy, _cosine(self.vectors[i], self.vectors[j]))
        )
        a, b = self.numbers[i], self.numbers[j]
        if a and b and not a & b:
            return min(score, NUMBER_MISMATCH_CAP)
        return score


STOP_AFTER = re.compile(
    r"\b(po|az|potom|ked|bude|budu|budu|dokonceni\w*|ukonceni\w*|skonceni\w*|hotov\w*"
    r"|zrealizovani\w*|vykonani\w*|prace|praci)\b"
)


def _after_key(after: str) -> str:
    return re.sub(r"\s+", " ", STOP_AFTER.sub(" ", normalize(after))).strip()


@dataclass
class Draft:
    data: dict[str, Any]
    kind: str = "new"
    target_task_id: str | None = None
    decision: str = "pending"


def extract_drafts(
    source: dict[str, Any],
    windows: list[Window],
    professions: list[str],
    on_progress: Any = None,
) -> tuple[list[dict[str, Any]], date | None]:
    """LLM per window → verified raw tasks (quote, page) + document date."""
    raw: list[dict[str, Any]] = []
    doc_date: date | None = None
    prof_line = ", ".join(professions)
    pdf_path = _source_pdf(source)
    for i, window in enumerate(windows):
        prompt = (
            f"Profesie: {prof_line}\n\nDokument „{source['filename']}“ "
            f'(časť {i + 1}/{len(windows)}):\n"""\n{window.prompt_text()}\n"""'
        )
        x = _llm_json(prompt, IMPORT_SYSTEM, _XDocument, max_tokens=IMPORT_MAX_TOKENS)
        if on_progress:
            on_progress((i + 1) / len(windows))
        if x is None:
            continue
        if doc_date is None:
            doc_date = _document_date(x.document_date, window.text)
        for t in x.tasks:
            title = t.title.strip()[:120]
            seg = verify_quote(t.quote, window)
            if not title or seg is None:
                logger.info("úloha bez overeného citátu zahodená: %r", title)
                continue
            work_type = t.work_type.strip()
            if work_type not in professions:
                work_type = "iné"
            raw.append(
                {
                    "title": title,
                    "description": t.description.strip()[:1000],
                    "work_type": work_type,
                    "person": (t.person or "").strip() or None,
                    "date_text": (t.date_text or "").strip() or None,
                    "after_text": (t.after or "").strip() or None,
                    "quote": t.quote.strip()[:600],
                    "page": seg.page,
                    # quote lines in the PDF text layer; otherwise the block from Docling
                    "boxes": (pdf_path and locate_quote(pdf_path, seg.page, t.quote))
                    or seg.boxes[:8],
                }
            )
    return raw, doc_date


def _source_pdf(source: dict[str, Any]) -> Path | None:
    from app.ingest.store import document_path

    doc = db.get_document(source["doc_id"]) if source.get("doc_id") else None
    if doc is None or (doc.get("format") or "pdf") != "pdf":
        return None
    return document_path(doc["id"], "pdf")


def dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(items) < 2:
        return items
    sim = Similarity([f"{i['title']} {i['description'][:120]}" for i in items])
    keep: list[int] = []
    for i, item in enumerate(items):
        dup = next(
            (
                k
                for k in keep
                if sim.score(i, k) >= DUPLICATE_SCORE and items[k]["work_type"] == item["work_type"]
            ),
            None,
        )
        if dup is None:
            keep.append(i)
            continue
        kept = items[dup]
        # fill in missing data from the duplicate (e.g. a deadline from another part of the
        # document)
        for key in ("date_text", "after_text", "person"):
            if not kept.get(key) and item.get(key):
                kept[key] = item[key]
    return [items[k] for k in keep]


def build_drafts(
    source: dict[str, Any],
    items: list[dict[str, Any]],
    doc_date: date | None,
    *,
    today: date | None = None,
) -> list[Draft]:
    today = today or date.today()
    ref = doc_date or today
    site_id = source["site_id"]
    workers = store.list_workers(active_only=True)
    all_tasks = store.list_tasks()
    site_tasks = [t for t in all_tasks if t["site_id"] == site_id and t["status"] != "cancelled"]

    drafts: list[Draft] = []
    for item in items:
        item["date_text"] = expand_term(item["date_text"], item["quote"])
        term = parse_term(item["date_text"], ref)
        start, end = term.date_from, term.date_to
        derived_start = bool(end and not start)
        if end and not start:
            start = min(max(ref, today), end)
        sug = suggest_worker(
            person=item["person"],
            work_type=item["work_type"],
            site_id=site_id,
            date_from=start,
            date_to=end,
            workers=workers,
            tasks=all_tasks,
        )
        data = {
            **item,
            "date_from": start.isoformat() if start else None,
            "date_to": end.isoformat() if end else None,
            "date_how": term.how,
            # start computed from the document date — a new version must not report it as a change
            "date_from_derived": derived_start,
            "depends_on": None,
            "assignees": [sug.user_id] if sug.user_id else [],
            "assign_reason": sug.reason,
            "assign_basis": sug.basis,
        }
        drafts.append(Draft(data))

    _link_dependencies(drafts, site_tasks)
    for d in drafts:
        score_draft(d.data)
    return drafts


def flag_existing(drafts: list[Draft], existing: list[dict[str, Any]]) -> None:
    """A new task from the document that already exists on the site (the same defect in both the
    record and the protocol)."""
    open_tasks = [t for t in existing if t["status"] in ("todo", "in_progress")]
    new = [d for d in drafts if d.kind == "new"]
    if not open_tasks or not new:
        return
    sim = Similarity([d.data["title"] for d in new] + [t["title"] for t in open_tasks])
    for i, draft in enumerate(new):
        best = max(range(len(open_tasks)), key=lambda j: sim.score(i, len(new) + j))
        if sim.score(i, len(new) + best) >= MATCH_SCORE:
            task = open_tasks[best]
            draft.data["duplicate_of"] = {"task": task["id"], "title": task["title"]}
            score_draft(draft.data)


def _link_dependencies(drafts: list[Draft], site_tasks: list[dict[str, Any]]) -> None:
    """„po dokončení základov“ (after the foundations are finished) → the most similar other task
    from the import or the site."""
    pending = [(i, d) for i, d in enumerate(drafts) if d.data.get("after_text")]
    if not pending:
        return
    titles = [d.data["title"] for d in drafts] + [t["title"] for t in site_tasks]
    keys = [_after_key(d.data["after_text"]) for _, d in pending]
    sim = Similarity(titles + keys)
    n_titles = len(titles)
    for k, (i, draft) in enumerate(pending):
        best, best_score = None, 0.0
        for j in range(n_titles):
            if j == i:
                continue
            score = sim.score(n_titles + k, j)
            if score > best_score:
                best, best_score = j, score
        if best is None or best_score < DEPENDENCY_SCORE:
            continue
        if best < len(drafts):
            draft.data["depends_on"] = {"draft": best, "title": drafts[best].data["title"]}
        else:
            task = site_tasks[best - len(drafts)]
            draft.data["depends_on"] = {"task": task["id"], "title": task["title"]}


def draft_issues(data: dict[str, Any]) -> list[str]:
    issues = []
    if not (data.get("date_from") and data.get("date_to")) and not data.get("depends_on"):
        issues.append("chýba termín" if not data.get("date_to") else "chýba začiatok")
    if data.get("date_from") and data.get("date_to") and data["date_from"] > data["date_to"]:
        issues.append("začiatok je po termíne")
    if not data.get("title", "").strip():
        issues.append("chýba názov")
    return issues


def score_draft(data: dict[str, Any]) -> None:
    issues = draft_issues(data)
    if not data.get("assignees"):
        issues.append("nikto nenavrhnutý")
    if dup := data.get("duplicate_of"):
        # soft warning — does not block use, but „Schváliť všetko“ (Approve all) skips it
        issues.append(f"podobná úloha už existuje: {dup['title']}")
    data["issues"] = issues
    # high = exact date from the document and a suggested worker; relative deadline / dependency =
    # medium; anything missing = low (the owner must fill it in)
    if issues:
        data["confidence"] = "low"
    elif data.get("date_how") == "absolute":
        data["confidence"] = "high"
    else:
        data["confidence"] = "medium"


# the description is not compared — the LLM words it differently in every version and would report
# false changes
DIFF_FIELDS = ("title", "work_type", "date_from", "date_to")
TERM_PREFIX_RE = re.compile(
    r"(?:^|\s)(do|od|najneskor(?:\s+do)?|az|termin(?:\s+dokoncenia)?)\s*[:\-]?\s*$"
)


TERM_START_RE = re.compile(r"(do|od|najneskor|az|termin|v|vo|o|za|pocas|behom)\b")


def expand_term(date_text: str | None, quote: str) -> str | None:
    """The LLM sometimes drops the preposition („23. 10.“ instead of „do 23. 10.“) — add it from the
    quote."""
    if not date_text:
        return date_text
    norm_quote, norm_term = normalize(quote), normalize(date_text)
    pos = norm_quote.find(norm_term)
    if pos <= 0 or TERM_START_RE.match(norm_term):
        return date_text  # the preposition is already there, or it is not in the quote
    m = TERM_PREFIX_RE.search(norm_quote[max(0, pos - 25) : pos])
    return f"{m.group(1)} {date_text}" if m else date_text


def _same(field: str, old: Any, new: Any) -> bool:
    if field == "title":
        # a model typo („Betónáž“) is not a title change
        return fuzz.ratio(normalize(str(old or "")), normalize(str(new or ""))) >= 90
    return normalize(str(old or "")) == normalize(str(new or ""))


def diff_with_previous(drafts: list[Draft], previous_tasks: list[dict[str, Any]]) -> list[Draft]:
    """New document version: proposals ↔ tasks from earlier versions (greedy 1:1 by similarity)."""
    old = [t for t in previous_tasks if t["status"] != "cancelled"]
    if not old:
        return drafts
    texts = [d.data["title"] for d in drafts] + [t["title"] for t in old]
    sim = Similarity(texts)
    n = len(drafts)
    pairs = sorted(
        ((sim.score(i, n + j), i, j) for i in range(n) for j in range(len(old))), reverse=True
    )
    used_new: set[int] = set()
    used_old: set[int] = set()
    for score, i, j in pairs:
        if score < MATCH_SCORE:
            break
        if i in used_new or j in used_old:
            continue
        used_new.add(i)
        used_old.add(j)
        task, draft = old[j], drafts[i]
        skip = {"date_from"} if draft.data.get("date_from_derived") else set()
        changes = {
            f: [task.get(f) or None, draft.data.get(f) or None]
            for f in DIFF_FIELDS
            # a missing value in the new version is not a change — the original stays
            if f not in skip and draft.data.get(f) and not _same(f, task.get(f), draft.data.get(f))
        }
        draft.target_task_id = task["id"]
        draft.data["assignees"] = task["assignees"] or draft.data["assignees"]
        if task["assignees"]:
            draft.data["assign_reason"] = "ponechané z pôvodnej úlohy"
        # whatever did not change stays exactly as in the original task (including the description)
        for f in (*DIFF_FIELDS, "description"):
            if f not in changes:
                draft.data[f] = task.get(f)
        draft.data["changes"] = changes
        draft.data["task_status"] = task["status"]
        score_draft(draft.data)
        if changes:
            draft.kind = "changed"
        else:
            draft.kind, draft.decision = "unchanged", "approved"

    for j, task in enumerate(old):
        if j in used_old:
            continue
        warning = {"in_progress": "úloha je rozpracovaná", "done": "úloha je už hotová"}.get(
            task["status"]
        )
        drafts.append(
            Draft(
                data={
                    "title": task["title"],
                    "description": task["description"],
                    "work_type": task["work_type"],
                    "date_from": task["date_from"],
                    "date_to": task["date_to"],
                    "assignees": task["assignees"],
                    "quote": task.get("source_quote") or "",
                    "page": task.get("source_page"),
                    "issues": [warning] if warning else [],
                    "confidence": "medium",
                    "task_status": task["status"],
                },
                kind="cancelled",
                target_task_id=task["id"],
            )
        )
    return drafts


def suggest_previous(site_id: str, filename: str) -> dict[str, Any] | None:
    """Same document in a new version? A similar file name on the same site."""

    def stem(name: str) -> str:
        base = normalize(Path(name).stem)
        return re.sub(r"\b(v\d+|verzia \d+|final\w*|nov\w*|aktualiz\w*|\d+)\b", " ", base).strip()

    target = stem(filename)
    for src in store.list_sources(site_id):
        if src["status"] == "applied" and fuzz.ratio(stem(src["filename"]), target) >= 80:
            return src
    return None


def _wait_for_parse(doc_id: str) -> None:
    deadline = time.monotonic() + PARSE_WAIT_S
    while time.monotonic() < deadline:
        doc = db.get_document(doc_id)
        if doc is None:
            raise RuntimeError("dokument bol zmazaný")
        if doc["status"] == "error":
            raise RuntimeError(f"dokument sa nepodarilo spracovať: {doc['error']}")
        if doc["status"] in PARSED_STATUSES:
            return
        time.sleep(1.0)
    raise RuntimeError("spracovanie dokumentu trvá príliš dlho")


def run_import(source_id: str) -> None:
    """Runs in a thread: waits for the document to be parsed → task proposals → status review."""
    source = store.get_source(source_id)
    if source is None:
        return
    try:
        if not source["doc_id"]:
            raise RuntimeError("zdroj nemá dokument")
        _wait_for_parse(source["doc_id"])
        store.set_source_status(source_id, "extracting", progress=0.05)
        windows = make_windows(document_segments(source["doc_id"]))
        if not windows:
            raise RuntimeError("v dokumente sa nenašiel žiadny text")
        items, doc_date = extract_drafts(
            source,
            windows,
            store.list_professions(),
            on_progress=lambda p: store.set_source_status(
                source_id, "extracting", progress=0.05 + 0.85 * p
            ),
        )
        drafts = build_drafts(source, dedupe(items), doc_date)
        chain: list[str] = []
        if source["previous_id"]:
            chain = store.source_chain(source["previous_id"])
            drafts = diff_with_previous(drafts, store.list_tasks(source_ids=chain))
        flag_existing(
            drafts,
            [
                t
                for t in store.list_tasks(site_id=source["site_id"])
                if t.get("source_id") not in chain
            ],
        )
        store.replace_drafts(
            source_id,
            [
                {
                    "kind": d.kind,
                    "target_task_id": d.target_task_id,
                    "data": d.data,
                    "decision": d.decision,
                }
                for d in drafts
            ],
        )
        store.set_source_status(
            source_id, "review", progress=1.0, doc_date=doc_date.isoformat() if doc_date else None
        )
    except Exception as exc:  # noqa: BLE001 — the error is shown to the owner on import
        logger.exception("import úloh %s zlyhal", source_id)
        store.set_source_status(source_id, "error", error=str(exc)[:300])


class ApplyError(ValueError):
    def __init__(self, problems: list[dict[str, Any]]) -> None:
        super().__init__("návrhy nie sú pripravené")
        self.problems = problems


def apply_import(source_id: str) -> dict[str, int]:
    """Approved proposals → tasks. Every proposal must be decided, an approved one must have a
    deadline."""
    source = store.get_source(source_id)
    if source is None:
        raise KeyError(source_id)
    if source["status"] != "review":
        raise ApplyError([{"problem": f"import je v stave {source['status']}"}])
    drafts = store.list_drafts(source_id)
    problems = []
    for idx, d in enumerate(drafts):
        if d["decision"] == "pending":
            problems.append({"draft_id": d["id"], "index": idx, "problem": "nerozhodnuté"})
        elif d["decision"] == "approved" and d["kind"] in ("new", "changed"):
            for issue in draft_issues(d["data"]):
                problems.append({"draft_id": d["id"], "index": idx, "problem": issue})
            dep = d["data"].get("depends_on")
            if dep and "draft" in dep:
                dep_draft = drafts[dep["draft"]] if dep["draft"] < len(drafts) else None
                if dep_draft is None or dep_draft["decision"] != "approved":
                    problems.append(
                        {
                            "draft_id": d["id"],
                            "index": idx,
                            "problem": "závisí od neschválenej úlohy",
                        }
                    )
    if problems:
        raise ApplyError(problems)

    created_ids: dict[int, str] = {}
    counts = {"created": 0, "updated": 0, "cancelled": 0}
    for idx, d in enumerate(drafts):
        data = d["data"]
        fields = {
            "title": data["title"].strip(),
            "description": data.get("description") or "",
            "work_type": data.get("work_type"),
            "date_from": data.get("date_from"),
            "date_to": data.get("date_to"),
            "source_id": source_id,
            "source_quote": data.get("quote"),
            "source_page": data.get("page"),
        }
        if d["decision"] != "approved":
            continue
        if d["kind"] == "new":
            dep = data.get("depends_on") or {}
            created_ids[idx] = store.insert_task(
                {**fields, "site_id": source["site_id"], "depends_on": dep.get("task")},
                data.get("assignees") or [],
            )
            counts["created"] += 1
        elif d["kind"] == "changed" and d["target_task_id"]:
            store.update_task(d["target_task_id"], fields)
            store.set_task_assignees(d["target_task_id"], data.get("assignees") or [])
            counts["updated"] += 1
        elif d["kind"] == "unchanged" and d["target_task_id"]:
            store.update_task(d["target_task_id"], {"source_id": source_id})
        elif d["kind"] == "cancelled" and d["target_task_id"]:
            store.update_task(d["target_task_id"], {"status": "cancelled"})
            counts["cancelled"] += 1
    # dependencies between new tasks only once all of them exist
    for idx, d in enumerate(drafts):
        dep = d["data"].get("depends_on") or {}
        if idx in created_ids and "draft" in dep and dep["draft"] in created_ids:
            store.update_task(created_ids[idx], {"depends_on": created_ids[dep["draft"]]})
    store.set_source_status(source_id, "applied")
    return counts
