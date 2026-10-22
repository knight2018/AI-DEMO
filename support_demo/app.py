"""SPEC-005：仅本机使用的售后助手 API 与网页。"""
import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from support_demo import ai
from support_demo.domain import (ROOT, MODEL, QUESTIONS, Advice, Chunk, Identification, Issue, Question, Session,
                                 canonical_model, chunks_for, next_question, validate_advice, validate_image)
from support_demo.store import ConflictError, Store
from support_demo.pipeline import SupportPipeline
from support_demo.memory_pipeline import MemoryPipeline, NoRemainingActions
from support_demo.memory import append_turn, extract_patch, get_memory, process_message, remember_advice
from support_demo.retrieval_types import IntentResult, KnowledgeDocument, KnowledgeManifest

class StartRequest(BaseModel):
    model: str
    confirmed: bool
    issue: Issue
    description: str = Field(min_length=1, max_length=2000)

    @field_validator('description')
    @classmethod
    def clean(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('empty_description')
        return value.strip()

class VersionRequest(BaseModel):
    version: int = Field(ge=0)

class AnswerRequest(VersionRequest):
    key: str
    answer: str = Field(min_length=1, max_length=1000)

class OutcomeRequest(VersionRequest):
    outcome: Literal['resolved', 'handoff']

class MessageRequest(VersionRequest):
    text: str = Field(min_length=1,max_length=2000)
    action_id: str | None = None

class ImageRequest(BaseModel):
    image: str = Field(max_length=7_000_000)

class IntakeRequest(BaseModel):
    model: str
    confirmed: bool
    description: str = Field(min_length=1, max_length=2000)

class IntakeView(BaseModel):
    intent: IntentResult
    reply: str
    view: 'View | None' = None

class View(BaseModel):
    session: Session
    question: Question | None

class KnowledgeView(BaseModel):
    manifest: KnowledgeManifest
    documents: list[KnowledgeDocument]

def view(session: Session) -> View:
    question = next_question(session) if session.status == 'collecting' else None
    return View(session=session, question=question)

def create_app(db: Path | None = None,
               advisor: Callable[[Session, list[Chunk]], Awaitable[Advice]] = ai.advise,
               recognizer: Callable[[str], Awaitable[Identification]] = ai.identify,
               pipeline: SupportPipeline | None = None) -> FastAPI:
    app = FastAPI(title='说明书售后助手', docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])
    store = Store(db or ROOT / 'data/sessions.sqlite3')
    app.state.store = store
    workflow = pipeline if pipeline is not None else MemoryPipeline()

    @app.middleware('http')
    async def local_request(request: Request, call_next: Callable[..., Awaitable[Response]]) -> Response:
        if request.method == 'POST':
            # 浏览器同源自定义头阻止外部页面提交；此本机 demo 不提供跨域开放。
            if request.headers.get('x-support-client') != 'local-web':
                return JSONResponse({'detail': '请从本地助手页面操作。'}, status_code=403)
            origin = request.headers.get('origin')
            if origin and origin != f'{request.url.scheme}://{request.url.netloc}':
                return JSONResponse({'detail': '禁止跨域请求。'}, status_code=403)
            body = await request.body()
            if len(body) > 7_100_000:
                return JSONResponse({'detail': '请求过大。'}, status_code=413)
        return await call_next(request)

    def get_current(session_id: str, version: int | None = None) -> Session:
        try:
            session = store.get(session_id)
        except KeyError:
            raise HTTPException(404, '会话不存在。') from None
        if version is not None and session.version != version:
            raise HTTPException(409, '会话已更新，请刷新后继续。')
        return session

    @app.exception_handler(ConflictError)
    async def conflict_handler(request: Request, error: ConflictError) -> JSONResponse:
        return JSONResponse({'detail': '会话已更新，请刷新后继续。'}, status_code=409)

    @app.get('/')
    async def index() -> FileResponse:
        return FileResponse(ROOT / 'web/index.html')

    @app.get('/api/source')
    async def source() -> dict[str, str | int]:
        return json.loads((ROOT / 'manuals/source.json').read_text(encoding='utf-8'))

    @app.get('/manual.pdf')
    async def manual() -> FileResponse:
        return FileResponse(ROOT / 'manuals/a119-mini2.pdf', media_type='application/pdf')

    @app.post('/api/identify')
    async def identify(payload: ImageRequest) -> dict[str, str | bool | None]:
        try:
            image = validate_image(payload.image)
        except ValueError:
            raise HTTPException(422, '图片须为有效 PNG/JPEG/WebP，最大 5MB、1600 万像素，长宽至少 32 像素。') from None
        try:
            result = await recognizer(image)
        except Exception:
            raise HTTPException(502, '图片识别服务未成功返回。请检查模型服务后重试，或人工确认型号。') from None
        matched = canonical_model(result.model) if result.readable else None
        # 必须有可见型号文字佐证；仍是候选，不替代用户确认。
        compact = ''.join(c for c in result.visible_text.upper() if c.isalnum())
        if matched and 'A119MINI2' not in compact:
            matched = None
        return {'brand': result.brand, 'model': result.model, 'visible_text': result.visible_text,
                'readable': result.readable, 'matched_model': matched, 'requires_confirmation': True}

    @app.post('/api/sessions', response_model=View)
    async def start(payload: StartRequest) -> View:
        if not payload.confirmed or canonical_model(payload.model) != MODEL:
            raise HTTPException(422, '请先确认准确型号；当前知识库只支持 A119 MINI 2。')
        session = Session(id=uuid.uuid4().hex, model=MODEL, issue=payload.issue, description=payload.description)
        store.create(session)
        return view(session)

    @app.get('/api/knowledge')
    async def knowledge() -> KnowledgeView:
        try:
            index = workflow.index
            return KnowledgeView(manifest=index.manifest, documents=index.docs)
        except Exception:
            raise HTTPException(503, '知识库未就绪或索引校验失败，请完成建库后重启服务。') from None

    @app.post('/api/intake', response_model=IntakeView)
    async def intake(payload: IntakeRequest) -> IntakeView:
        if not payload.description.strip():
            raise HTTPException(422, '请描述具体问题。')
        if not payload.confirmed or canonical_model(payload.model) != MODEL:
            raise HTTPException(422, '请先核对并确认准确型号。')
        try:
            result = await workflow.intake(payload.description.strip(), MODEL)
        except Exception:
            raise HTTPException(502, '本次意图识别或混合检索未完成。请检查模型服务和知识库后重试，未创建不完整会话。') from None
        if result.session is None:
            return IntakeView(intent=result.intent, reply=result.reply)
        store.create(result.session)
        return IntakeView(intent=result.intent, reply='', view=view(result.session))

    @app.get('/api/sessions/{session_id}', response_model=View)
    async def get(session_id: str) -> View:
        return view(get_current(session_id))

    @app.post('/api/sessions/{session_id}/answer', response_model=View)
    async def answer(session_id: str, payload: AnswerRequest) -> View:
        session = get_current(session_id, payload.version)
        question = next_question(session)
        if session.status != 'collecting' or question is None or payload.key != question.key:
            raise HTTPException(409, '当前问题已变化，请刷新后继续。')
        if not payload.answer.strip():
            raise HTTPException(422, '请回答当前问题，也可以填写不确定。')
        if session.workflow_version==3:
            try:
                session=await process_message(session,payload.answer,
                    workflow.extractor if isinstance(workflow,MemoryPipeline) else extract_patch,question)
            except Exception:
                raise HTTPException(502,'本次记忆更新未通过校验，原会话已保留，请重试。') from None
            return view(store.save(session,payload.version))
        session.answers[payload.key] = payload.answer.strip()
        if next_question(session) is None:
            session.status = 'ready'
        return view(store.save(session, payload.version))

    @app.post('/api/sessions/{session_id}/message', response_model=View)
    async def message(session_id: str, payload: MessageRequest) -> View:
        session=get_current(session_id,payload.version)
        if session.workflow_version!=3 or session.status in {'resolved','handoff'}:
            raise HTTPException(409,'请在启用记忆且未结束的会话中补充信息。')
        if not payload.text.strip():raise HTTPException(422,'请填写补充信息或操作结果。')
        if payload.action_id is not None and payload.action_id not in get_memory(session).actions:
            raise HTTPException(422,'请选择本次会话已给出的操作。')
        try:
            session=await process_message(session,payload.text,
                workflow.extractor if isinstance(workflow,MemoryPipeline) else extract_patch,
                next_question(session) if session.status=='collecting' and payload.action_id is None else None,payload.action_id)
        except Exception:
            raise HTTPException(502,'本次记忆更新未通过校验，原会话已保留，请重试。') from None
        return view(store.save(session,payload.version))

    @app.post('/api/sessions/{session_id}/advise', response_model=View)
    async def advise(session_id: str, payload: VersionRequest) -> View:
        session = get_current(session_id, payload.version)
        if session.status != 'ready':
            raise HTTPException(409, '请先完成必要信息，已有建议无需重复生成。')
        if session.memory is not None and session.memory.clarification:
            raise HTTPException(409,'请先补充待澄清的信息，再生成建议。')
        try:
            if session.workflow_version in {2,3}:
                result, trace = await workflow.advise(session)
                session.retrieval_history.append(trace)
            else:
                # 保留历史版本会话的原有处理路径，不作为新流程异常时的替代路线。
                chunks = chunks_for(session.issue)
                result = await advisor(session, chunks)
                validate_advice(result, chunks)
        except NoRemainingActions as done:
            session.retrieval_history.append(done.trace)
            memory=get_memory(session)
            memory.notice='当前命中证据中没有尚未执行的可用步骤。已保留检查结果，请下载摘要交由人工继续核查。'
            append_turn(session,'assistant',memory.notice)
            session.status='handoff'
            return view(store.save(session,payload.version))
        except Exception:
            raise HTTPException(502, '本次未获得通过来源校验的建议。会话已保留，请检查模型服务后重试。') from None
        session.advice = result
        session.status = 'advised'
        if session.workflow_version==3:remember_advice(session)
        return view(store.save(session, payload.version))

    @app.post('/api/sessions/{session_id}/outcome', response_model=View)
    async def outcome(session_id: str, payload: OutcomeRequest) -> View:
        session = get_current(session_id, payload.version)
        if session.status != 'advised':
            raise HTTPException(409, '当前会话不可重复结束。')
        session.status = payload.outcome
        if session.workflow_version==3:
            append_turn(session,'user','记录处理结果：'+('已解决' if payload.outcome=='resolved' else '仍未解决，转人工'))
        return view(store.save(session, payload.version))

    @app.get('/api/sessions/{session_id}/export')
    async def export(session_id: str) -> Response:
        session = get_current(session_id)
        names = {q.key: q.text for q in (session.questions if session.workflow_version in {2,3} else QUESTIONS[session.issue])}
        labels = {'collecting': '收集信息', 'ready': '待生成建议', 'advised': '待确认处理结果', 'resolved': '用户标记已解决', 'handoff': '待人工接手'}
        lines = ['说明书售后会话摘要（仅供演示使用）', f'会话：{session.id}', f'型号：{session.model}',
                 f'状态：{labels[session.status]} ({session.status})', f'用户描述：{session.description}', '已收集信息：']
        lines += [f'{names[key]} {value}' for key, value in session.answers.items()]
        if session.memory is not None:
            memory=session.memory
            lines += ['本次会话记忆（用户报告，非设备自动验证）：',f'记忆版本：{memory.revision}',memory.notice]
            if memory.clarification:lines += ['待澄清：'+memory.clarification]
            lines += [f'{key}：{fact.value} [{fact.status}] 来源轮次{fact.source_turn} 原话：{fact.quote}' for key,fact in memory.facts.items()]
            lines += ['事实变更历史：']+[f'事件{event.id} {event.key}={event.value} [{event.status}] 替代事件{event.supersedes} 来源轮次{event.source_turn}' for event in memory.events]
            lines += ['操作结果：']+[f'{a.action_id} {a.text} [{a.status}] {a.result} 来源轮次{a.source_turn}' for a in memory.actions.values()]
            lines += ['操作变更历史：']+[f'{a.action_id} [{a.status}] {a.result} 来源轮次{a.source_turn}' for a in memory.action_events]
            lines += ['完整会话原话：']+[f'{t.id} {t.role}：{t.text}' for t in memory.turns]
            lines += ['已失效建议（仅供追溯）：']+[item.content for item in memory.advice_history]
        for number, trace in enumerate(session.retrieval_history, 1):
            lines += [f'检索过程 {number}：{trace.intent.name}；{trace.intent.reason}',
                      f'型号范围：{trace.model_filter}；索引：{trace.index_id}',
                      '原始问题与回答：'+trace.original, '中文扩写：'+trace.expansion.chinese,
                      '英文扩写：'+trace.expansion.english,
                      '实际召回查询：'+json.dumps(trace.queries, ensure_ascii=False),
                      f'向量模型：{trace.embedding_model}；维度：{trace.dimensions}；RRF k={trace.rrf_k}（排序分数不是准确率）']
            for lane_name, hits in [('BM25', trace.bm25), ('FAISS', trace.vector), ('RRF融合', trace.fused)]:
                lines += [lane_name + '：']
                for i, hit in enumerate(hits, 1):
                    lines += [f'{i}. {hit.chunk_id} PDF第{hit.page}页 BM25={hit.bm25_score} 向量={hit.vector_score} RRF={hit.rrf_score}',
                              '章节：'+' > '.join(hit.chapter_path), '原文坐标：'+(hit.source_id or hit.chunk_id), hit.text]
            lines += ['关联证据补充（非召回命中）：'+', '.join(trace.linked_evidence)]
        if session.advice:
            lines += ['整体判断：' + session.advice.summary]
            for i, step in enumerate(session.advice.steps, 1):
                lines += [f'{i}. {step.action}', '观察：' + step.expected]
                lines += [f'PDF 第 {e.page} 页：{e.quote}' for e in step.evidence]
            lines += ['转人工条件：' + session.advice.handoff]
        lines += ['来源：' + str((await source())['url']), '说明书版本：V25.12.18', '转人工仅生成摘要，未发送给客服。']
        return Response('\n'.join(lines), media_type='text/plain; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="support-{session.id}.txt"'})

    app.mount('/static', StaticFiles(directory=ROOT / 'web'), name='static')
    return app

app = create_app()
