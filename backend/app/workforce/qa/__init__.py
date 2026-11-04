"""Questions about company data in the chat and on the worker's phone (docs/sk/design-log.md §17).

Code decides everything — who, when, what type of question, who may see what — and the
answer is assembled by a template from read-only data. The LLM at most assigns the question
to a fixed type.
"""
