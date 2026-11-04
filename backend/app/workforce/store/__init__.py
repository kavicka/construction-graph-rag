"""SQLite vrstva modulu Firma — vlastná schéma (tabuľky wf_*), hlúpe CRUD funkcie.

Pracovník je bežný používateľ (users, rola member) + riadok vo wf_workers s PIN-om
a profesiami; prihlásenie a session zdieľa s hlavným auth.

One module per aggregate (`schema`, `workers`, `sites`, `tasks`, `imports`, `reports`);
callers keep using `from app.workforce import store; store.get_task(...)`.

Monkeypatching: `store.foo` replaces the name only in this package namespace. Functions
inside a submodule call their siblings directly (`source_chain` → `get_source`), so to
change that behaviour patch the defining module, e.g. `app.workforce.store.imports`.
"""

from app.workforce.store.common import new_id
from app.workforce.store.imports import (
    get_draft,
    get_source,
    insert_source,
    list_drafts,
    list_sources,
    replace_drafts,
    set_source_status,
    source_chain,
    update_draft,
)
from app.workforce.store.reports import (
    QUESTION_FIELDS,
    REPORT_FIELDS,
    clear_input_audio,
    expired_audio_inputs,
    get_daily_summary,
    get_input,
    get_or_create_report,
    get_question,
    get_report,
    insert_input,
    insert_question,
    list_inputs,
    list_questions,
    list_reports,
    set_daily_summary,
    set_input_text,
    task_history,
    update_question,
    update_report,
)
from app.workforce.store.schema import DEFAULT_PROFESSIONS, SCHEMA, init_workforce_db
from app.workforce.store.sites import delete_site, get_site, insert_site, list_sites, update_site
from app.workforce.store.tasks import (
    TASK_FIELDS,
    TASK_SELECT,
    TASK_STATUSES,
    delete_task,
    get_task,
    insert_task,
    list_tasks,
    set_task_assignees,
    update_task,
)
from app.workforce.store.workers import (
    WORKER_SELECT,
    claim_pin_attempt,
    delete_worker,
    get_worker,
    insert_worker,
    list_professions,
    list_workers,
    replace_professions,
    reset_pin_failures,
    set_worker_pin,
    update_worker,
    worker_ids,
)

__all__ = [
    "DEFAULT_PROFESSIONS",
    "QUESTION_FIELDS",
    "REPORT_FIELDS",
    "SCHEMA",
    "TASK_FIELDS",
    "TASK_SELECT",
    "TASK_STATUSES",
    "WORKER_SELECT",
    "claim_pin_attempt",
    "clear_input_audio",
    "delete_site",
    "delete_task",
    "delete_worker",
    "expired_audio_inputs",
    "get_daily_summary",
    "get_draft",
    "get_input",
    "get_or_create_report",
    "get_question",
    "get_report",
    "get_site",
    "get_source",
    "get_task",
    "get_worker",
    "init_workforce_db",
    "insert_input",
    "insert_question",
    "insert_site",
    "insert_source",
    "insert_task",
    "insert_worker",
    "list_drafts",
    "list_inputs",
    "list_professions",
    "list_questions",
    "list_reports",
    "list_sites",
    "list_sources",
    "list_tasks",
    "list_workers",
    "new_id",
    "replace_drafts",
    "replace_professions",
    "reset_pin_failures",
    "set_daily_summary",
    "set_input_text",
    "set_source_status",
    "set_task_assignees",
    "set_worker_pin",
    "source_chain",
    "task_history",
    "update_draft",
    "update_question",
    "update_report",
    "update_site",
    "update_task",
    "update_worker",
    "worker_ids",
]
