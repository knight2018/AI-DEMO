"""SPEC-001：批次持久化、自动分析与失败重试。"""
import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, TypedDict
from uuid import uuid4
from backend.domain import Insight, Review, Report, Validated, validate_reviews, check_limits, build_report

class Snapshot(TypedDict):
    input_url: str
    url: str
    site: str
    asin: str
    title: str
    variant: str | None
    captured_at: str
    complete: bool
    error: str | None
    reviews: list[Review]

class Batch(TypedDict):
    id: str
    status: str
    snapshot: Snapshot
    validation: Validated
    report: Report | None
    error: str | None
    created_at: str
    finished_at: str | None
    analysis_seconds: float | None

class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def save(self, batch: Batch) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO batches VALUES (?,?)", (batch["id"], json.dumps(batch, ensure_ascii=False)))

    def get(self, identity: str) -> Batch:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM batches WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError(identity)
        return json.loads(row[0])

    def list(self) -> list[Batch]:
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM batches ORDER BY rowid DESC LIMIT 100").fetchall()
        return [json.loads(row[0]) for row in rows]

class Service:
    def __init__(self, store: Store, analyzer: Callable[[list[Review]], Awaitable[Insight]]) -> None:
        self.store = store
        self.analyzer = analyzer
        self.lock = asyncio.Lock()
        # 异常退出的任务保留数据，恢复时明确要求重试，不能一直显示分析中。
        for batch in store.list():
            if batch["status"] == "analyzing":
                batch.update(status="analysis_failed", error="interrupted")
                store.save(batch)

    def create(self, snapshot: Snapshot) -> Batch:
        validation = validate_reviews(snapshot["reviews"])
        batch: Batch = {"id": uuid4().hex, "status": "validating", "snapshot": snapshot,
                        "validation": validation, "report": None, "error": None,
                        "created_at": datetime.now(timezone.utc).isoformat(), "finished_at": None, "analysis_seconds": None}
        if not snapshot["complete"]:
            batch.update(status="collection_failed", error=snapshot["error"] or "collection_incomplete")
        elif not validation["valid"]:
            batch.update(status="no_valid_reviews", error="no_valid_reviews")
        else:
            try:
                check_limits(validation["valid"])
            except ValueError as error:
                batch.update(status="analysis_failed", error=str(error))
        self.store.save(batch)
        return batch

    async def run(self, identity: str) -> Batch:
        if self.lock.locked():
            raise ValueError("task_running")
        async with self.lock:
            batch = self.store.get(identity)
            if batch["status"] not in ("validating", "analysis_failed"):
                raise ValueError("invalid_state")
            try:
                check_limits(batch["validation"]["valid"])
                batch.update(status="analyzing", error=None, report=None)
                self.store.save(batch)
                started = asyncio.get_running_loop().time()
                insight = await self.analyzer(batch["validation"]["valid"])
                batch["report"] = build_report(insight, batch["validation"]["valid"])
                batch["analysis_seconds"] = round(asyncio.get_running_loop().time() - started, 2)
                batch.update(status="completed", finished_at=datetime.now(timezone.utc).isoformat())
            except Exception as error:
                # 不回传第三方异常原文，避免请求头、密钥或响应敏感内容进入界面和日志。
                safe_codes = {"evidence_mismatch", "action_topic_mismatch", "duplicate_topic", "sample_limit_exceeded", "no_valid_reviews"}
                code = str(error) if isinstance(error, ValueError) and str(error) in safe_codes else type(error).__name__
                batch.update(status="analysis_failed", error=code, report=None)
            self.store.save(batch)
            return batch
