import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import sessions
from app.core.config import get_settings
from app.core.errors import AppError

settings = get_settings()
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Chat Sessions API",
    description=(
        "Chat sessions backed by OpenAI, with conversation history, token usage "
        "and accumulated cost stored per session."
    ),
    version="1.0.0",
)

app.include_router(sessions.router)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- error handling: every failure leaves through the same shape ---


@app.exception_handler(AppError)
async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request payload is invalid.",
                "details": exc.errors(),
            }
        },
    )


@app.exception_handler(SQLAlchemyError)
async def handle_db_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
    # Log the detail, return none of it: driver messages can carry the DSN.
    logger.exception("database failure: %s", type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "database_error", "message": "Database failure."}},
    )


@app.exception_handler(Exception)
async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error: %s", type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "Internal server error."}},
    )


# --- misc ---


@app.get("/health", tags=["service"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
