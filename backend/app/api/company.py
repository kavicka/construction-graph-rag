"""Nastavenia firmy: údaje, hlavička/pätička a náhľad PDF (vzhľad dáva téma aplikácie)."""

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from app.api.access import require_admin
from app.company import CompanySettings, load_company, save_company
from app.docgen.pdf import PdfEngineError
from app.docgen.render import render_document
from app.docgen.schema import DocSection, DocumentContent
from app.retrieve.pipeline import Source

router = APIRouter(prefix="/company")


@router.get("")
async def get_company() -> CompanySettings:
    return await run_in_threadpool(load_company)


@router.put("")
async def put_company(data: CompanySettings) -> CompanySettings:
    require_admin()
    return await run_in_threadpool(save_company, data)


def preview_content(company: CompanySettings) -> tuple[DocumentContent, list[Source]]:
    name = company.name or "Vaša firma"
    sources = [
        Source(
            n=1, chunk_id="preview:1", doc_id="preview", filename="zmluva-o-dielo.pdf",
            page_from=3, page_to=3, type="text", table_id=None, text="",
            snippet="Zhotoviteľ sa zaväzuje dokončiť dielo do 30. 6. 2027.",
        ),
        Source(
            n=2, chunk_id="preview:2", doc_id="preview", filename="rozpocet-2027.pdf",
            page_from=1, page_to=2, type="table_summary", table_id=None, text="",
            snippet="Celkový rozpočet projektu je 248 500 EUR bez DPH.",
        ),
    ]
    content = DocumentContent(
        doc_type="report",
        lang="sk",
        title="Ukážkový report z dokumentov",
        subtitle=f"Takto bude vyzerať dokument, ktorý {name} vygeneruje z chatu.",
        sections=[
            DocSection(
                "Zhrnutie",
                "Dielo musí byť dokončené do **30. 6. 2027** [1]. Rozpočet projektu "
                "je 248 500 EUR bez DPH [2].",
            ),
            DocSection(
                "Súvislosti medzi dokumentmi",
                "- Termín zo zmluvy [1] a rozpočet [2] sa týkajú tej istej stavby.\n"
                "- Fakturácia prebieha po etapách podľa harmonogramu [1].\n\n"
                "| Etapa | Termín | Suma |\n|---|---|---|\n"
                "| Hrubá stavba | 31. 12. 2026 | 120 000 € |\n"
                "| Dokončenie | 30. 6. 2027 | 128 500 € |",
            ),
        ],
    )
    return content, sources


@router.post("/preview")
async def preview_pdf(data: CompanySettings) -> Response:
    """Náhľad s neuloženými hodnotami z formulára — logo sa berie aktuálne nahraté."""
    content, sources = preview_content(data)
    try:
        rendered = await run_in_threadpool(render_document, content, data, sources)
    except PdfEngineError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        rendered.data,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="nahlad.pdf"'},
    )
