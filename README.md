# RAG Module

Я сделал отдельный RAG-модуль на `FastAPI`, чтобы его можно было подключать к другой системе как готовый backend.

## Что это умеет

- принимает документы через API
- извлекает текст из `PDF`, `DOCX`, `TXT`, `XLSX`, `JPG`, `PNG` и других форматов
- распознает сканы через `OCR` (`Tesseract`)
- режет текст на чанки
- хранит вектора в `Chroma`
- отвечает на вопросы через `POST /api/rag/ask`
- показывает цитаты и источники
- умеет экспортировать и импортировать документы
- проверяет здоровье сервиса через health-endpoint'ы

## Как это работает

Схема простая:

1. Пользователь загружает файл.
2. Сервис сохраняет сам документ.
3. Если это скан, запускается OCR.
4. Текст режется на куски.
5. Для кусков считаются embeddings.
6. Embeddings кладутся в `Chroma`.
7. Пользователь задает вопрос.
8. Сервис ищет похожие фрагменты.
9. При необходимости вызывает LLM через Ollama.
10. Возвращает ответ и источники.

## Почему именно Chroma

выбрал `Chroma`, потому что для этого проекта он самый понятный и удобный:

- быстро поднимается как локальное векторное БД
- не требует отдельного сложного сервера на старте
- хорошо подходит для MVP и отдельного микросервиса
- нормально работает с embeddings и semantic search
- удобно хранить и искать фрагменты документов по смыслу

Важно:

- `Chroma` нужна именно для поиска по смыслу.
- файл `chroma.sqlite3` внутри `Chroma` - это ее внутреннее служебное хранилище

## OCR

OCR добавил для сканов и картинок внутри документов.

- PDF со сканами
- изображения `PNG`, `JPG`, `WEBP`, `TIFF`
- картинки внутри `DOCX`

Перед OCR идет preprocessing:

- grayscale
- autocontrast
- resize
- threshold

Для OCR нужны языки:

- `rus`
- `eng`

## Ответы ассистента

Endpoint `POST /api/rag/ask` сам решает, как отвечать:

- из metadata документа
- из OCR/chunks
- из `Chroma`
- из LLM через Ollama

Если вопрос простой и его можно закрыть по metadata, LLM не вызывается.

## Основные endpoints

### Health

- `GET /api/health`
- `GET /api/health/ocr`
- `GET /api/health/llm`
- `GET /api/health/chroma`

### Документы

- `GET /api/documents`
- `GET /api/documents/stats`
- `GET /api/documents/{documentId}`
- `GET /api/documents/{documentId}/preview`
- `GET /api/documents/{documentId}/file`
- `POST /api/documents/upload`
- `POST /api/documents/{documentId}/reindex`
- `DELETE /api/documents/{documentId}`
- `GET /api/documents/export`
- `POST /api/documents/import`

### RAG

- `POST /api/rag/ask`
- `GET /api/rag/chat/sessions`
- `GET /api/rag/chat/sessions/{sessionId}/messages`

## Пример загрузки

```bash
curl -X POST "http://127.0.0.1:8010/api/documents/upload" \
  -F "file=@sample.pdf" \
  -F "title=Sample contract" \
  -F "documentType=contract" \
  -F "vendor=Adobe" \
  -F "validTo=2027-12-31" \
  -F "amount=735000" \
  -F "currency=RUB"
```

## Пример вопроса

```json
{
  "question": "что написано в документе договор 1182"
}
```

## Пример health-check

```bash
curl http://127.0.0.1:8010/api/health
curl http://127.0.0.1:8010/api/health/ocr
curl http://127.0.0.1:8010/api/health/llm
curl http://127.0.0.1:8010/api/health/chroma
```

## Настройки

Основные переменные окружения:

- `HOST`
- `PORT`
- `LLM_PROVIDER`
- `LLM_MODEL`
- `LLM_BASE_URL`
- `OCR_ENABLED`
- `OCR_LANG`
- `TESSERACT_CMD`
- `TESSDATA_DIR`
- `CHROMA_DIR`
- `DOCUMENTS_DIR`
- `SESSIONS_DIR`

## Структура проекта

- `app/main.py` - точка входа
- `app/routers/` - API endpoints
- `app/services/` - основная логика
- `app/utils/` - форматирование и статусы
- `storage/` - документы, Chroma, OCR-файлы, сессии

## Проверка руками

1. Открыть Swagger: `http://127.0.0.1:8010/docs`
2. Проверить `GET /api/health`
3. Загрузить документ через `POST /api/documents/upload`
4. Открыть `GET /api/documents`
5. Проверить `GET /api/documents/{id}/preview`
6. Задать вопрос через `POST /api/rag/ask`

