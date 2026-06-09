import io
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps

from app.config import Settings, getSettings


class OcrService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or getSettings()
        Image.MAX_IMAGE_PIXELS = self.settings.maxImagePixels
        self.pytesseractModule = None

    def isAvailable(self) -> bool:
        if not self.settings.ocrEnabled:
            return False
        try:
            pytesseract = self.getPytesseract()
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False

    def getHealth(self) -> dict:
        tessdataDir = self.getTessdataPath()
        if not self.settings.ocrEnabled:
            return {
                "ocrEnabled": False,
                "tesseractAvailable": False,
                "language": self.settings.ocrLang,
                "tessdataDir": str(tessdataDir) if tessdataDir else None,
                "warning": "OCR disabled in settings",
            }
        try:
            pytesseract = self.getPytesseract()
            version = str(pytesseract.get_tesseract_version())
            languages = self.getAvailableLanguages()
            required = [lang for lang in self.settings.ocrLang.split("+") if lang]
            warnings = [
                f"Не найден языковой пакет {lang}.traineddata"
                for lang in required
                if lang not in languages
            ]
            return {
                "ocrEnabled": True,
                "tesseractAvailable": True,
                "tesseractVersion": version,
                "language": self.settings.ocrLang,
                "tesseractCmd": self.settings.tesseractCmd,
                "tessdataDir": str(tessdataDir) if tessdataDir else None,
                "availableLanguages": languages,
                "warnings": warnings,
            }
        except Exception as error:
            return {
                "ocrEnabled": True,
                "tesseractAvailable": False,
                "language": self.settings.ocrLang,
                "tesseractCmd": self.settings.tesseractCmd,
                "tessdataDir": str(tessdataDir) if tessdataDir else None,
                "warnings": [f"OCR недоступен: {error}"],
            }

    def extractTextFromImage(self, imagePath: str, lang: str | None = None) -> dict:
        with Image.open(imagePath) as image:
            return self.extractTextFromPilImage(image, lang)

    def extractTextFromPilImage(self, image: Image.Image, lang: str | None = None) -> dict:
        if not self.isAvailable():
            return self.buildOcrErrorResult("OCR недоступен, текст с изображения не извлечен")
        processedImage = self.preprocessImage(image)
        language = lang or self.settings.ocrLang
        pytesseract = self.getPytesseract()
        try:
            text = pytesseract.image_to_string(
                processedImage,
                lang=language,
                config=self.getTessdataConfig(),
            ).strip()
            confidence = self.getConfidence(processedImage, language)
        except Exception as error:
            return self.buildOcrErrorResult(f"OCR не смог обработать изображение: {error}")
        warnings: list[str] = []
        if len(text) < self.settings.ocrMinTextLength:
            warnings.append("Скан загружен, но текст распознан частично")
        return {
            "text": text,
            "confidence": confidence,
            "sourceType": "ocr",
            "pageNumber": None,
            "imageIndex": None,
            "warnings": warnings,
        }

    def extractTextFromPdfPages(self, pdfPath: str) -> list[dict]:
        if not self.isAvailable():
            return [self.buildOcrErrorResult("OCR недоступен")]
        try:
            import fitz
        except Exception as error:
            return [self.buildOcrErrorResult(f"Не найден PyMuPDF: {error}")]

        pdfDocument = fitz.open(pdfPath)
        processedBlocks: list[dict] = []
        maxPages = min(len(pdfDocument), self.settings.ocrMaxPages)
        matrix = fitz.Matrix(self.settings.ocrRenderScale, self.settings.ocrRenderScale)
        for pageIndex in range(maxPages):
            page = pdfDocument[pageIndex]
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))
            result = self.extractTextFromPilImage(image)
            result["pageNumber"] = pageIndex + 1
            processedBlocks.append(result)
        pdfDocument.close()
        return processedBlocks

    def extractImagesFromDocx(self, docxPath: str) -> list[dict]:
        extractedImages: list[dict] = []
        with zipfile.ZipFile(docxPath) as archive:
            mediaNames = [
                name for name in archive.namelist()
                if name.startswith("word/media/") and Path(name).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
            ]
            for imageIndex, mediaName in enumerate(mediaNames, start=1):
                content = archive.read(mediaName)
                with tempfile.TemporaryDirectory() as tempDir:
                    tempPath = Path(tempDir) / f"docx_media_{imageIndex}{Path(mediaName).suffix}"
                    tempPath.write_bytes(content)
                    result = self.extractTextFromImage(str(tempPath))
                result["imageIndex"] = imageIndex
                result["imageName"] = mediaName
                extractedImages.append(result)
        return extractedImages

    def preprocessImage(self, image: Image.Image) -> Image.Image:
        processedImage = image.convert("RGB")
        width, height = processedImage.size
        if width < 1500:
            scale = min(3, max(2, round(1500 / max(width, 1))))
            processedImage = processedImage.resize((width * scale, height * scale), Image.Resampling.LANCZOS)
        processedImage = ImageOps.grayscale(processedImage)
        processedImage = ImageOps.autocontrast(processedImage)
        processedImage = processedImage.filter(ImageFilter.SHARPEN)
        processedImage = processedImage.point(lambda value: 255 if value > 170 else 0)
        return processedImage

    def getPytesseract(self) -> Any:
        if self.pytesseractModule is None:
            import pytesseract
            if self.settings.tesseractCmd:
                pytesseract.pytesseract.tesseract_cmd = self.settings.tesseractCmd
            self.pytesseractModule = pytesseract
        return self.pytesseractModule

    def getConfidence(self, image: Image.Image, lang: str) -> float | None:
        try:
            pytesseract = self.getPytesseract()
            data = pytesseract.image_to_data(
                image,
                lang=lang,
                config=self.getTessdataConfig(),
                output_type=pytesseract.Output.DICT,
            )
            values = [
                float(value)
                for value in data.get("conf", [])
                if str(value).strip() not in {"", "-1"}
            ]
            if not values:
                return None
            return round(sum(values) / len(values) / 100, 4)
        except Exception:
            return None

    def getAvailableLanguages(self) -> list[str]:
        try:
            pytesseract = self.getPytesseract()
            languages = sorted(pytesseract.get_languages(config=self.getTessdataConfig()))
            if languages:
                return languages
        except Exception:
            languages = []
        tessdataPath = self.getTessdataPath()
        if tessdataPath and tessdataPath.exists():
            fallbackLanguages = sorted(
                path.stem for path in tessdataPath.glob("*.traineddata")
                if path.is_file()
            )
            if fallbackLanguages:
                return fallbackLanguages
        return []

    def getTessdataConfig(self) -> str:
        tessdataPath = self.getTessdataPath()
        if not tessdataPath:
            return ""
        return f"--tessdata-dir {tessdataPath}"

    def getTessdataPath(self) -> Path | None:
        if self.settings.tessdataDir:
            return Path(self.settings.tessdataDir).resolve()
        if self.settings.tesseractCmd:
            defaultPath = Path(self.settings.tesseractCmd).resolve().parent / "tessdata"
            if defaultPath.exists():
                return defaultPath
        return None

    def buildOcrErrorResult(self, warning: str) -> dict:
        return {
            "text": "",
            "confidence": None,
            "sourceType": "ocr",
            "pageNumber": None,
            "imageIndex": None,
            "warnings": [warning],
        }
