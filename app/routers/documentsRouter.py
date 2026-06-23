from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.services.documentService import DocumentService


router = APIRouter(prefix="/documents", tags=["documents"])
documentService = DocumentService()


@router.get("")
def getDocuments() -> list[dict]:
    return documentService.getDocuments()


@router.get("/stats")
def getDocumentStats() -> dict:
    return documentService.getStats()


@router.get("/export")
def exportDocuments(documentIds: str | None = None) -> FileResponse:
    ids = [item.strip() for item in (documentIds or "").split(",") if item.strip()] or None
    exportPath = documentService.exportDocumentsBundle(ids)
    return FileResponse(path=exportPath, filename=exportPath.name, media_type="application/zip")


@router.post("/import")
async def importDocuments(bundle: UploadFile = File(...)) -> dict:
    return await documentService.importDocumentsBundle(bundle)


@router.get("/{documentId}")
def getDocument(documentId: str) -> dict:
    document = documentService.getDocument(documentId)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return documentService.sanitizeDocumentRecord(document)


@router.patch("/{documentId}")
async def updateDocument(
    documentId: str,
    title: str | None = Form(default=None),
    documentType: str | None = Form(default=None),
    vendor: str | None = Form(default=None),
    contractNumber: str | None = Form(default=None),
    validFrom: str | None = Form(default=None),
    validTo: str | None = Form(default=None),
    amount: str | None = Form(default=None),
    currency: str | None = Form(default=None),
    softwareName: str | None = Form(default=None),
    licenseCount: str | None = Form(default=None),
    comment: str | None = Form(default=None),
) -> dict:
    document = documentService.updateDocument(
        documentId,
        {
            "title": title,
            "documentType": documentType,
            "vendor": vendor,
            "contractNumber": contractNumber,
            "validFrom": validFrom,
            "validTo": validTo,
            "amount": amount,
            "currency": currency,
            "softwareName": softwareName,
            "licenseCount": licenseCount,
            "comment": comment,
        },
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return documentService.sanitizeDocumentRecord(document)


@router.get("/{documentId}/preview")
def getDocumentPreview(documentId: str) -> dict:
    preview = documentService.getDocumentPreview(documentId)
    if not preview:
        raise HTTPException(status_code=404, detail="Document not found")
    return preview


@router.get("/{documentId}/file")
def getDocumentFile(documentId: str) -> FileResponse:
    filePath = documentService.getFilePath(documentId)
    if not filePath or not Path(filePath).exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=filePath)


@router.post("/upload")
async def uploadDocument(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    documentType: str | None = Form(default=None),
    vendor: str | None = Form(default=None),
    contractNumber: str | None = Form(default=None),
    validFrom: str | None = Form(default=None),
    validTo: str | None = Form(default=None),
    amount: str | None = Form(default=None),
    currency: str | None = Form(default=None),
    softwareName: str | None = Form(default=None),
    licenseCount: str | None = Form(default=None),
    comment: str | None = Form(default=None),
) -> dict:
    return await documentService.uploadDocument(
        file,
        {
            "title": title,
            "documentType": documentType,
            "vendor": vendor,
            "contractNumber": contractNumber,
            "validFrom": validFrom,
            "validTo": validTo,
            "amount": amount,
            "currency": currency,
            "softwareName": softwareName,
            "licenseCount": licenseCount,
            "comment": comment,
        },
    )


@router.post("/{documentId}/reindex")
def reindexDocument(documentId: str) -> dict:
    document = documentService.reindexDocument(documentId)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return documentService.sanitizeDocumentRecord(document)


@router.delete("/{documentId}")
def deleteDocument(documentId: str) -> dict:
    deleted = documentService.deleteDocument(documentId)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"ok": True}
