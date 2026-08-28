"""FastAPI entrypoint. Loads `.env` before anything else imports, so every module
that reads an environment variable — `backend/config.py`, `backend/agents/llm.py` —
sees it regardless of import order.
"""

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request  # noqa: E402 — must follow load_dotenv()
from fastapi.exceptions import HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from .api.cases import router as cases_router  # noqa: E402
from .api.chat import router as chat_router  # noqa: E402
from .api.demo import reset_live_demo_data  # noqa: E402
from .api.demo import router as demo_router  # noqa: E402
from .api.mandates import router as mandates_router  # noqa: E402
from .api.stream import router as stream_router  # noqa: E402
from .api.summary import router as summary_router  # noqa: E402
from .webhooks.razorpay import router as razorpay_router  # noqa: E402

app = FastAPI(title="Paygent")

app.add_middleware(
    CORSMiddleware,
    # :5173 is the dashboard, :5174 is the storefront — a separate site on a
    # separate port on purpose (see vite.shop.config.js), so both need CORS.
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """FastAPI's default handler wraps `detail` under `{"detail": ...}`. Every route
    here already raises with `detail={"error": {"code": ..., "message": ...}}`
    matching CONTRACTS.md §5 exactly — this makes that the literal response body
    instead of nesting it one level deeper.
    """
    return JSONResponse(status_code=exc.status_code, content=exc.detail)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """A guardrail block is an expected outcome and is never a 500 (routes raise
    HTTPException for that, handled above). Anything reaching here is a genuine bug —
    still returned in the contract's error shape rather than a raw traceback, since a
    malformed webhook payload or an unexpected DB state must not crash the demo.
    """
    return JSONResponse(status_code=500, content={
        "error": {"code": "INTERNAL_ERROR", "message": str(exc)}})


@app.on_event("startup")
def _reset_live_demo_data_on_boot() -> None:
    """Every process start (including uvicorn --reload's restarts) is a clean
    slate for anything the live demo created — a Ctrl+C and re-run must never
    resurface an old abandoned cart or scenario run. `scripts/seed.py`'s dataset
    is untouched; only the `ses_live_*` prefix is ever wiped."""
    reset_live_demo_data()


app.include_router(razorpay_router, prefix="/api")
app.include_router(summary_router, prefix="/api")
app.include_router(cases_router, prefix="/api")
app.include_router(mandates_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(demo_router, prefix="/api")
app.include_router(stream_router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
