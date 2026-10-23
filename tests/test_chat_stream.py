"""SPEC-011：真实流式边界、原子保存和并行调用的确定性验证。"""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableLambda

from support_demo import ai, streaming
from support_demo.app import create_app
from support_demo.catalog import Selection, build_advice
from support_demo.domain import MODEL, Session, chunks_for
from support_demo.memory import apply_patch
from support_demo.memory_pipeline import MemoryPipeline
from support_demo.memory_types import FactUpdate, MemoryPatch, SessionMemory
from support_demo.pipeline import IntakeResult, SupportPipeline
from support_demo.retrieval_types import IntentResult
from test_retrieval import make_index, trace_for

HEADERS = {'X-Support-Client': 'local-web'}

def decode(frame):
    lines = frame.strip().splitlines()
    return lines[0][7:], json.loads(lines[1][6:])

def test_status_and_draft_arrive_before_completion_and_disconnect_cancels():
    async def check():
        release = asyncio.Event()
        cancelled = asyncio.Event()
        async def operation():
            try:
                await streaming.publish('draft', {'text': '正在生成的真实分段'})
                await release.wait()
            finally:
                cancelled.set()
        output = streaming.events(operation)
        assert decode(await anext(output))[0] == 'status'
        assert decode(await asyncio.wait_for(anext(output), 1))[0] == 'draft'
        assert not release.is_set() and not cancelled.is_set()
        await output.aclose()
        assert cancelled.is_set()
    asyncio.run(check())

def test_model_stream_emits_partial_summary_and_keeps_final_validation(monkeypatch):
    async def check():
        release = asyncio.Event()
        async def model(prompt):
            yield AIMessageChunk(content='{"summary":"目前')
            await release.wait()
            yield AIMessageChunk(content='还需确认连接状态","step_ids":["wifi_enable"],"handoff":"未解决请联系人工"}')
        monkeypatch.setattr(ai, 'client', lambda _: RunnableLambda(model))
        result = []
        async def operation():
            result.append(await ai.advise(Session(id='s', model=MODEL, issue='wifi', description='无画面'), chunks_for('wifi')))
        output = streaming.events(operation)
        await anext(output)
        event, data = decode(await asyncio.wait_for(anext(output), 1))
        assert event == 'draft' and data['text'] == '目前' and not result
        release.set()
        remaining = [decode(frame) async for frame in output]
        assert remaining[-1][0] == 'done'
        assert result[0].steps[0].evidence
    asyncio.run(check())

    async def invalid(prompt):
        yield AIMessageChunk(content='{"summary":"草稿","step_ids":["made_up"],"handoff":"交接"}')
    monkeypatch.setattr(ai, 'client', lambda _: RunnableLambda(invalid))
    async def failure():
        async def operation():
            await ai.advise(Session(id='s', model=MODEL, issue='wifi', description='无画面'), chunks_for('wifi'))
        frames = [decode(frame) async for frame in streaming.events(operation)]
        assert frames[-1][0] == 'error' and not any(e == 'done' for e, _ in frames)
        assert 'made_up' not in str(frames[-1])
    asyncio.run(failure())

class ChatFixture(MemoryPipeline):
    fail = False
    async def intake(self, description, model):
        session = Session(id='chat-test', model=MODEL, issue='wifi', description=description,
                          workflow_version=3, memory=SessionMemory())
        apply_patch(session, description, MemoryPatch(facts=[FactUpdate(key='problem', value=description, status='known', quote=description)]))
        return IntakeResult(IntentResult(name='wifi', reason='连接'), '', session)
    async def advise(self, session):
        await streaming.publish('draft', {'text': '连接情况需要确认'})
        if self.fail:
            raise RuntimeError('PRIVATE_TOKEN')
        return build_advice(Selection(summary='请核对连接状态', step_ids=['wifi_enable'], handoff='未解决请联系人工'), 'wifi'), trace_for(make_index())
    @staticmethod
    async def extractor(session, text, question, action):
        return MemoryPatch(clarification='请说明具体是哪项检查没有改善。')

def frames(response):
    return [decode(frame) for frame in response.text.split('\n\n') if frame.strip()]

def test_chat_auto_advice_state_order_restore_conflict_and_auth(tmp_path):
    app = create_app(tmp_path/'chat.db', pipeline=ChatFixture())
    client = TestClient(app, headers=HEADERS)
    payload = {'description': 'App无画面', 'model': MODEL, 'confirmed': True}
    events = frames(client.post('/api/intake/stream', json=payload))
    assert [e for e, _ in events] == ['status', 'state', 'draft', 'state', 'done']
    state = events[-2][1]
    assert state['session']['status'] == 'advised' and state['session']['advice']['steps'][0]['evidence']
    assert client.get('/api/sessions/chat-test').json() == state
    conflict = frames(client.post('/api/sessions/chat-test/message/stream', json={'version': 0, 'text': '没有'}))
    assert conflict[-1][0] == 'error' and conflict[-1][1]['code'] == 409
    bad_action = frames(client.post('/api/sessions/chat-test/message/stream', json={'version': 1, 'text': '没有', 'action_id': 'unknown'}))
    assert bad_action[-1][1]['code'] == 422
    assert client.post('/api/intake/stream', json=payload, headers={'Origin': 'https://evil.test'}).status_code == 403
    assert TestClient(app).post('/api/intake/stream', json=payload).status_code == 403
    clarification = frames(client.post('/api/sessions/chat-test/message/stream', json={'version': 1, 'text': '没用'}))
    assert clarification[-2][1]['session']['memory']['clarification']
    assert not any(e == 'draft' for e, _ in clarification)

def test_failed_advice_keeps_saved_input_without_partial_advice(tmp_path):
    workflow = ChatFixture(); workflow.fail = True
    app = create_app(tmp_path/'chat.db', pipeline=workflow)
    client = TestClient(app, headers=HEADERS)
    response = client.post('/api/intake/stream', json={'description': '没有画面', 'model': MODEL, 'confirmed': True})
    events = frames(response)
    assert events[-1][0] == 'error' and 'PRIVATE_TOKEN' not in response.text
    saved = client.get('/api/sessions/chat-test').json()['session']
    assert saved['description'] == '没有画面' and saved['advice'] is None and saved['status'] == 'ready'
    assert frames(client.post('/api/intake/stream', json={'description': '问题', 'model': MODEL, 'confirmed': False}))[-1][1]['code'] == 422

def test_initial_question_selection_and_memory_run_concurrently(monkeypatch):
    async def check():
        selection_started, memory_started = asyncio.Event(), asyncio.Event()
        async def retrieve(self, session, intent):
            return trace_for(make_index())
        async def extract(session, text, question, action):
            memory_started.set()
            await asyncio.wait_for(selection_started.wait(), 1)
            assert session.questions
            return MemoryPatch(facts=[
                FactUpdate(key='problem', value=text, quote=text, status='known'),
                FactUpdate(key=session.questions[0].key, value=text, quote=text, status='known'),
            ])
        async def classify(text): return IntentResult(name='wifi', reason='连接')
        async def select(text, candidates):
            selection_started.set()
            await asyncio.wait_for(memory_started.wait(), 1)
            return []
        monkeypatch.setattr(SupportPipeline, 'retrieve', retrieve)
        workflow = MemoryPipeline(index=make_index(), classifier=classify, selector=select)
        workflow.extractor = extract
        result = await workflow.intake('App无画面', MODEL)
        assert result.session.status == 'ready'
        assert result.session.answers == {}
        assert len(result.session.memory.facts) == 2
        assert result.session.retrieval_history[-1].memory_revision == result.session.memory.revision
    asyncio.run(check())

def test_same_revision_reuses_retrieval_but_new_facts_require_search(monkeypatch):
    workflow = MemoryPipeline(index=make_index())
    session = Session(id='s', model=MODEL, issue='wifi', description='无画面', workflow_version=3, memory=SessionMemory())
    trace = trace_for(workflow.index); trace.memory_revision = 0; session.retrieval_history = [trace]
    calls = []
    async def retrieve(*args): calls.append('search'); return trace
    async def advise(*args): return build_advice(Selection(summary='核对', step_ids=['wifi_enable'], handoff='交接'), 'wifi')
    monkeypatch.setattr(workflow, 'retrieve', retrieve); monkeypatch.setattr(ai, 'advise', advise)
    asyncio.run(workflow.advise(session)); assert calls == []
    session.memory.revision = 1
    asyncio.run(workflow.advise(session)); assert calls == ['search']
