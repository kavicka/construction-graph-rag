"""Bounded reading of multipart uploads — never pull an oversized file into memory."""

from fastapi import HTTPException, UploadFile

UPLOAD_CHUNK_BYTES = 1024 * 1024


async def read_capped(file: UploadFile, max_bytes: int, too_large: str) -> bytes:
    """Read the upload chunk by chunk; 413 with `too_large` as soon as it exceeds max_bytes."""
    data = bytearray()
    while chunk := await file.read(UPLOAD_CHUNK_BYTES):
        data += chunk
        if len(data) > max_bytes:
            raise HTTPException(status_code=413, detail=too_large)
    return bytes(data)
