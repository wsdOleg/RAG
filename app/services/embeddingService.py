import hashlib
import math
import re
from collections import Counter

from app.config import Settings, getSettings


class EmbeddingService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or getSettings()
        self.model = None
        if self.settings.enableTransformerEmbeddings:
            try:
                from sentence_transformers import SentenceTransformer
                self.model = SentenceTransformer("intfloat/multilingual-e5-small")
            except Exception:
                self.model = None

    def getEmbedding(self, text: str) -> list[float]:
        if self.model is not None:
            return [float(value) for value in self.model.encode([text], normalize_embeddings=True)[0]]
        buckets = [0.0] * 384
        tokens = re.findall(r"[\wа-яА-ЯёЁ]+", text.lower())
        counts = Counter(tokens)
        for token, count in counts.items():
            bucketIndex = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % len(buckets)
            buckets[bucketIndex] += math.log1p(count)
        norm = math.sqrt(sum(value * value for value in buckets)) or 1.0
        return [value / norm for value in buckets]

    def getCosineSimilarity(self, left: list[float], right: list[float]) -> float:
        numerator = sum(leftValue * rightValue for leftValue, rightValue in zip(left, right))
        leftNorm = math.sqrt(sum(value * value for value in left)) or 1.0
        rightNorm = math.sqrt(sum(value * value for value in right)) or 1.0
        return numerator / (leftNorm * rightNorm)
