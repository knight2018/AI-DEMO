"""SPEC-009：有来源的事实更新、问答记忆与操作进度回归。"""
import asyncio
from pathlib import Path

import pytest
import numpy as np
from fastapi.testclient import TestClient

from support_demo.app import create_app
from support_demo.catalog import Selection, build_advice
from support_demo.domain import MODEL, Question, Session, next_question
from support_demo.memory import (apply_patch, current_context, pending_questions, process_message,
    question_key, remember_advice, sync_progress)
from support_demo.memory_pipeline import MemoryPipeline, NoRemainingActions
from support_demo.memory_types import ActionUpdate, FactUpdate, MemoryPatch, SessionMemory
from support_demo.pipeline import IntakeResult
from support_demo.retrieval_types import IntentResult
from support_demo.store import Store
from test_retrieval import make_index, trace_for

def session() -> Session:
    s=Session(id='memory-fixture',model=MODEL,issue='wifi',description='旧原话：手机没有连接，App无画面',
        workflow_version=3,memory=SessionMemory(),questions=[
            Question(key='phone_connection',text='手机已经连接相机热点了吗？',reason='区分连接阶段',options=['是','没有','不知道']),
            Question(key='app_state',text='App是否出现实时画面？',reason='核对画面',options=['有','没有','不知道'])])
    apply_patch(s,'App无画面',MemoryPatch(facts=[FactUpdate(key='problem',value='App无画面',status='known',quote='App无画面')]))
    return s

def fact(key: str, value: str, quote: str, status: str = 'known') -> MemoryPatch:
    return MemoryPatch(facts=[FactUpdate(key=key,value=value,status=status,quote=quote)])

def advice(s: Session, ids: list[str]) -> None:
    s.advice=build_advice(Selection(summary='核对当前状态',step_ids=ids,handoff='未解决请转人工'),s.issue)
    s.status='advised';remember_advice(s)

def test_spec009_tc01_initial_facts_aliases_and_multi_answer() -> None:
    s=session()
    apply_patch(s,'已连接热点，App还是黑屏',MemoryPatch(facts=[
        FactUpdate(key='phone_connection',value='已连接',status='known',quote='已连接热点'),
        FactUpdate(key='app_state',value='无画面',status='known',quote='App还是黑屏')]))
    assert next_question(s) is None and s.status=='ready'
    apply_patch(s,'屏保不知道',fact('screen_saver_setting','不知道','不知道','unknown'))
    q=Question(key='screen_saver_timeout_configured',text='屏保设置过吗？',reason='屏保',options=['不知道'])
    assert pending_questions(s,[q,q])==[]
    assert pending_questions(s,s.questions)==[]

@pytest.mark.parametrize(('text','status','value'),[('没有','known','未连接'),('不知道','unknown','不知道')])
def test_spec009_tc02_short_answer_uses_active_question(text: str,status: str,value: str) -> None:
    s=session();q=next_question(s)
    async def extractor(state: Session, raw: str, question: Question | None, action: str | None) -> MemoryPatch:
        assert question==q and action is None
        return fact('phone_connection',value,raw,status)
    result=asyncio.run(process_message(s,text,extractor,q))
    assert 'phone_connection' not in s.memory.facts
    assert result.memory.facts['phone_connection'].status==status
    assert next_question(result).key=='app_state'
    user=next(t for t in result.memory.turns if t.text==text)
    assert user.question_key=='phone_connection'
    if status=='unknown':assert '手机是否连接' not in current_context(result,for_search=True)

def test_spec009_tc03_ambiguous_short_answer_cannot_write() -> None:
    s=session()
    async def must_not_run(*args: object) -> MemoryPatch:raise AssertionError('无锚点短句应由程序澄清')
    result=asyncio.run(process_message(s,'没用',must_not_run))
    assert result.memory.clarification and result.memory.facts==s.memory.facts
    q=Question(key='power_state',text='屏幕和指示灯是否都亮？',reason='复合问题',options=['有','没有'])
    result=asyncio.run(process_message(s,'没有',must_not_run,q))
    assert '分别' in result.memory.clarification and 'power_state' not in result.memory.facts

def test_spec009_tc04_retract_wrong_time_recording_and_keep_history() -> None:
    s=session();s.issue='recording'
    apply_patch(s,'黑屏后那几分钟有视频',MemoryPatch(facts=[
        FactUpdate(key='recording_files',value='本次黑屏后有视频',status='known',quote='黑屏后那几分钟有视频'),
        FactUpdate(key='recording_state',value='所查时段有录像',status='known',quote='黑屏后那几分钟有视频')]))
    old=s.memory.facts['recording_files']
    advice(s,['screen_saver'])
    apply_patch(s,'刚才看错了，那是昨天的视频',MemoryPatch(facts=[
        FactUpdate(key='recording_files',value='本次未确认',status='retracted',quote='那是昨天的视频'),
        FactUpdate(key='recording_state',value='本次未确认',status='retracted',quote='那是昨天的视频')]))
    assert s.advice is None and len(s.memory.advice_history)==1
    assert s.memory.facts['recording_files'].supersedes==old.id
    assert any(e.value=='本次黑屏后有视频' for e in s.memory.events)
    context=current_context(s)
    assert '所查时段有录像' not in context and '已撤回' in context
    assert '旧原话' not in context

def test_spec009_tc05_correction_preserves_unrelated_fact() -> None:
    s=session()
    apply_patch(s,'没有连接',fact('phone_connection','未连接','没有连接'))
    apply_patch(s,'屏保是一分钟',fact('screen_saver_setting','一分钟','一分钟'))
    apply_patch(s,'现在连接好了',fact('phone_connection','已连接','连接好了'))
    assert s.memory.facts['screen_saver_setting'].value=='一分钟'
    assert '未连接' not in current_context(s)
    assert next_question(s).key=='app_state'

def test_spec009_tc06_tc07_action_progress_excluded_and_retracted(monkeypatch: pytest.MonkeyPatch) -> None:
    s=session();advice(s,['wifi_app'])
    patch=MemoryPatch(actions=[ActionUpdate(action_id='wifi_app',status='completed',result='执行后仍无画面',quote='做了，还是没用')])
    apply_patch(s,'做了，还是没用',patch,action_id='wifi_app')
    assert s.status!='resolved' and s.advice is None
    assert s.memory.actions['wifi_app'].status=='completed'
    index=make_index();trace=trace_for(index);s.retrieval_history=[trace]
    workflow=MemoryPipeline(index=index)
    async def retrieve(*args: object):return trace
    monkeypatch.setattr(workflow,'retrieve',retrieve)
    monkeypatch.setattr(workflow,'grounded_context',lambda *args:([],['wifi_app']))
    with pytest.raises(NoRemainingActions):asyncio.run(workflow.advise(s))
    apply_patch(s,'刚才没操作，记错了',MemoryPatch(actions=[ActionUpdate(action_id='wifi_app',status='retracted',result='未确认执行',quote='刚才没操作')]))
    assert s.memory.actions['wifi_app'].status=='retracted'
    assert '执行后仍无画面' not in current_context(s)
    assert len(s.memory.action_events)==3

@pytest.mark.parametrize('patch',[
    MemoryPatch(facts=[FactUpdate(key='password',value='123',status='known',quote='原话')]),
    MemoryPatch(facts=[FactUpdate(key='phone_connection',value='已连接',status='known',quote='不存在的文字')]),
    MemoryPatch(actions=[ActionUpdate(action_id='not_suggested',status='completed',result='做过',quote='原话')]),
    MemoryPatch(clarification='请说明',facts=[FactUpdate(key='phone_connection',value='已连接',status='known',quote='原话')]),
    MemoryPatch(facts=[FactUpdate(key='phone_connection',value='已连接',status='known',quote='原话')]*2),
])
def test_spec009_tc08_invalid_patch_atomic(patch: MemoryPatch) -> None:
    s=session();before=s.model_dump_json()
    async def extractor(*args: object) -> MemoryPatch:return patch
    with pytest.raises(ValueError):asyncio.run(process_message(s,'原话',extractor))
    assert s.model_dump_json()==before

class FixturePipeline(MemoryPipeline):
    async def intake(self,description: str,model: str) -> IntakeResult:
        s=session();s.id=description
        return IntakeResult(intent=IntentResult(name='wifi',reason='连接'),reply='',session=s)
    @staticmethod
    async def extractor(s: Session,text: str,q: Question | None,action: str | None) -> MemoryPatch:
        if text=='timeout':raise TimeoutError('PRIVATE_TOKEN')
        return fact(question_key(q) if q else 'phone_connection','未连接' if text=='没有' else text,text)

def test_spec009_tc08_tc09_api_failure_conflict_persistence_isolation(tmp_path: Path) -> None:
    db=tmp_path/'db';app=create_app(db,pipeline=FixturePipeline())
    client=TestClient(app,headers={'X-Support-Client':'local-web'})
    for name in ['one','two']:
        assert client.post('/api/intake',json={'description':name,'confirmed':True,'model':MODEL}).status_code==200
    initial=client.get('/api/sessions/one').json()
    failure=client.post('/api/sessions/one/message',json={'version':0,'text':'timeout'})
    assert failure.status_code==502 and 'PRIVATE_TOKEN' not in failure.text
    assert client.get('/api/sessions/one').json()==initial
    answer={'version':0,'key':'phone_connection','answer':'没有'}
    done=client.post('/api/sessions/one/answer',json=answer)
    assert done.status_code==200 and done.json()['question']['key']=='app_state'
    assert client.post('/api/sessions/one/answer',json=answer).status_code==409
    loaded=Store(db).get('one')
    assert loaded.memory.facts['phone_connection'].value=='未连接'
    assert 'phone_connection' not in Store(db).get('two').memory.facts
    exported=client.get('/api/sessions/one/export').text
    assert '完整会话原话' in exported and '未连接' in exported and '来源轮次' in exported
    assert client.post('/api/sessions/one/message',json={'version':1,'text':'做了','action_id':'unknown'}).status_code==422

def test_spec009_tc07_clarification_suspends_old_advice() -> None:
    s=session();advice(s,['wifi_app'])
    apply_patch(s,'看错了',MemoryPatch(clarification='请说明哪项结果看错了？'))
    assert s.advice is None and s.memory.advice_history and s.memory.clarification

def test_spec009_tc07_retraction_only_invalidates_dependent_facts() -> None:
    s=session();s.issue='recording';advice(s,['recording_files'])
    apply_patch(s,'检查后发现录像有，屏保一分钟',MemoryPatch(facts=[
        FactUpdate(key='recording_files',value='所查时段有录像',status='known',quote='录像有',depends_on_action='recording_files'),
        FactUpdate(key='screen_saver_setting',value='一分钟',status='known',quote='屏保一分钟')],
        actions=[ActionUpdate(action_id='recording_files',status='completed',result='检查到录像',quote='检查后发现录像有')]))
    apply_patch(s,'没有检查录像，刚才说错了',MemoryPatch(actions=[ActionUpdate(action_id='recording_files',status='retracted',result='未确认检查',quote='没有检查录像')]))
    assert s.memory.facts['recording_files'].status=='retracted'
    assert s.memory.facts['screen_saver_setting'].value=='一分钟'
    assert s.memory.facts['problem'].status=='known'

def test_spec009_tc10_dynamic_question_export(tmp_path: Path) -> None:
    app=create_app(tmp_path/'db',pipeline=FixturePipeline())
    s=session();s.questions=[Question(key='screen_saver_timeout_configured',text='设置过屏保等待时间吗？',reason='屏保',options=['不知道'])]
    apply_patch(s,'一分钟',fact('screen_saver_setting','一分钟','一分钟'))
    app.state.store.create(s)
    exported=TestClient(app).get(f'/api/sessions/{s.id}/export')
    assert exported.status_code==200 and '设置过屏保等待时间吗' in exported.text

def test_spec009_tc04_retrieval_uses_effective_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from support_demo import memory_pipeline
    from support_demo.retrieval_types import Expansion
    seen: list[str]=[]
    async def expand(text: str) -> Expansion:
        seen.append(text);return Expansion(chinese='当前已连接无画面',english='Connected with no preview')
    async def embed(texts: list[str]) -> np.ndarray:
        seen.extend(texts);return np.asarray([[1,0,0]]*len(texts),dtype=np.float32)
    monkeypatch.setattr(memory_pipeline,'embed',embed)
    s=session();apply_patch(s,'现在已连接',fact('phone_connection','现在已连接','现在已连接'))
    trace=asyncio.run(MemoryPipeline(index=make_index(),expander=expand).retrieve(s,IntentResult(name='wifi',reason='连接')))
    assert all('旧原话' not in text and '手机没有连接' not in text for text in seen)
    assert '现在已连接' in trace.original and trace.memory_revision==s.memory.revision

def test_spec009_tc04_advice_prompt_omits_superseded_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_core.runnables import RunnableLambda
    from support_demo import ai
    seen: list[str]=[]
    def response(prompt):
        seen.extend(str(message.content) for message in prompt.to_messages())
        return Selection(summary='当前已连接但无画面',step_ids=['wifi_app'],handoff='未解决请转人工').model_dump_json()
    monkeypatch.setattr(ai,'client',lambda model:RunnableLambda(response))
    s=session();apply_patch(s,'现在已连接',fact('phone_connection','现在已连接','现在已连接'))
    doc=make_index().by_id['p10-2']
    asyncio.run(ai.advise(s,[{'id':'p10-2','page':10,'text':doc.text}],['wifi_app']))
    assert '现在已连接' in '\n'.join(seen)
    assert '旧原话' not in '\n'.join(seen) and '手机没有连接' not in '\n'.join(seen)

def test_spec009_tc07_api_no_remaining_actions_handoff(tmp_path: Path,monkeypatch: pytest.MonkeyPatch) -> None:
    index=make_index();trace=trace_for(index);workflow=MemoryPipeline(index=index)
    async def retrieve(*args: object):return trace
    monkeypatch.setattr(workflow,'retrieve',retrieve)
    monkeypatch.setattr(workflow,'grounded_context',lambda *args:([],['wifi_app']))
    app=create_app(tmp_path/'db',pipeline=workflow)
    s=session();s.questions=[];s.retrieval_history=[trace];advice(s,['wifi_app'])
    apply_patch(s,'操作无法执行',MemoryPatch(actions=[ActionUpdate(action_id='wifi_app',status='unable',result='无法执行',quote='无法执行')]))
    app.state.store.create(s)
    client=TestClient(app,headers={'X-Support-Client':'local-web'})
    result=client.post(f'/api/sessions/{s.id}/advise',json={'version':0})
    assert result.status_code==200 and result.json()['session']['status']=='handoff'
    assert result.json()['session']['advice'] is None

def test_spec009_tc01_distinguish_software_and_historical_power() -> None:
    q=Question(key='app_state',text='电脑VIOFO Player打开了吗？',reason='电脑播放器',options=['有'])
    assert question_key(q)=='pc_player_state'
    q=Question(key='camera_power_state',text='插入卡前是否已关机？',reason='操作前提',options=['是'])
    assert question_key(q)=='card_handling_power_off'

def test_spec009_tc01_app_symptom_does_not_answer_setup_question() -> None:
    s=session()
    q=Question(key='app_state',text='是否点击Connect New Device并选择型号？',reason='连接前提',options=['是','没有'])
    s.questions=[q]
    apply_patch(s,'App没有画面',fact('app_state','没有画面','App没有画面'))
    assert question_key(q)=='app_setup_completed'
    assert next_question(s)==q and 'app_state' not in s.answers
    apply_patch(s,'没有',fact('app_setup_completed','没有完成连接步骤','没有'),question=q)
    assert next_question(s) is None
    assert s.memory.facts['app_state'].value=='没有画面'
    assert s.answers['app_state']=='没有完成连接步骤'
