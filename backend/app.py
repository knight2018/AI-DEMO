"""SPEC-001：仅供受信任桌面主进程调用的本地接口。"""
import os
import secrets
from pathlib import Path
from urllib.parse import urlparse
from typing import Awaitable, Callable, cast
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response as StarletteResponse
from backend.analysis import analyze
from backend.domain import Review
from backend.service import Store, Service, Snapshot, Batch
from backend.export import export_csv, export_html

class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str | None = None
    body: str = ""
    rating: int | float | str | None = None
    expanded: bool = False
    title: str | None = None
    url: str | None = None
    date_text: str | None = None
    country: str | None = None
    variant: str | None = None
    verified: bool | None = None
    captured_at: str = ""

class SnapshotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_url: str
    url: str
    site: str
    asin: str = Field(pattern=r"^[A-Z0-9]{10}$")
    title: str
    variant: str | None = None
    captured_at: str
    complete: bool
    error: str | None = None
    reviews: list[ReviewInput] = Field(max_length=1000)

def create_app(store: Store | None = None, token: str | None = None) -> FastAPI:
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    secret = token or os.environ["DEMO_API_TOKEN"]
    service = Service(store or Store(Path(__file__).parent.parent / "data/reviews.sqlite3"), analyze)
    application.state.service = service

    @application.middleware("http")
    async def authenticate(request: Request, call_next: Callable[[Request], Awaitable[StarletteResponse]]) -> StarletteResponse:
        if request.headers.get("origin") or not secrets.compare_digest(request.headers.get("x-demo-token", ""), secret):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        if int(request.headers.get("content-length", "0")) > 2_000_000:
            return JSONResponse({"detail": "input_too_large"}, status_code=413)
        return await call_next(request)

    @application.get("/health")
    async def health() -> dict[str, object]:
        return {"ok": True, "model_configured": bool(os.environ.get("AGI_KEY"))}

    @application.get("/batches")
    async def batches() -> list[Batch]:
        return service.store.list()

    @application.post("/batches")
    async def create(payload: SnapshotInput) -> Batch:
        if service.lock.locked():
            raise HTTPException(409, "task_running")
        if payload.site not in ("amazon.com", "amazon.de") or urlparse(payload.url).hostname not in (payload.site, "www." + payload.site):
            raise HTTPException(422, "source_invalid")
        # 外部采集结构已由Pydantic验证，业务缺失与评分规则交给批次校验记录。
        return service.create(cast(Snapshot, payload.model_dump()))

    @application.post("/batches/{identity}/analyze")
    async def run(identity: str) -> Batch:
        try:
            return await service.run(identity)
        except KeyError:
            raise HTTPException(404, "batch_missing")
        except ValueError:
            raise HTTPException(409, "invalid_state_or_busy")

    @application.get("/batches/{identity}/export/{kind}")
    async def export(identity: str, kind: str) -> Response:
        try:
            batch = service.store.get(identity)
            if kind == "csv":
                return Response(export_csv(batch), media_type="text/csv; charset=utf-8")
            if kind == "html":
                return Response(export_html(batch), media_type="text/html; charset=utf-8")
            raise HTTPException(400, "format_invalid")
        except KeyError:
            raise HTTPException(404, "batch_missing")
        except ValueError:
            raise HTTPException(409, "report_unavailable")
    return application
