from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import getSettings
from app.routers.documentsRouter import router as documentsRouter
from app.routers.healthRouter import router as healthRouter
from app.routers.ragRouter import router as ragRouter


settings = getSettings()
staticDir = Path(__file__).resolve().parent / "static"
app = FastAPI(title=settings.appName)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.corsOrigins.split(",") if origin.strip()] or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(staticDir)), name="static")
app.include_router(healthRouter, prefix=settings.apiPrefix)
app.include_router(documentsRouter, prefix=settings.apiPrefix)
app.include_router(ragRouter, prefix=settings.apiPrefix)


@app.get("/", include_in_schema=False)
def getRoot() -> FileResponse:
    return FileResponse(staticDir / "index.html")
