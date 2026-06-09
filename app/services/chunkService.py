import re


class ChunkService:
    def cleanText(self, text: str) -> str:
        cleanText = text.replace("\x00", " ")
        cleanText = re.sub(r"[ \t]+", " ", cleanText)
        cleanText = re.sub(r"\n{3,}", "\n\n", cleanText)
        return cleanText.strip()

    def chunkText(self, blocks: list[dict], chunkSize: int = 4200, overlap: int = 650) -> list[dict]:
        chunks: list[dict] = []
        for block in blocks:
            text = self.cleanText(block.get("text") or "")
            if not text:
                continue
            startIndex = 0
            chunkIndex = 0
            while startIndex < len(text):
                endIndex = min(startIndex + chunkSize, len(text))
                if endIndex < len(text):
                    sentenceEnd = max(
                        text.rfind(".", startIndex, endIndex),
                        text.rfind("!", startIndex, endIndex),
                        text.rfind("?", startIndex, endIndex),
                    )
                    if sentenceEnd > startIndex + int(chunkSize * 0.55):
                        endIndex = sentenceEnd + 1
                chunkText = text[startIndex:endIndex].strip()
                if chunkText:
                    chunks.append({
                        "chunkIndex": chunkIndex,
                        "pageNumber": block.get("pageNumber"),
                        "sourceType": block.get("sourceType") or "text",
                        "extractionMethod": block.get("extractionMethod") or "unknown",
                        "imageIndex": block.get("imageIndex"),
                        "imageName": block.get("imageName"),
                        "ocrConfidence": block.get("ocrConfidence"),
                        "ocrLanguage": block.get("ocrLanguage"),
                        "warnings": block.get("warnings") or [],
                        "text": chunkText,
                    })
                if endIndex >= len(text):
                    break
                startIndex = max(0, endIndex - overlap)
                chunkIndex += 1
        return chunks

