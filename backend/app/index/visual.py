"""Visual retrieval (experimental, RAG_VISUAL_RETRIEVAL) — the ColPali family of models.

A PDF page is rendered as an image and the model (ColQwen / ColSmol) turns it into
a multi-vector (one vector per "patch"). The question is encoded the same way and the page score is
MaxSim: for each question token the best patch, summed. This finds pages where the
answer is in a drawing, chart or scanned table that OCR read badly.

Storage: float16 .npy per page in data/visual/ — for hundreds of pages MaxSim
in numpy is faster and simpler than a multi-vector index.
Installation: `uv sync --extra visual`; models by RAM — ColSmol-256M (~0,5 GB) or
ColQwen2.5 (~7 GB).
"""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from app.config import get_settings

logger = logging.getLogger(__name__)

RENDER_SCALE = (
    1.0  # ~600×850 px for A4 — more only slows things down, patches get downscaled anyway
)
BATCH = 4


class VisualEmbedder(Protocol):
    def embed_images(self, images: list[Any]) -> list[np.ndarray]: ...
    def embed_query(self, text: str) -> np.ndarray: ...


class ColPaliEmbedder:
    def __init__(self, model_name: str) -> None:
        import torch
        from colpali_engine import models as cm

        family = model_name.casefold()
        if "colqwen2.5" in family or "colqwen2_5" in family:
            model_cls, proc_cls = cm.ColQwen2_5, cm.ColQwen2_5_Processor
        elif "colqwen2" in family:
            model_cls, proc_cls = cm.ColQwen2, cm.ColQwen2Processor
        elif "colsmol" in family or "idefics" in family:
            model_cls, proc_cls = cm.ColIdefics3, cm.ColIdefics3Processor
        else:
            model_cls, proc_cls = cm.ColPali, cm.ColPaliProcessor
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        dtype = torch.float16 if self.device == "mps" else torch.float32
        self._torch = torch
        self.model = model_cls.from_pretrained(model_name, torch_dtype=dtype).to(self.device)
        self.model.eval()
        self.processor = proc_cls.from_pretrained(model_name)

    def _run(self, batch: Any) -> list[np.ndarray]:
        batch = {k: v.to(self.device) for k, v in batch.items()}
        with self._torch.no_grad():
            out = self.model(**batch)
        mask = batch.get("attention_mask")
        vectors = []
        for i in range(out.shape[0]):
            rows = out[i]
            if mask is not None:
                rows = rows[mask[i].bool()]
            vectors.append(rows.float().cpu().numpy())
        return vectors

    def embed_images(self, images: list[Any]) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        for i in range(0, len(images), BATCH):
            out += self._run(self.processor.process_images(images[i : i + BATCH]))
        return out

    def embed_query(self, text: str) -> np.ndarray:
        return self._run(self.processor.process_queries([text]))[0]


@lru_cache
def get_visual_embedder() -> VisualEmbedder:
    model = get_settings().visual_model
    logger.info("vizuálny retrieval: %s", model)
    return ColPaliEmbedder(model)


def visual_dir() -> Path:
    d = get_settings().data_dir / "visual"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(norms == 0, 1, norms)


def render_pdf_pages(pdf_path: Path) -> list[Any]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return [pdf[i].render(scale=RENDER_SCALE).to_pil().convert("RGB") for i in range(len(pdf))]
    finally:
        pdf.close()


def index_pages(doc_id: str, pdf_path: Path, embedder: VisualEmbedder | None = None) -> int:
    """Encode all pages of the document; replaces the document's old embeddings."""
    delete_pages(doc_id)
    images = render_pdf_pages(pdf_path)
    if not images:
        return 0
    vectors = (embedder or get_visual_embedder()).embed_images(images)
    for page_no, vec in enumerate(vectors, start=1):
        np.save(visual_dir() / f"{doc_id}__{page_no}.npy", _normalize(vec).astype(np.float16))
    return len(vectors)


def delete_pages(doc_id: str) -> None:
    for f in visual_dir().glob(f"{doc_id}__*.npy"):
        f.unlink()


def maxsim(query: np.ndarray, page: np.ndarray) -> float:
    """Σ over question tokens of the max cosine with the page patches (late interaction)."""
    return float((query @ page.T).max(axis=1).sum())


def search_pages(
    query: str,
    *,
    k: int = 5,
    doc_ids: set[str] | None = None,
    embedder: VisualEmbedder | None = None,
) -> list[tuple[str, int, float]]:
    """→ [(doc_id, page, score)] sorted by MaxSim."""
    files = sorted(visual_dir().glob("*.npy"))
    if not files:
        return []
    q = _normalize((embedder or get_visual_embedder()).embed_query(query).astype(np.float32))
    scored: list[tuple[str, int, float]] = []
    for f in files:
        doc_id, page = f.stem.rsplit("__", 1)
        if doc_ids is not None and doc_id not in doc_ids:
            continue
        scored.append((doc_id, int(page), maxsim(q, np.load(f).astype(np.float32))))
    scored.sort(key=lambda t: t[2], reverse=True)
    return scored[:k]
