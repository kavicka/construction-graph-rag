"""Firma (company) module: workers (PIN), sites and tasks, task import from documents, voice
attendance with a daily report for the owner.

Everything that decides (active tasks, missing data, deadlines, assignment, hour totals) is computed
by code. The LLM only turns free text into structure, and every number it returns is checked against
what the person actually said or what the document states."""
