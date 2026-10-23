"""SPEC-011：请求隔离的 SSE 事件；断开时取消任务，不保存未完成输出。"""
import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from support_demo.store import ConflictError

Sink = Callable[[str, dict], Awaitable[None]]
_sink: ContextVar[Sink | None] = ContextVar('support_stream_sink', default=None)

def active() -> bool:
    return _sink.get() is not None

async def publish(event: str, data: dict) -> None:
    sink = _sink.get()
    if sink is not None:
        await sink(event, data)

async def status(text: str) -> None:
    await publish('status', {'text': text})

def encode(event: str, data: dict) -> str:
    return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'

async def events(operation: Callable[[], Awaitable[None]]) -> AsyncIterator[str]:
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)

    async def send(event: str, data: dict) -> None:
        await queue.put((event, data))

    async def worker() -> None:
        token = _sink.set(send)
        try:
            await operation()
            await send('done', {})
        except HTTPException as error:
            await send('error', {'text': str(error.detail), 'code': error.status_code})
        except ConflictError:
            await send('error', {'text': '会话已更新，请恢复最新记录后继续。', 'code': 409})
        except Exception:
            await send('error', {'text': '本次回复未完成，请恢复会话记录后重试。', 'code': 502})
        finally:
            _sink.reset(token)

    task = asyncio.create_task(worker())
    try:
        yield encode('status', {'text': '收到，我正在查看你描述的情况。'})
        while True:
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield ': keep-alive\n\n'
                continue
            yield encode(event, data)
            if event in {'done', 'error'}:
                break
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

def response(operation: Callable[[], Awaitable[None]]) -> StreamingResponse:
    return StreamingResponse(events(operation), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
