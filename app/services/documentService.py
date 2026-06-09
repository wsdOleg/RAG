import json
import mimetypes
import re
import shutil
import tempfile
import zipfile
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from fastapi import UploadFile

from app.config import Settings, getSettings
from app.services.chunkService import ChunkService
from app.services.chromaStoreService import ChromaStoreService
from app.services.textExtractionService import TextExtractionError, TextExtractionService
from app.utils.formatting import formatAmount, formatDate, formatDocumentType, formatStatus, formatVendor
from app.utils.status import calculateBusinessStatus


class DocumentService:
    allowedExtensions = {
        ".txt",
        ".csv",
        ".rtf",
        ".pdf",
        ".docx",
        ".doc",
        ".xlsx",
        ".xls",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".tif",
        ".tiff",
        ".bmp",
    }
    allowedImportExtensions = {".zip"}

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or getSettings()
        self.textExtractionService = TextExtractionService(self.settings)
        self.chunkService = ChunkService()
        self.chromaStoreService = ChromaStoreService(self.settings)

    def getNowIso(self) -> str:
        return datetime.now(UTC).isoformat()

    async def uploadDocument(self, file: UploadFile, formData: dict) -> dict:
        documentId = uuid4().hex
        safeFileName = self.getSafeFileName(file.filename or f"{documentId}.bin")
        self.validateUploadFile(file, safeFileName)
        documentDir = self.settings.documentsDir / documentId
        documentDir.mkdir(parents=True, exist_ok=True)
        filePath = documentDir / safeFileName
        fileSize = await self.saveUploadToPath(file, filePath, self.settings.maxUploadMb)
        documentRecord = self.buildDocumentRecord(documentId, file, filePath, formData, fileSize)
        self.saveDocumentRecord(documentRecord)
        processedRecord = self.processDocument(documentRecord)
        return self.sanitizeDocumentRecord(processedRecord)

    def processDocument(self, documentRecord: dict) -> dict:
        try:
            extractedBlocks = self.textExtractionService.extractText(documentRecord["filePath"])
            chunks = self.chunkService.chunkText(
                extractedBlocks,
                chunkSize=self.settings.chunkSizeChars,
                overlap=self.settings.overlapChars,
            )
            recordWithContent = dict(documentRecord)
            recordWithContent["processingStatus"] = "indexed"
            recordWithContent["updatedAt"] = self.getNowIso()
            recordWithContent["businessStatus"] = calculateBusinessStatus(recordWithContent.get("validTo"))
            recordWithContent["extractedBlocks"] = extractedBlocks
            recordWithContent["chunks"] = self.buildChunkRecords(recordWithContent, chunks)
            recordWithContent["pageCount"] = max([block.get("pageNumber") or 0 for block in extractedBlocks] or [0])
            recordWithContent["shortSummary"] = self.buildShortSummary(recordWithContent)
            if not recordWithContent["chunks"]:
                allWarnings = [
                    warning
                    for block in extractedBlocks
                    for warning in (block.get("warnings") or [])
                ]
                recordWithContent["processingStatus"] = "failed"
                recordWithContent["processingError"] = "; ".join(allWarnings) or "Скан загружен, но текст не распознан"
            self.saveDocumentRecord(recordWithContent)
            self.chromaStoreService.saveDocumentMetadata(recordWithContent)
            if recordWithContent["chunks"]:
                self.chromaStoreService.saveDocumentChunks(recordWithContent, recordWithContent["chunks"])
            return recordWithContent
        except TextExtractionError as error:
            documentRecord["processingStatus"] = "failed"
            documentRecord["processingError"] = str(error)
            documentRecord["updatedAt"] = self.getNowIso()
            self.saveDocumentRecord(documentRecord)
            return documentRecord

    def getDocuments(self) -> list[dict]:
        records: list[dict] = []
        for recordPath in sorted(self.settings.documentsDir.glob("*/record.json")):
            record = json.loads(recordPath.read_text(encoding="utf-8"))
            records.append(self.sanitizeDocumentRecord(record))
        records.sort(key=lambda record: record.get("createdAt") or "", reverse=True)
        return records

    def getDocument(self, documentId: str) -> dict | None:
        recordPath = self.getRecordPath(documentId)
        if not recordPath.exists():
            return None
        return json.loads(recordPath.read_text(encoding="utf-8"))

    def getDocumentPreview(self, documentId: str) -> dict | None:
        documentRecord = self.getDocument(documentId)
        if not documentRecord:
            return None
        return {
            "id": documentRecord["id"],
            "title": documentRecord["title"],
            "documentType": formatDocumentType(documentRecord.get("documentType")),
            "vendor": formatVendor(documentRecord.get("vendor")),
            "validTo": formatDate(documentRecord.get("validTo")),
            "amount": formatAmount(documentRecord.get("amount"), documentRecord.get("currency"), emptyText="-"),
            "businessStatus": formatStatus(documentRecord.get("businessStatus")),
            "processingStatus": documentRecord.get("processingStatus"),
            "shortSummary": documentRecord.get("shortSummary"),
            "blocks": documentRecord.get("extractedBlocks") or [],
            "chunks": documentRecord.get("chunks") or [],
            "fileUrl": f"/api/documents/{documentId}/file",
        }

    def exportDocumentsBundle(self, documentIds: list[str] | None = None) -> Path:
        sourceDocuments = self.getDocuments()
        if documentIds:
            sourceDocuments = [document for document in sourceDocuments if document["id"] in set(documentIds)]
        exportId = f"rag_export_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        exportPath = self.settings.exportsDir / f"{exportId}.zip"
        manifestDocuments: list[dict] = []
        with zipfile.ZipFile(exportPath, "w", zipfile.ZIP_DEFLATED) as archive:
            for document in sourceDocuments:
                fullRecord = self.getDocument(document["id"])
                if not fullRecord:
                    continue
                filePath = Path(fullRecord["filePath"])
                archivePath = Path("documents") / document["id"] / filePath.name
                if filePath.exists():
                    archive.write(filePath, archivePath.as_posix())
                manifestDocuments.append({
                    "id": fullRecord["id"],
                    "title": fullRecord["title"],
                    "originalFileName": fullRecord.get("originalFileName"),
                    "fileName": fullRecord.get("fileName"),
                    "archivePath": archivePath.as_posix(),
                    "mimeType": fullRecord.get("mimeType"),
                    "fileSize": fullRecord.get("fileSize"),
                    "documentType": fullRecord.get("documentType"),
                    "vendor": fullRecord.get("vendor"),
                    "contractNumber": fullRecord.get("contractNumber"),
                    "validFrom": fullRecord.get("validFrom"),
                    "validTo": fullRecord.get("validTo"),
                    "amount": fullRecord.get("amount"),
                    "currency": fullRecord.get("currency"),
                    "softwareName": fullRecord.get("softwareName"),
                    "licenseCount": fullRecord.get("licenseCount"),
                    "comment": fullRecord.get("comment"),
                })
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "version": 1,
                        "createdAt": self.getNowIso(),
                        "documents": manifestDocuments,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        return exportPath

    async def importDocumentsBundle(self, bundleFile: UploadFile) -> dict:
        with tempfile.TemporaryDirectory() as tempDirPath:
            tempDir = Path(tempDirPath)
            bundleName = self.getSafeFileName(bundleFile.filename or "documents_bundle.zip")
            self.validateImportBundle(bundleFile, bundleName)
            bundlePath = tempDir / bundleName
            await self.saveUploadToPath(bundleFile, bundlePath, self.settings.maxImportBundleMb)
            importedDocuments: list[dict] = []
            with zipfile.ZipFile(bundlePath, "r") as archive:
                self.validateArchiveEntries(archive)
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                for documentMeta in manifest.get("documents") or []:
                    importedDocuments.append(
                        self.importDocumentFromArchive(archive, tempDir, documentMeta)
                    )
        return {
            "importedCount": len(importedDocuments),
            "documents": [self.sanitizeDocumentRecord(document) for document in importedDocuments],
        }

    def importDocumentFromArchive(self, archive: zipfile.ZipFile, tempDir: Path, documentMeta: dict) -> dict:
        sourceDocumentId = documentMeta["id"]
        archivePath = self.getSafeArchiveMemberPath(str(documentMeta["archivePath"]))
        self.validateArchiveMember(archive, archivePath)
        extractedPath = tempDir / Path(archivePath).name
        with archive.open(archivePath) as source, extractedPath.open("wb") as target:
            shutil.copyfileobj(source, target)
        newDocumentId = uuid4().hex
        documentDir = self.settings.documentsDir / newDocumentId
        documentDir.mkdir(parents=True, exist_ok=True)
        finalFilePath = documentDir / Path(documentMeta.get("fileName") or extractedPath.name).name
        self.validateStoredFile(finalFilePath.name)
        shutil.copy2(extractedPath, finalFilePath)
        documentRecord = {
            "id": newDocumentId,
            "title": documentMeta.get("title") or Path(finalFilePath).stem,
            "originalFileName": documentMeta.get("originalFileName") or finalFilePath.name,
            "fileName": finalFilePath.name,
            "filePath": str(finalFilePath.resolve()),
            "mimeType": documentMeta.get("mimeType") or mimetypes.guess_type(finalFilePath.name)[0] or "application/octet-stream",
            "fileSize": finalFilePath.stat().st_size,
            "documentType": documentMeta.get("documentType") or "document",
            "vendor": documentMeta.get("vendor"),
            "contractNumber": documentMeta.get("contractNumber"),
            "validFrom": documentMeta.get("validFrom"),
            "validTo": documentMeta.get("validTo"),
            "amount": documentMeta.get("amount"),
            "currency": documentMeta.get("currency") or "RUB",
            "softwareName": documentMeta.get("softwareName"),
            "licenseCount": documentMeta.get("licenseCount"),
            "comment": documentMeta.get("comment"),
            "processingStatus": "processing",
            "businessStatus": calculateBusinessStatus(documentMeta.get("validTo")),
            "createdAt": self.getNowIso(),
            "updatedAt": self.getNowIso(),
            "shortSummary": None,
            "processingError": None,
            "extractedBlocks": [],
            "chunks": [],
            "pageCount": 0,
            "importedFromDocumentId": sourceDocumentId,
        }
        self.saveDocumentRecord(documentRecord)
        return self.processDocument(documentRecord)

    def deleteDocument(self, documentId: str) -> bool:
        documentRecord = self.getDocument(documentId)
        if not documentRecord:
            return False
        self.chromaStoreService.deleteDocument(documentId)
        documentDir = self.settings.documentsDir / documentId
        for path in sorted(documentDir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        if documentDir.exists():
            documentDir.rmdir()
        return True

    def reindexDocument(self, documentId: str) -> dict | None:
        documentRecord = self.getDocument(documentId)
        if not documentRecord:
            return None
        self.chromaStoreService.deleteDocument(documentId)
        return self.processDocument(documentRecord)

    def getStats(self) -> dict:
        records = [self.getDocument(record["id"]) for record in self.getDocuments()]
        normalizedRecords = [record for record in records if record]
        return {
            "total": len(normalizedRecords),
            "active": sum(1 for record in normalizedRecords if record.get("businessStatus") == "active"),
            "expiring": sum(1 for record in normalizedRecords if record.get("businessStatus") == "expiring"),
            "expired": sum(1 for record in normalizedRecords if record.get("businessStatus") == "expired"),
            "noDate": sum(1 for record in normalizedRecords if record.get("businessStatus") == "no_date"),
            "processing": sum(1 for record in normalizedRecords if record.get("processingStatus") == "processing"),
            "failed": sum(1 for record in normalizedRecords if record.get("processingStatus") == "failed"),
        }

    def getFilePath(self, documentId: str) -> Path | None:
        documentRecord = self.getDocument(documentId)
        if not documentRecord:
            return None
        filePath = Path(documentRecord["filePath"]).resolve()
        try:
            filePath.relative_to(self.settings.documentsDir.resolve())
        except ValueError:
            return None
        return filePath

    def buildDocumentRecord(self, documentId: str, file: UploadFile, filePath: Path, formData: dict, fileSize: int) -> dict:
        originalFileName = file.filename or filePath.name
        title = (formData.get("title") or Path(originalFileName).stem).strip()
        amount = self.parseAmount(formData.get("amount"))
        validTo = formData.get("validTo")
        return {
            "id": documentId,
            "title": title,
            "originalFileName": originalFileName,
            "fileName": filePath.name,
            "filePath": str(filePath.resolve()),
            "mimeType": file.content_type or mimetypes.guess_type(originalFileName)[0] or "application/octet-stream",
            "fileSize": fileSize,
            "documentType": formData.get("documentType") or "document",
            "vendor": formData.get("vendor"),
            "contractNumber": formData.get("contractNumber"),
            "validFrom": formData.get("validFrom"),
            "validTo": validTo,
            "amount": amount,
            "currency": formData.get("currency") or "RUB",
            "softwareName": formData.get("softwareName"),
            "licenseCount": int(formData["licenseCount"]) if str(formData.get("licenseCount") or "").strip().isdigit() else None,
            "comment": formData.get("comment"),
            "processingStatus": "processing",
            "businessStatus": calculateBusinessStatus(validTo),
            "createdAt": self.getNowIso(),
            "updatedAt": self.getNowIso(),
            "shortSummary": None,
            "processingError": None,
            "extractedBlocks": [],
            "chunks": [],
            "pageCount": 0,
        }

    def buildChunkRecords(self, documentRecord: dict, chunks: list[dict]) -> list[dict]:
        preparedChunks: list[dict] = []
        for chunk in chunks:
            preparedChunks.append({
                "id": f"{documentRecord['id']}:chunk:{len(preparedChunks)}",
                **chunk,
            })
        return preparedChunks

    def buildShortSummary(self, documentRecord: dict) -> str:
        if documentRecord.get("softwareName") and documentRecord.get("documentType") == "license":
            return f"Лицензионный документ по {documentRecord['softwareName']}."
        if documentRecord.get("documentType") == "contract":
            return "Договор по программному обеспечению или услугам."
        firstChunk = (documentRecord.get("chunks") or [{}])[0]
        text = (firstChunk.get("text") or "").strip()
        if not text:
            return f"Документ {documentRecord['title']} загружен в RAG."
        shortText = re.sub(r"\s+", " ", text)[:220].strip()
        if len(text) > 220:
            shortText += "..."
        return shortText

    def saveDocumentRecord(self, documentRecord: dict) -> None:
        self.getRecordPath(documentRecord["id"]).write_text(
            json.dumps(documentRecord, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def sanitizeDocumentRecord(self, documentRecord: dict) -> dict:
        return {
            "id": documentRecord["id"],
            "title": documentRecord["title"],
            "originalFileName": documentRecord.get("originalFileName"),
            "fileName": documentRecord.get("fileName"),
            "mimeType": documentRecord.get("mimeType"),
            "fileSize": documentRecord.get("fileSize"),
            "documentType": documentRecord.get("documentType"),
            "vendor": documentRecord.get("vendor"),
            "contractNumber": documentRecord.get("contractNumber"),
            "validFrom": documentRecord.get("validFrom"),
            "validTo": documentRecord.get("validTo"),
            "amount": documentRecord.get("amount"),
            "currency": documentRecord.get("currency"),
            "softwareName": documentRecord.get("softwareName"),
            "licenseCount": documentRecord.get("licenseCount"),
            "comment": documentRecord.get("comment"),
            "processingStatus": documentRecord.get("processingStatus"),
            "businessStatus": documentRecord.get("businessStatus"),
            "createdAt": documentRecord.get("createdAt"),
            "updatedAt": documentRecord.get("updatedAt"),
            "pageCount": documentRecord.get("pageCount"),
            "shortSummary": documentRecord.get("shortSummary"),
            "previewUrl": f"/api/documents/{documentRecord['id']}/preview",
            "fileUrl": f"/api/documents/{documentRecord['id']}/file",
        }

    def getRecordPath(self, documentId: str) -> Path:
        return self.settings.documentsDir / documentId / "record.json"

    def getSafeFileName(self, fileName: str) -> str:
        cleanName = re.sub(r"[^\w.\-]+", "_", fileName, flags=re.UNICODE)
        return cleanName.strip("._") or f"{uuid4().hex}.bin"

    async def saveUploadToPath(self, file: UploadFile, targetPath: Path, maxMb: int) -> int:
        maxBytes = maxMb * 1024 * 1024
        totalBytes = 0
        try:
            with targetPath.open("wb") as target:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    totalBytes += len(chunk)
                    if totalBytes > maxBytes:
                        raise HTTPException(status_code=413, detail=f"Размер файла превышает {maxMb} МБ")
                    target.write(chunk)
        except Exception:
            if targetPath.exists():
                targetPath.unlink()
            raise
        finally:
            await file.close()
        return totalBytes

    def validateUploadFile(self, file: UploadFile, safeFileName: str) -> None:
        self.validateStoredFile(safeFileName)
        contentType = (file.content_type or "").lower()
        if contentType and contentType in {"application/x-msdownload", "application/x-sh", "application/javascript"}:
            raise HTTPException(status_code=415, detail="Неподдерживаемый тип файла")

    def validateImportBundle(self, file: UploadFile, safeFileName: str) -> None:
        extension = Path(safeFileName).suffix.lower()
        if extension not in self.allowedImportExtensions:
            raise HTTPException(status_code=415, detail="Для импорта поддерживаются только ZIP-архивы")

    def validateStoredFile(self, fileName: str) -> None:
        extension = Path(fileName).suffix.lower()
        if extension not in self.allowedExtensions:
            raise HTTPException(status_code=415, detail=f"Неподдерживаемый тип файла: {extension or 'без расширения'}")

    def validateArchiveEntries(self, archive: zipfile.ZipFile) -> None:
        members = archive.infolist()
        if len(members) > self.settings.maxZipEntries:
            raise HTTPException(status_code=400, detail="Архив содержит слишком много файлов")
        totalUncompressed = 0
        for member in members:
            totalUncompressed += member.file_size
            if member.file_size > self.settings.maxZipEntryMb * 1024 * 1024:
                raise HTTPException(status_code=400, detail=f"Файл внутри архива превышает {self.settings.maxZipEntryMb} МБ")
            if totalUncompressed > self.settings.maxImportBundleMb * 1024 * 1024:
                raise HTTPException(status_code=400, detail="Архив слишком большой после распаковки")
            self.getSafeArchiveMemberPath(member.filename)

    def validateArchiveMember(self, archive: zipfile.ZipFile, memberPath: str) -> None:
        try:
            info = archive.getinfo(memberPath)
        except KeyError as error:
            raise HTTPException(status_code=400, detail="В архиве отсутствует ожидаемый файл документа") from error
        if info.file_size > self.settings.maxZipEntryMb * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"Файл внутри архива превышает {self.settings.maxZipEntryMb} МБ")

    def getSafeArchiveMemberPath(self, memberPath: str) -> str:
        normalized = Path(memberPath)
        if normalized.is_absolute() or ".." in normalized.parts:
            raise HTTPException(status_code=400, detail="Архив содержит небезопасные пути")
        return normalized.as_posix()

    def parseAmount(self, value: object) -> float | None:
        if value in {None, "", "-", "None", "null", "undefined"}:
            return None
        cleanValue = str(value).replace(" ", "").replace("₽", "").replace(",", ".")
        try:
            amount = Decimal(cleanValue)
        except (InvalidOperation, ValueError):
            return None
        if amount <= 0:
            return None
        return float(amount)
