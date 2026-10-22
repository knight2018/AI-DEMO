"""SPEC009-TC11：真实模型多轮语义验收，使用独立测试会话。"""
import asyncio
import json
from collections.abc import Awaitable, Callable
from support_demo.catalog import Selection, build_advice
from support_demo.domain import ROOT, MODEL, Question, Session, next_question
from support_demo.memory import apply_patch, current_context, process_message, remember_advice
from support_demo.memory_types import FactUpdate, MemoryPatch, SessionMemory

def fresh(identity: str, issue: str = 'wifi') -> Session:
    return Session(id=identity,model=MODEL,issue=issue,description='独立记忆验收',workflow_version=3,memory=SessionMemory(),questions=[
        Question(key='phone_connection',text='手机已连接到相机的Wi-Fi了吗？',reason='确认连接',options=['已连接','没有','不知道']),
        Question(key='app_state',text='手机VIOFO App能看到实时画面吗？',reason='确认画面',options=['有','没有','不知道'])] if issue=='wifi' else [])

async def duplicate() -> Session:
    s=await process_message(fresh('qa-duplicate'),'手机已经连上记录仪热点，但手机VIOFO App一直没有实时画面。')
    assert s.memory.facts['phone_connection'].status=='known'
    assert next_question(s) is None
    s=await process_message(s,'刚才说错了，手机其实没有连接相机的热点。')
    assert any(word in s.memory.facts['phone_connection'].value for word in ['未','没','不'])
    assert s.memory.facts['phone_connection'].supersedes is not None
    return s

async def short_answers() -> Session:
    s=fresh('qa-short')
    apply_patch(s,'App没有画面',MemoryPatch(facts=[FactUpdate(key='problem',value='App没有画面',status='known',quote='App没有画面')]))
    s=await process_message(s,'没有',question=next_question(s))
    assert s.memory.facts['phone_connection'].status=='known'
    assert any(word in s.memory.facts['phone_connection'].value for word in ['未','没','不'])
    s=await process_message(s,'不知道',question=next_question(s))
    assert s.memory.facts['app_state'].status=='unknown' and next_question(s) is None
    return s

async def correction() -> Session:
    s=await process_message(fresh('qa-correction','recording'),'行驶一会儿屏幕黑了。我在电脑上看过，本次黑屏后那几分钟的视频文件是有的。')
    assert s.memory.facts['recording_files'].status=='known'
    s.advice=build_advice(Selection(summary='查看屏保设置',step_ids=['screen_saver'],handoff='未解决转人工'),'recording')
    s.status='advised';remember_advice(s)
    s=await process_message(s,'刚才看错了，那是昨天的视频，不是今天这次黑屏之后的视频。今天那几分钟有没有录像我不知道。')
    assert s.memory.facts['recording_files'].status in {'retracted','unknown'}
    assert s.memory.facts.get('recording_state') is None or s.memory.facts['recording_state'].status in {'retracted','unknown'}
    assert s.advice is None and len(s.memory.advice_history)==1
    assert '有的' not in current_context(s,for_search=True)
    return s

async def action_results() -> Session:
    s=await process_message(fresh('qa-actions'),'手机连上了相机热点，App没有画面。')
    s.advice=build_advice(Selection(summary='核对App和灯',step_ids=['wifi_app','wifi_led'],handoff='未解决转人工'),'wifi')
    s.status='advised';remember_advice(s)
    s=await process_message(s,'没用')
    assert s.memory.clarification and s.memory.actions['wifi_app'].status=='suggested'
    s=await process_message(s,'做了，还是没用，App仍然没有画面。',action_id='wifi_app')
    assert s.memory.actions['wifi_app'].status=='completed' and not s.memory.clarification
    assert s.memory.actions['wifi_led'].status=='suggested' and s.status!='resolved'
    s=await process_message(s,'刚才说错了，我还没有执行这个操作。',action_id='wifi_app')
    assert s.memory.actions['wifi_app'].status=='retracted'
    assert s.memory.facts['problem'].status=='known'
    assert s.memory.facts['app_state'].status=='known'
    return s

async def main() -> None:
    root=ROOT/'data/qa/spec009';root.mkdir(parents=True,exist_ok=True)
    semaphore=asyncio.Semaphore(2)
    async def one(name: str, run: Callable[[],Awaitable[Session]]) -> bool:
        async with semaphore:
            try:
                s=await run();record={'case':name,'passed':True,'session':s.model_dump()}
            except Exception as error:
                record={'case':name,'passed':False,'error_type':type(error).__name__}
            (root/f'{name}.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({k:v for k,v in record.items() if k!='session'},ensure_ascii=False),flush=True)
            return bool(record['passed'])
    results=await asyncio.gather(one('duplicate',duplicate),one('short_answers',short_answers),one('correction',correction),one('action_results',action_results))
    if not all(results):raise RuntimeError('memory_live_acceptance_failed')

if __name__=='__main__':asyncio.run(main())
