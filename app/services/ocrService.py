import io
import re
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
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
        language = lang or self.settings.ocrLang
        try:
            bestResult = self.getBestOcrResult(image, language)
        except Exception as error:
            return self.buildOcrErrorResult(f"OCR не смог обработать изображение: {error}")
        warnings: list[str] = []
        if len(bestResult["text"]) < self.settings.ocrMinTextLength:
            warnings.append("Скан загружен, но текст распознан частично")
        return {
            "text": bestResult["text"],
            "confidence": bestResult["confidence"],
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
        renderedPages: list[tuple[int, bytes]] = []
        for pageIndex in range(maxPages):
            page = pdfDocument[pageIndex]
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            renderedPages.append((pageIndex + 1, pixmap.tobytes("png")))
        pdfDocument.close()
        maxWorkers = min(4, len(renderedPages)) if len(renderedPages) > 1 else 1
        with ThreadPoolExecutor(max_workers=maxWorkers) as executor:
            processedBlocks = list(executor.map(self.runPdfPageOcr, renderedPages))
        processedBlocks.sort(key=lambda block: block.get("pageNumber") or 0)
        return processedBlocks

    def runPdfPageOcr(self, renderedPage: tuple[int, bytes]) -> dict:
        pageNumber, imageBytes = renderedPage
        image = Image.open(io.BytesIO(imageBytes))
        result = self.extractTextFromPilImage(image)
        result["pageNumber"] = pageNumber
        return result

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

    def preprocessImage(self, image: Image.Image, variant: str = "balanced") -> Image.Image:
        processedImage = image.convert("RGB")
        width, height = processedImage.size
        minWidth = 2200
        if width < minWidth:
            scale = min(4, max(2, round(minWidth / max(width, 1))))
            processedImage = processedImage.resize((width * scale, height * scale), Image.Resampling.LANCZOS)
        processedImage = ImageOps.grayscale(processedImage)
        processedImage = ImageOps.autocontrast(processedImage)
        if variant == "balanced":
            processedImage = processedImage.filter(ImageFilter.MedianFilter(size=3))
            processedImage = processedImage.filter(ImageFilter.SHARPEN)
            processedImage = processedImage.point(lambda value: 255 if value > 182 else 0)
        elif variant == "soft":
            processedImage = processedImage.filter(ImageFilter.MedianFilter(size=3))
            processedImage = processedImage.filter(ImageFilter.SHARPEN)
        elif variant == "highContrast":
            processedImage = processedImage.filter(ImageFilter.SHARPEN)
            processedImage = processedImage.point(lambda value: 255 if value > 168 else 0)
        return processedImage

    def getBestOcrResult(self, image: Image.Image, lang: str) -> dict:
        bestCandidate: dict | None = None
        for variant, extraConfig in (
            ("soft", "--oem 1 --psm 6"),
            ("balanced", "--oem 1 --psm 6"),
        ):
            processedImage = self.preprocessImage(image, variant)
            fullConfig = self.buildOcrConfig(extraConfig)
            text = self.getImageText(processedImage, lang, fullConfig)
            cleanedText = self.cleanExtractedText(text)
            confidence = self.getConfidence(processedImage, lang, fullConfig)
            score = self.calculateTextQualityScore(cleanedText, confidence)
            candidate = {
                "text": cleanedText,
                "confidence": confidence,
                "score": score,
                "variant": variant,
                "config": fullConfig,
            }
            if bestCandidate is None or candidate["score"] > bestCandidate["score"]:
                bestCandidate = candidate
            if candidate["score"] >= 78 and len(cleanedText) >= 300:
                return candidate
        return bestCandidate or {"text": "", "confidence": None}

    def getOcrConfigs(self) -> list[str]:
        return [
            self.buildOcrConfig("--oem 1 --psm 3"),
            self.buildOcrConfig("--oem 1 --psm 6"),
        ]

    def buildOcrConfig(self, config: str) -> str:
        baseConfig = self.getTessdataConfig().strip()
        return f"{baseConfig} {config}".strip() if baseConfig else config

    def getImageText(self, image: Image.Image, lang: str, config: str) -> str:
        pytesseract = self.getPytesseract()
        return pytesseract.image_to_string(
            image,
            lang=lang,
            config=config,
        ).strip()

    def cleanExtractedText(self, text: str) -> str:
        lines = [line.rstrip() for line in (text or "").replace("\r", "\n").split("\n")]
        cleanedLines: list[str] = []
        for rawLine in lines:
            line = re.sub(r"[ \t]+", " ", rawLine).strip()
            if not line:
                if cleanedLines and cleanedLines[-1] != "":
                    cleanedLines.append("")
                continue
            if self.shouldSkipOcrLine(line):
                continue
            line = re.sub(r"([А-Яа-яA-Za-z])\s{2,}([А-Яа-яA-Za-z])", r"\1 \2", line)
            line = line.replace(" ,", ",").replace(" .", ".")
            cleanedLines.append(line)
        compactLines: list[str] = []
        blankPending = False
        for line in cleanedLines:
            if not line:
                blankPending = True
                continue
            if blankPending and compactLines:
                compactLines.append("")
            compactLines.append(line)
            blankPending = False
        return "\n".join(compactLines).strip()

    def shouldSkipOcrLine(self, line: str) -> bool:
        if re.fullmatch(r"[\W_]+", line):
            return True
        if len(line) <= 2 and not re.search(r"\d", line):
            return True
        if re.search(r"(передан через диадок|страница \d+ из \d+)", line, re.IGNORECASE):
            return True
        if re.fullmatch(r"[\dA-Za-zА-Яа-я\-+/=|]{8,}", line) and not re.search(r"[А-Яа-я]{2,}", line):
            return True
        return False

    def calculateTextQualityScore(self, text: str, confidence: float | None) -> float:
        if not text.strip():
            return -1000.0
        lettersCount = len(re.findall(r"[A-Za-zА-Яа-яЁё]", text))
        totalCount = max(len(text), 1)
        wordsCount = len(re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", text))
        digitInsideWordsPenalty = len(re.findall(r"[A-Za-zА-Яа-яЁё]+\d+|\d+[A-Za-zА-Яа-яЁё]+", text))
        noisyLinesPenalty = sum(
            1 for line in text.splitlines()
            if line.strip() and len(line.strip()) <= 2
        )
        letterRatio = lettersCount / totalCount
        confidenceScore = (confidence or 0.0) * 100
        return round(confidenceScore + letterRatio * 80 + wordsCount * 0.25 - digitInsideWordsPenalty * 3 - noisyLinesPenalty * 5, 4)

    def getPytesseract(self) -> Any:
        if self.pytesseractModule is None:
            import pytesseract
            if self.settings.tesseractCmd:
                pytesseract.pytesseract.tesseract_cmd = self.settings.tesseractCmd
            self.pytesseractModule = pytesseract
        return self.pytesseractModule

    def getConfidence(self, image: Image.Image, lang: str, config: str | None = None) -> float | None:
        try:
            pytesseract = self.getPytesseract()
            data = pytesseract.image_to_data(
                image,
                lang=lang,
                config=config or self.getTessdataConfig(),
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
