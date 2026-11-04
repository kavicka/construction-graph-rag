"""Graph schema — node/edge types and Pydantic models for LLM extraction.

Document-bound nodes (doc/page/chunk/table) are deleted on re-ingest;
domain nodes (concept/quantity/ref) are shared across documents —
links between PDFs arise precisely through them.
"""

from typing import Literal

from pydantic import BaseModel, Field

# node types
DOCUMENT = "document"
PAGE = "page"
CHUNK = "chunk"
TABLE = "table"
CONCEPT = "concept"
QUANTITY = "quantity"
REFERENCE = "reference"

# edge types
CONTAINS = "CONTAINS"
HAS_TABLE = "HAS_TABLE"
NEXT = "NEXT"
MENTIONS = "MENTIONS"
DEFINES = "DEFINES"
MEASURES = "MEASURES"
CITES = "CITES"
SAME_AS = "SAME_AS"

DOC_SCOPED_TYPES = (DOCUMENT, PAGE, CHUNK, TABLE)
SHARED_TYPES = (CONCEPT, QUANTITY, REFERENCE)


def doc_node_id(doc_id: str) -> str:
    return f"doc:{doc_id}"


def page_node_id(doc_id: str, page_no: int) -> str:
    return f"page:{doc_id}:{page_no}"


def chunk_node_id(chunk_id: str) -> str:
    return f"chunk:{chunk_id}"


def table_node_id(table_id: str) -> str:
    return f"table:{table_id}"


def concept_node_id(norm_key: str) -> str:
    return f"concept:{norm_key}"


def quantity_node_id(norm_key: str) -> str:
    return f"quantity:{norm_key}"


def reference_node_id(norm_key: str) -> str:
    return f"ref:{norm_key}"


class ExtractedConcept(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    type: Literal["concept", "method", "material", "device"] = "concept"
    defined_here: bool = False


class ConceptExtraction(BaseModel):
    concepts: list[ExtractedConcept] = Field(default_factory=list, max_length=8)


# JSON schema for Ollama structured output (guaranteed valid JSON)
CONCEPT_JSON_SCHEMA = ConceptExtraction.model_json_schema()
