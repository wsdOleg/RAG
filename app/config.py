from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    appName: str = Field(default="Standalone RAG Service", validation_alias=AliasChoices("APP_NAME", "appName"))
    apiPrefix: str = Field(default="/api", validation_alias=AliasChoices("API_PREFIX", "apiPrefix"))
    corsOrigins: str = Field(default="http://localhost,http://127.0.0.1", validation_alias=AliasChoices("CORS_ORIGINS", "corsOrigins"))

    host: str = Field(default="0.0.0.0", validation_alias=AliasChoices("HOST", "host"))
    port: int = Field(default=8010, validation_alias=AliasChoices("PORT", "port"))

    storageRoot: Path = Field(default=Path("./storage"), validation_alias=AliasChoices("STORAGE_ROOT", "storageRoot"))
    documentsDir: Path = Field(default=Path("./storage/documents"), validation_alias=AliasChoices("DOCUMENTS_DIR", "documentsDir"))
    chromaDir: Path = Field(default=Path("./storage/chroma"), validation_alias=AliasChoices("CHROMA_DIR", "chromaDir"))
    sessionsDir: Path = Field(default=Path("./storage/sessions"), validation_alias=AliasChoices("SESSIONS_DIR", "sessionsDir"))
    cacheDir: Path = Field(default=Path("./storage/cache"), validation_alias=AliasChoices("CACHE_DIR", "cacheDir"))
    exportsDir: Path = Field(default=Path("./storage/exports"), validation_alias=AliasChoices("EXPORTS_DIR", "exportsDir"))

    llmProvider: str = Field(default="ollama", validation_alias=AliasChoices("LLM_PROVIDER", "llmProvider"))
    llmModel: str = Field(default="qwen2.5:7b", validation_alias=AliasChoices("LLM_MODEL", "llmModel"))
    llmBaseUrl: str = Field(default="http://localhost:11434", validation_alias=AliasChoices("LLM_BASE_URL", "llmBaseUrl"))
    llmTimeoutSeconds: int = Field(default=90, validation_alias=AliasChoices("LLM_TIMEOUT_SECONDS", "llmTimeoutSeconds"))
    ragConfidenceThreshold: float = Field(default=0.62, validation_alias=AliasChoices("RAG_CONFIDENCE_THRESHOLD", "ragConfidenceThreshold"))
    enableTransformerEmbeddings: bool = Field(default=False, validation_alias=AliasChoices("ENABLE_TRANSFORMER_EMBEDDINGS", "enableTransformerEmbeddings"))

    chromaCollectionChunks: str = Field(default="rag_chunks", validation_alias=AliasChoices("CHROMA_COLLECTION_CHUNKS", "chromaCollectionChunks"))
    chromaCollectionDocuments: str = Field(default="rag_documents", validation_alias=AliasChoices("CHROMA_COLLECTION_DOCUMENTS", "chromaCollectionDocuments"))

    chunkSizeChars: int = Field(default=4200, validation_alias=AliasChoices("CHUNK_SIZE_CHARS", "chunkSizeChars"))
    overlapChars: int = Field(default=650, validation_alias=AliasChoices("OVERLAP_CHARS", "overlapChars"))
    maxUploadMb: int = Field(default=5, validation_alias=AliasChoices("MAX_UPLOAD_MB", "maxUploadMb"))
    maxImportBundleMb: int = Field(default=25, validation_alias=AliasChoices("MAX_IMPORT_BUNDLE_MB", "maxImportBundleMb"))
    maxZipEntryMb: int = Field(default=8, validation_alias=AliasChoices("MAX_ZIP_ENTRY_MB", "maxZipEntryMb"))
    maxZipEntries: int = Field(default=100, validation_alias=AliasChoices("MAX_ZIP_ENTRIES", "maxZipEntries"))
    maxImagePixels: int = Field(default=40_000_000, validation_alias=AliasChoices("MAX_IMAGE_PIXELS", "maxImagePixels"))
    processDocumentsInBackground: bool = Field(default=True, validation_alias=AliasChoices("PROCESS_DOCUMENTS_IN_BACKGROUND", "processDocumentsInBackground"))

    ocrEnabled: bool = Field(default=True, validation_alias=AliasChoices("OCR_ENABLED", "ocrEnabled"))
    ocrLang: str = Field(default="rus+eng", validation_alias=AliasChoices("OCR_LANG", "ocrLang"))
    tesseractCmd: str | None = Field(default=None, validation_alias=AliasChoices("TESSERACT_CMD", "tesseractCmd"))
    tessdataDir: str | None = Field(default=None, validation_alias=AliasChoices("TESSDATA_DIR", "tessdataDir"))
    ocrForceAllPdfPages: bool = Field(default=False, validation_alias=AliasChoices("OCR_FORCE_ALL_PDF_PAGES", "ocrForceAllPdfPages"))
    ocrMinTextLength: int = Field(default=20, validation_alias=AliasChoices("OCR_MIN_TEXT_LENGTH", "ocrMinTextLength"))
    ocrRenderScale: float = Field(default=1.8, validation_alias=AliasChoices("OCR_RENDER_SCALE", "ocrRenderScale"))
    ocrMaxPages: int = Field(default=50, validation_alias=AliasChoices("OCR_MAX_PAGES", "ocrMaxPages"))

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


@lru_cache
def getSettings() -> Settings:
    settings = Settings()
    settings.storageRoot.mkdir(parents=True, exist_ok=True)
    settings.documentsDir.mkdir(parents=True, exist_ok=True)
    settings.chromaDir.mkdir(parents=True, exist_ok=True)
    settings.sessionsDir.mkdir(parents=True, exist_ok=True)
    settings.cacheDir.mkdir(parents=True, exist_ok=True)
    settings.exportsDir.mkdir(parents=True, exist_ok=True)
    return settings
