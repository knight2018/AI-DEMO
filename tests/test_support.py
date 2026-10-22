"""SPEC-005/006：售后工作流、来源与失败路径回归。"""
import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from support_demo.app import create_app
from support_demo.domain import (ROOT, MODEL, Advice, Identification, Session, canonical_model,
                                 chunks_for, validate_advice, validate_image)
from support_demo import ai
from support_demo.catalog import CATALOG, Selection, build_advice

HEADERS = {'X-Support-Client': 'local-web'}

def image_url() -> str:
    stream = io.BytesIO()
    Image.new('RGB', (64, 64), 'white').save(stream, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()

def sample_advice(issue: str = 'wifi') -> Advice:
    chunk = chunks_for(issue)[0]
    return Advice.model_validate({'summary': '需根据已知现象继续检查，尚不能确定故障原因。',
        'steps': [{'action': '检查相机当前显示状态。', 'expected': '记录实际显示的状态。',
                   'evidence': [{'chunk_id': chunk['id'], 'page': chunk['page'], 'quote': chunk['text'][:100]}]}],
        'handoff': '未解决时携带现象与检查结果联系人工客服。'})

async def fake_advisor(session: Session, chunks: list[dict]) -> Advice:
    return sample_advice(session.issue)

async def fake_recognizer(data_url: str) -> Identification:
    return Identification(brand='VIOFO', model=MODEL, visible_text='VIOFO A119 MINI 2', readable=True)

@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path/'sessions.db', fake_advisor, fake_recognizer), headers=HEADERS)

def start(client: TestClient, issue: str = 'wifi') -> dict:
    response = client.post('/api/sessions', json={'model': MODEL, 'confirmed': True, 'issue': issue, 'description': '无法正常使用'})
    assert response.status_code == 200
    return response.json()

def ready(client: TestClient, issue: str = 'wifi') -> dict:
    view = start(client, issue)
    while view['question']:
        session, question = view['session'], view['question']
        view = client.post(f"/api/sessions/{session['id']}/answer", json={'version': session['version'], 'key': question['key'], 'answer': '不确定'}).json()
    return view

def test_spec005_tc01_manual_provenance() -> None:
    source = json.loads((ROOT/'manuals/source.json').read_text(encoding='utf-8'))
    assert hashlib.sha256((ROOT/'manuals/a119-mini2.pdf').read_bytes()).hexdigest() == source['sha256']
    assert source['version'] == 'V25.12.18'
    assert source['pages'] == 17
    assert source['url'].startswith('https://viofotech.com/')
    assert '512GB' in chunks_for('recording')[0]['text']

@pytest.mark.parametrize('value,expected', [('A119 MINI2', MODEL), ('VIOFO A119 MINI 2', MODEL), ('A119 MINI', None), ('A229', None), ('A119 MINI 2 PRO', None)])
def test_spec005_tc02_exact_model(value: str, expected: str | None) -> None:
    assert canonical_model(value) == expected

def test_spec005_tc02_confirmation(client: TestClient) -> None:
    response = client.post('/api/identify', json={'image': image_url()})
    assert response.json()['requires_confirmation'] is True
    assert response.json()['matched_model'] == MODEL
    for model, confirmed in [(MODEL, False), ('A119 MINI', True)]:
        assert client.post('/api/sessions', json={'model': model, 'confirmed': confirmed, 'issue': 'wifi', 'description': 'test'}).status_code == 422

@pytest.mark.parametrize('value', ['https://example.com/photo.png', 'data:image/png;base64,@@@@', 'data:image/png;base64,AAAA'])
def test_spec005_tc03_invalid_image(value: str) -> None:
    with pytest.raises(ValueError):
        validate_image(value)

def test_spec005_tc03_image_validation() -> None:
    valid = image_url()
    assert validate_image(valid) == valid
    with pytest.raises(ValueError):
        validate_image(valid.replace('image/png', 'image/jpeg'))
    with pytest.raises(ValueError):
        validate_image('data:image/png;base64,' + 'A'*7_000_001)

def test_spec005_tc03_unknown_and_missing_visible_text(tmp_path: Path) -> None:
    async def unknown(data: str) -> Identification:
        return Identification(readable=False)
    async def unsupported(data: str) -> Identification:
        return Identification(readable=True, model='A229', visible_text='A229')
    async def hallucinated(data: str) -> Identification:
        return Identification(readable=True, model=MODEL, visible_text='VIOFO')
    for index, recognizer in enumerate([unknown, unsupported, hallucinated]):
        client = TestClient(create_app(tmp_path/f'{index}.db', fake_advisor, recognizer), headers=HEADERS)
        assert client.post('/api/identify', json={'image': image_url()}).json()['matched_model'] is None

@pytest.mark.parametrize('issue,count', [('wifi', 3), ('recording', 2), ('power', 2)])
def test_spec005_tc04_question_sequence(client: TestClient, issue: str, count: int) -> None:
    view = ready(client, issue)
    assert view['session']['status'] == 'ready'
    assert len(view['session']['answers']) == count
    assert view['question'] is None

def test_spec005_tc04_empty_and_duplicate(client: TestClient) -> None:
    view = start(client)
    path = f"/api/sessions/{view['session']['id']}/answer"
    payload = {'version': 0, 'key': view['question']['key'], 'answer': '  '}
    assert client.post(path, json=payload).status_code == 422
    payload['answer'] = '已显示'
    assert client.post(path, json=payload).status_code == 200
    assert client.post(path, json=payload).status_code == 409
    assert client.post('/api/sessions', json={'model': MODEL, 'confirmed': True, 'issue': 'wifi', 'description': '  '}).status_code == 422

def test_spec005_tc05_evidence_and_risky_steps() -> None:
    advice = sample_advice()
    validate_advice(advice, chunks_for('wifi'))
    for field, value in [('page', 99), ('chunk_id', 'missing'), ('quote', 'this quote was invented')]:
        broken = advice.model_copy(deep=True)
        setattr(broken.steps[0].evidence[0], field, value)
        with pytest.raises(ValueError, match='invalid_evidence'):
            validate_advice(broken, chunks_for('wifi'))
    advice.steps[0].action = '现在格式化存储卡'
    with pytest.raises(ValueError, match='unsafe_step'):
        validate_advice(advice, chunks_for('wifi'))
    advice.steps[0].action = '长按 [ ] 按钮开机'
    with pytest.raises(ValueError, match='unusable_step'):
        validate_advice(advice, chunks_for('wifi'))
    advice.steps[0].action = '关机后取出存储卡查看文件'
    with pytest.raises(ValueError, match='unsafe_card_handling'):
        validate_advice(advice, chunks_for('wifi'))

def test_spec005_tc05_catalog_all_sources() -> None:
    for issue, options in CATALOG.items():
        for step_id in options:
            advice = build_advice(Selection(summary='核对现象', step_ids=[step_id], handoff='联系人工'), issue)
            validate_advice(advice, chunks_for(issue))
    for step_ids in [['invented'], ['wifi_app', 'wifi_app'], ['card_specs']]:
        with pytest.raises(ValueError, match='invalid_step_selection'):
            build_advice(Selection(summary='核对现象', step_ids=step_ids, handoff='联系人工'), 'wifi')

def test_spec005_tc06_failure_preserves_session(tmp_path: Path) -> None:
    calls = 0
    async def fail_once(session: Session, chunks: list[dict]) -> Advice:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError('secret_should_not_escape')
        return sample_advice()
    client = TestClient(create_app(tmp_path/'db', fail_once, fake_recognizer), headers=HEADERS)
    view = ready(client)
    path = f"/api/sessions/{view['session']['id']}"
    response = client.post(path+'/advise', json={'version': 3})
    assert response.status_code == 502 and 'secret_should_not_escape' not in response.text
    assert client.get(path).json() == view
    assert client.post(path+'/advise', json={'version': 3}).json()['session']['status'] == 'advised'

@pytest.mark.parametrize('outcome', ['resolved', 'handoff'])
def test_spec005_tc07_tc10_persistence_export_outcome(client: TestClient, outcome: str) -> None:
    view = ready(client)
    path = f"/api/sessions/{view['session']['id']}"
    advised = client.post(path+'/advise', json={'version': view['session']['version']}).json()
    assert client.get(path).json() == advised
    response = client.post(path+'/outcome', json={'version': advised['session']['version'], 'outcome': outcome})
    assert response.status_code == 200 and response.json()['session']['status'] == outcome
    exported = client.get(path+'/export')
    assert '无法正常使用' in exported.text and 'PDF 第 10 页' in exported.text
    assert outcome in exported.text and '\ufffd' not in exported.text
    assert client.post(path+'/outcome', json={'version': response.json()['session']['version'], 'outcome': outcome}).status_code == 409

def test_spec005_tc07_reopen_store(tmp_path: Path) -> None:
    db = tmp_path/'db'
    first = TestClient(create_app(db, fake_advisor, fake_recognizer), headers=HEADERS)
    view = ready(first)
    second = TestClient(create_app(db, fake_advisor, fake_recognizer), headers=HEADERS)
    assert second.get('/api/sessions/'+view['session']['id']).json() == view

def test_spec005_tc08_local_boundary(client: TestClient) -> None:
    assert client.get('/').status_code == 200
    assert client.get('/manual.pdf').headers['content-type'] == 'application/pdf'
    assert client.get('/api/sessions/missing').status_code == 404
    assert client.post('/api/sessions', json={}, headers={'X-Support-Client': ''}).status_code == 403
    assert client.post('/api/sessions', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.get('/', headers={'Host': 'evil.example'}).status_code == 400

def test_spec006_tc01_fixed_model() -> None:
    assert ai.VISION_MODEL == ai.TEXT_MODEL == 'deepseek-flash'

def test_spec006_tc03_vision_failure(tmp_path: Path) -> None:
    async def failed(data: str) -> Identification:
        raise RuntimeError('secret_should_not_escape')
    client = TestClient(create_app(tmp_path/'db', fake_advisor, failed), headers=HEADERS)
    response = client.post('/api/identify', json={'image': image_url()})
    assert response.status_code == 502 and 'secret_should_not_escape' not in response.text
