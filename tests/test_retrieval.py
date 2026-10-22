"""SPEC-007：真实算法配合隔离模型桩，覆盖检索与工作流边界。"""
import asyncio
import json
from pathlib import Path

import faiss
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from support_demo.app import create_app
from support_demo.build_knowledge import validate_generated
from support_demo import build_knowledge, pipeline as pipeline_module, retrieval
from support_demo.catalog import CATALOG
from support_demo.domain import ROOT, MODEL, Question, Session
from support_demo.pipeline import IntakeResult, PROGRAM_REPLIES, SupportPipeline, conversation_query, factual_query
from support_demo.retrieval import HybridIndex, normalize, reciprocal_rank_fusion, sha256, tokenize
from support_demo.retrieval_types import (Expansion, FollowupSeed, GeneratedQuestions, IntentResult,
    KnowledgeDocument, KnowledgeManifest, RetrievalTrace, SearchQuestion)

HEADERS = {'X-Support-Client':'local-web'}

def make_index() -> HybridIndex:
    texts = {item['id']:item for item in json.loads((ROOT/'manuals/chunks.json').read_text(encoding='utf-8'))}
    docs = [KnowledgeDocument(chunk_id=key, page=texts[key]['page'], text=texts[key]['text'], model=MODEL, version='V25.12.18') for key in ['p10-2','p14-2','p5-2','p7-1','p16-2']]
    docs[0].search_questions = [SearchQuestion(chinese='手机连接不上怎么办？', english='Phone cannot connect?', quote='Step 1: Use the phone to connect A119 Mini 2 Wi-Fi.')]
    docs[0].followups = [FollowupSeed(intent='wifi', field='phone_connection', text='手机已连接相机 Wi-Fi 吗？', reason='区分手机连接阶段', options=['是','否','不确定'], quote='Step 1: Use the phone to connect A119 Mini 2 Wi-Fi.')]
    vectors = normalize(np.asarray([[1,0,0],[0.9,0.1,0],[0,1,0],[0,0,1],[-1,0,0]], dtype=np.float32))
    index = faiss.IndexFlatIP(3);index.add(vectors)
    manifest = KnowledgeManifest(index_id='test-fixture', model=MODEL, version='V25.12.18', embedding_model='test-vectors', dimensions=3,
        document_count=5, search_question_count=1, followup_count=1, source_sha256='',chunks_sha256='',documents_sha256='',index_sha256='',generator_model='test',generator_version='test')
    return HybridIndex(docs,index,manifest)

def trace_for(index: HybridIndex, query: str = 'Wi-Fi connect phone') -> RetrievalTrace:
    return index.search(query, Expansion(chinese='手机连接相机网络', english='connect phone Wi-Fi'),
        np.asarray([[1,0,0]]*3, dtype=np.float32), MODEL, IntentResult(name='wifi',reason='连接问题'))

def test_spec007_tc01_generated_evidence_rejects_invention() -> None:
    chunk={'id':'p1-1','page':1,'text':'The camera supports Wi-Fi connections.'}
    valid=GeneratedQuestions(search_questions=[SearchQuestion(chinese='支持Wi-Fi吗？',english='Does it support Wi-Fi?',quote=chunk['text'])])
    validate_generated(valid,chunk)
    valid.search_questions[0].quote='This sentence does not exist.'
    with pytest.raises(ValueError,match='evidence_mismatch'):
        validate_generated(valid,chunk)

def test_spec007_tc01_failed_build_does_not_publish(tmp_path: Path,monkeypatch: pytest.MonkeyPatch) -> None:
    root=tmp_path/'support';manual=root/'manuals';manual.mkdir(parents=True)
    (manual/'a119-mini2.pdf').write_bytes(b'test-fixture')
    (manual/'source.json').write_text(json.dumps({'sha256':sha256(b'test-fixture'),'model':MODEL,'version':'test'}))
    (manual/'chunks.json').write_text(json.dumps([{'id':'p1-1','page':1,'text':'test source'}]))
    kb=tmp_path/'knowledge';kb.mkdir();(kb/'current.txt').write_text('previous-index')
    async def fail(chunk: dict) -> GeneratedQuestions:
        raise RuntimeError('model unavailable')
    monkeypatch.setattr(build_knowledge,'ROOT',root);monkeypatch.setattr(build_knowledge,'KB_ROOT',kb)
    monkeypatch.setattr(build_knowledge,'structured_chunks',lambda root:[{'id':'p1-1-s01','page':1,'text':'test source','source_id':'p1-1','chapter_path':['test'],'start':0,'end':11}])
    monkeypatch.setattr(build_knowledge,'generate_questions',fail)
    with pytest.raises(RuntimeError):asyncio.run(build_knowledge.build())
    assert (kb/'current.txt').read_text()=='previous-index'
    assert not list(kb.glob('*/manifest.json'))

def test_spec007_tc02_bm25_and_vector_independent() -> None:
    index=make_index()
    trace=index.search('microSD 512GB',Expansion(chinese='存储卡容量',english='microSD card capacity 512GB'),
        np.asarray([[0,0,1]]*3,dtype=np.float32),MODEL,IntentResult(name='recording',reason='存储卡'))
    assert trace.bm25[0].chunk_id=='p5-2'
    assert trace.vector[0].chunk_id=='p7-1'
    assert trace.vector[0].vector_score==pytest.approx(1.0)
    assert '屏幕' in tokenize('屏幕变黑 Wi-Fi E04') and 'e04' in tokenize('E04')
    assert len({hit.chunk_id for hit in trace.fused})==len(trace.fused)
    assert {'p5-2','p7-1'}.issubset({hit.chunk_id for hit in trace.fused})
    assert 'a119' not in tokenize('A119 MINI 2 screen E04')
    assert 'e04' in tokenize('A119 MINI 2 screen E04')
    assert '我' not in tokenize('我不知道屏幕为什么黑了')

def test_spec007_tc02_scope_dimension_and_zero_vector() -> None:
    index=make_index();expansion=Expansion(chinese='连接',english='connect');intent=IntentResult(name='wifi',reason='连接')
    with pytest.raises(ValueError,match='model_scope'):
        index.search('wifi',expansion,np.ones((3,3),dtype=np.float32),'OTHER MODEL',intent)
    with pytest.raises(ValueError,match='dimensions'):
        index.search('wifi',expansion,np.ones((3,4),dtype=np.float32),MODEL,intent)
    with pytest.raises(ValueError,match='invalid_vectors'):
        normalize(np.zeros((2,3),dtype=np.float32))

def test_spec007_tc02_changed_embedding_config_rejected(tmp_path: Path) -> None:
    manifest=make_index().manifest
    (tmp_path/'test-fixture').mkdir();(tmp_path/'current.txt').write_text('test-fixture')
    (tmp_path/'test-fixture/manifest.json').write_text(manifest.model_dump_json())
    with pytest.raises(ValueError,match='configuration_changed'):
        HybridIndex.load(tmp_path)

def test_spec007_tc03_rrf_dedup_and_single_lane() -> None:
    result=reciprocal_rank_fusion([[1,1,2],[2,3]])
    assert result[1]==pytest.approx(1/61)
    assert result[2]==pytest.approx(1/62+1/61)
    assert result[3]==pytest.approx(1/62)
    assert reciprocal_rank_fusion([[],[4]])=={4:pytest.approx(1/61)}

def test_spec007_tc04_original_and_answers_pass_to_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str]=[]
    async def expander(query: str) -> Expansion:
        seen.append(query);return Expansion(chinese='没有确定是否录像',english='Recording status is unknown')
    async def embedder(texts: list[str]) -> np.ndarray:
        seen.extend(texts);return np.asarray([[1,0,0]]*len(texts),dtype=np.float32)
    monkeypatch.setattr(pipeline_module,'embed',embedder)
    session=Session(id='test',model=MODEL,issue='recording',description='黑屏，不确定是不是停录',workflow_version=2,
        questions=[Question(key='card_specs',text='卡的容量？',reason='核对规格',options=['不确定'])],answers={'card_specs':'不知道，不是已确认损坏'})
    workflow=SupportPipeline(index=make_index(),expander=expander)
    trace=asyncio.run(workflow.retrieve(session,IntentResult(name='recording',reason='录像')))
    assert trace.original==conversation_query(session)
    assert '卡的容量？：不知道，不是已确认损坏' in seen[0]
    assert seen[1]==session.description and seen[2]==seen[0]
    assert trace.queries[0]==session.description and len(trace.queries)==4
    assert trace.expansion.english=='Recording status is unknown'

def test_spec007_tc04_unknown_answers_do_not_introduce_search_topics(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    async def expander(query: str) -> Expansion:
        seen.append(query)
        return Expansion(chinese='屏幕自动熄灭，录像状态未知',english='Screen turns off, recording status unknown')
    async def embedder(texts: list[str]) -> np.ndarray:
        seen.extend(texts)
        return np.asarray([[1,0,0]]*len(texts),dtype=np.float32)
    monkeypatch.setattr(pipeline_module,'embed',embedder)
    session=Session(id='test',model=MODEL,issue='recording',description='屏幕黑了，不知道是否停录',workflow_version=2,
        questions=[Question(key='motion',text='移动侦测是否开启？',reason='候选',options=['不确定'])],answers={'motion':'不确定'})
    trace=asyncio.run(SupportPipeline(index=make_index(),expander=expander).retrieve(session,IntentResult(name='recording',reason='黑屏')))
    assert '移动侦测' in trace.original
    assert all('移动侦测' not in text for text in seen)
    assert session.answers=={'motion':'不确定'}
    session.answers['motion']='没有开启，不是不确定'
    assert '没有开启，不是不确定' in factual_query(session)

@pytest.mark.parametrize('intent',['unrelated','other_support','unclear'])
def test_spec007_tc05_program_reply_without_retrieval(intent: str) -> None:
    async def classifier(text: str) -> IntentResult:return IntentResult(name=intent,reason='测试判定')
    class NoRetrieval(SupportPipeline):
        async def retrieve(self,*args: object) -> RetrievalTrace:raise AssertionError('不应检索')
    result=asyncio.run(NoRetrieval(classifier=classifier).intake('用户输入',MODEL))
    assert result.session is None and result.reply==PROGRAM_REPLIES[intent]

def test_spec007_tc06_questions_from_hits_and_dedup() -> None:
    index=make_index();trace=trace_for(index)
    index.docs[1].followups=index.docs[0].followups.copy()
    candidates=SupportPipeline(index=index).question_candidates(trace)
    assert len(candidates)==1
    assert candidates[0].chunk_id in {hit.chunk_id for hit in trace.fused}
    assert candidates[0].key=='phone_connection'

def test_spec007_tc06_cross_intent_screen_question_available() -> None:
    index=make_index();trace=trace_for(index)
    index.docs[0].followups[0].intent='power'
    trace.intent=IntentResult(name='recording',reason='熄屏但录像状态未知')
    candidates=SupportPipeline(index=index).question_candidates(trace)
    assert len(candidates)==1
    assert candidates[0].chunk_id in {hit.chunk_id for hit in trace.fused}

def test_spec007_tc06_grounded_actions_and_linked_sources() -> None:
    index=make_index();workflow=SupportPipeline(index=index);trace=trace_for(index)
    trace.fused=[hit for hit in trace.fused if hit.chunk_id=='p10-2']
    session=Session(id='test',model=MODEL,issue='wifi',description='连接失败')
    chunks,allowed=workflow.grounded_context(session,trace)
    assert allowed and all(any(e.chunk_id=='p10-2' for e in CATALOG['wifi'][key].evidence) for key in allowed)
    assert {chunk['id'] for chunk in chunks}=={'p10-2'}
    trace.fused=[]
    with pytest.raises(ValueError,match='no_grounded_actions'):workflow.grounded_context(session,trace)

def test_spec007_tc07_tc08_new_session_failure_and_export(tmp_path: Path) -> None:
    index=make_index()
    class FixtureWorkflow(SupportPipeline):
        async def intake(self,description: str,model: str) -> IntakeResult:
            trace=trace_for(index)
            session=Session(id='fixture-session',model=model,issue='wifi',description=description,workflow_version=2,
                questions=self.question_candidates(trace),retrieval_history=[trace])
            return IntakeResult(intent=trace.intent,reply='',session=session)
        async def advise(self,session: Session) -> tuple:
            raise RuntimeError('PRIVATE_TOKEN_MUST_NOT_ESCAPE')
    client=TestClient(create_app(tmp_path/'db',pipeline=FixtureWorkflow(index=index)),headers=HEADERS)
    response=client.post('/api/intake',json={'model':MODEL,'confirmed':True,'description':'手机连接问题'})
    assert response.status_code==200
    view=response.json()['view'];identity=view['session']['id']
    answered=client.post(f'/api/sessions/{identity}/answer',json={'version':0,'key':view['question']['key'],'answer':'已连接'}).json()
    assert answered['question'] is None
    failure=client.post(f'/api/sessions/{identity}/advise',json={'version':1})
    assert failure.status_code==502 and 'PRIVATE_TOKEN' not in failure.text
    assert client.get(f'/api/sessions/{identity}').json()==answered
    exported=client.get(f'/api/sessions/{identity}/export').text
    assert '中文扩写' in exported and 'FAISS' in exported and 'RRF' in exported and '手机已连接' in exported
    assert client.get('/api/knowledge').json()['manifest']['document_count']==5

def test_spec007_tc05_api_program_response(tmp_path: Path) -> None:
    async def classifier(text: str) -> IntentResult:return IntentResult(name='unrelated',reason='非售后话题')
    client=TestClient(create_app(tmp_path/'db',pipeline=SupportPipeline(classifier=classifier)),headers=HEADERS)
    result=client.post('/api/intake',json={'model':MODEL,'confirmed':True,'description':'写一首诗'}).json()
    assert result['view'] is None and result['reply']==PROGRAM_REPLIES['unrelated']

def test_spec007_tc02_embedding_response_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self,**kwargs: object) -> None:pass
        async def __aenter__(self) -> 'FakeClient':return self
        async def __aexit__(self,*args: object) -> None:pass
        async def post(self,*args: object,**kwargs: object) -> httpx.Response:
            return httpx.Response(200,json={'data':[{'index':0,'embedding':[1.0,2.0]}]},request=httpx.Request('POST','https://test'))
    monkeypatch.setenv('AGI_KEY','test-placeholder')
    monkeypatch.setattr(retrieval.httpx,'AsyncClient',FakeClient)
    with pytest.raises(ValueError,match='embedding_dimensions'):
        asyncio.run(retrieval.embed(['text']))
