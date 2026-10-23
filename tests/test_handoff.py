"""SPEC-012：交接摘要事实准确性与恢复、下载一致性。"""
from fastapi.testclient import TestClient
from support_demo.app import create_app
from support_demo.domain import MODEL, Session
from support_demo.handoff import handoff_summary
from support_demo.memory_types import SessionMemory, FactEvent, ActionMemory
from test_support import HEADERS, ready, fake_advisor


def test_effective_facts_and_action_states_only():
    memory = SessionMemory()
    for key, value, status in [('problem', 'App无画面', 'known'), ('wifi_led', '旧蓝灯结论', 'retracted'), ('phone_connection', '不知道', 'unknown')]:
        memory.facts[key] = FactEvent(id=1, source_turn=1, key=key, value=value, status=status, quote=value)
    for state in ['completed', 'unable', 'suggested', 'retracted']:
        memory.actions[state] = ActionMemory(action_id=state, text='检查'+state, status=state, result='结果'+state, quote='原话', source_turn=1)
    session = Session(id='handoff-test', model=MODEL, issue='wifi', description='过时主诉', workflow_version=3, memory=memory, status='handoff')
    text = handoff_summary(session)
    assert 'App无画面' in text and '过时主诉' not in text and '旧蓝灯结论' not in text
    assert '原结论已撤回' in text and '用户不确定' in text
    assert '结果completed' in text and '结果unable' in text
    assert '结果suggested' not in text and '结果retracted' not in text
    assert '仅建议，尚未反馈' in text


def test_outcome_refresh_and_download_match(tmp_path):
    app = create_app(tmp_path/'sessions.db', advisor=fake_advisor)
    client = TestClient(app, headers=HEADERS)
    view = ready(client); path = '/api/sessions/'+view['session']['id']
    assert client.get(path+'/handoff').status_code == 409
    view = client.post(path+'/advise', json={'version':view['session']['version']}).json()
    view = client.post(path+'/outcome', json={'version':view['session']['version'], 'outcome':'handoff'}).json()
    assert '无法正常使用' in view['handoff_summary']
    assert client.get(path).json()['handoff_summary'] == view['handoff_summary']
    downloaded = client.get(path+'/handoff')
    assert downloaded.text == view['handoff_summary']
    assert 'attachment' in downloaded.headers['content-disposition']
