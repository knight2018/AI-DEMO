"""SPEC-008：真实 PDF 结构、原文坐标、引用边界及多问法索引回归。"""
import json

import faiss
import numpy as np
import pytest

from support_demo.chunking import paragraph_ranges, structured_chunks
from support_demo.domain import ROOT, MODEL, Session, validate_advice
from support_demo.catalog import Selection, build_advice
from support_demo.pipeline import SupportPipeline
from support_demo.retrieval import HybridIndex, index_passages, normalize
from support_demo.retrieval_types import Expansion, IntentResult, KnowledgeDocument, KnowledgeManifest, SearchQuestion

def documents() -> list[KnowledgeDocument]:
    return [KnowledgeDocument(chunk_id=c['id'],page=c['page'],text=c['text'],source_id=c['source_id'],
        chapter_path=c['chapter_path'],start=c['start'],end=c['end'],model=MODEL,version='test')
        for c in structured_chunks(ROOT/'manuals')]

def fixture_index(docs: list[KnowledgeDocument]) -> HybridIndex:
    passages, parents = index_passages(docs)
    vectors = normalize(np.asarray([[1, (i+1)/100] for i in range(len(passages))],dtype=np.float32))
    index = faiss.IndexFlatIP(2)
    index.add(vectors)
    manifest = KnowledgeManifest(index_id='test',model=MODEL,version='test',embedding_model='fixture',dimensions=2,
        document_count=len(docs),search_question_count=0,followup_count=0,source_sha256='',chunks_sha256='',
        documents_sha256='',index_sha256='',generator_model='fixture',generator_version='test',
        index_format='sections_v3',passages=passages,passage_parents=parents)
    return HybridIndex(docs,index,manifest)

def test_spec008_tc01_sections_and_offsets() -> None:
    docs = documents()
    parents = {c['id']:c for c in json.loads((ROOT/'manuals/chunks.json').read_text(encoding='utf-8'))}
    for doc in docs:
        assert doc.text == parents[doc.source_id]['text'][doc.start:doc.end]
        assert doc.chapter_path and doc.page == parents[doc.source_id]['page']
    screen = next(doc for doc in docs if doc.chapter_path[-1]=='Screen Saver')
    assert screen.chapter_path == ['System Settings','Screen Saver']
    assert 'Boot Delay' not in screen.text and 'WiFi:' not in screen.text
    parking = next(doc for doc in docs if doc.chapter_path[-1]=='Parking G-sensor')
    assert parking.text.startswith('Parking G-sensor: The G-sensor')
    regular = next(doc for doc in docs if doc.chapter_path[-1]=='G-sensor')
    assert regular.text.startswith('G-sensor: The G-sensor measures')
    install = next(doc for doc in docs if doc.source_id=='p7-1')
    assert 'Install the Dashcam' in install.chapter_path
    assert 'Format the memory card' not in install.chapter_path
    # 表格列名不是小节；按钮表不能在“Recording Mode”等单元格中途断开。
    assert len([doc for doc in docs if doc.source_id=='p4-1'])==1
    assert len([doc for doc in docs if doc.source_id=='p4-2'])==1

def test_spec008_tc02_steps_and_warnings_stay_together() -> None:
    docs = documents()
    connect = next(doc for doc in docs if doc.chapter_path[-1]=='Connect VIOFO APP')
    assert all(f'Step {i}:' in connect.text for i in [1,2,3])
    card = next(doc for doc in docs if doc.source_id=='p5-2' and 'Ensure that the camera is turned off' in doc.text)
    assert 'Please unplug the power cable' in card.text
    text = 'a'*600+' '+'b'*600+' '+'c'*600
    ranges = paragraph_ranges(text,[601,1202],1200)
    assert ranges == [(0,601),(601,1202),(1202,len(text))]
    steps = 'Step 1: '+text+' Note: unplug before removal.'
    assert paragraph_ranges(steps,[601,1202],1200)==[(0,len(steps))]

def test_spec008_tc03_passages_keep_sections_and_deduplicate() -> None:
    docs = [doc for doc in documents() if doc.source_id=='p14-2']
    screen = next(doc for doc in docs if doc.chapter_path[-1]=='Screen Saver')
    screen.search_questions = [SearchQuestion(chinese='屏幕为何熄灭',english='Why does the screen turn off?',quote='Turn the screen OFF after a set time.')]*2
    passages, parents = index_passages(docs)
    assert len(passages)==len(docs)+1
    assert sum(parent==docs.index(screen) for parent in parents)==2
    index=fixture_index(docs)
    trace=index.search('screen saver',Expansion(chinese='屏幕为何熄灭',english='screen saver'),
        np.asarray([[1,0.06]]*2,dtype=np.float32),MODEL,IntentResult(name='recording',reason='黑屏'))
    assert len({hit.chunk_id for hit in trace.fused})==len(trace.fused)
    assert all(hit.source_id=='p14-2' for hit in trace.fused)
    manifest=index.manifest.model_copy(deep=True)
    manifest.passages[0]='tampered content'
    with pytest.raises(ValueError,match='passage_metadata'):
        HybridIndex(docs,index.index,manifest)

def test_spec008_tc04_same_page_other_section_not_evidence() -> None:
    docs=[doc for doc in documents() if doc.source_id=='p14-2']
    index=fixture_index(docs)
    workflow=SupportPipeline(index=index)
    trace=index.search('screen',Expansion(chinese='屏幕',english='screen saver'),np.asarray([[1,0.1]]*3,dtype=np.float32),MODEL,IntentResult(name='recording',reason='黑屏'))
    screen=next(doc for doc in docs if doc.chapter_path[-1]=='Screen Saver')
    hit=trace.fused[0].model_copy(update={'chunk_id':screen.chunk_id,'source_id':screen.source_id,'text':screen.text})
    trace.fused=[hit]
    session=Session(id='fixture',model=MODEL,issue='recording',description='黑屏')
    chunks,allowed=workflow.grounded_context(session,trace)
    assert allowed==['screen_saver']
    validate_advice(build_advice(Selection(summary='待核实',step_ids=allowed,handoff='转人工'),session.issue),chunks)
    wifi=next(doc for doc in docs if doc.chapter_path[-1]=='WiFi')
    trace.fused=[hit.model_copy(update={'chunk_id':wifi.chunk_id,'text':wifi.text})]
    with pytest.raises(ValueError,match='no_grounded_actions'):
        workflow.grounded_context(session,trace)

def test_spec008_tc04_linked_safety_and_multiple_sections() -> None:
    index=fixture_index(documents())
    trace=index.search('Loop Recording',Expansion(chinese='录像文件',english='movie file'),np.asarray([[1,0.1]]*3,dtype=np.float32),MODEL,IntentResult(name='recording',reason='文件'))
    loop=next(doc for doc in index.docs if doc.source_id=='p8-2' and doc.chapter_path[-1]=='Loop Recording')
    trace.fused=[trace.fused[0].model_copy(update={'chunk_id':loop.chunk_id,'source_id':loop.source_id,'text':loop.text,'page':loop.page})]
    session=Session(id='fixture',model=MODEL,issue='recording',description='录像文件')
    chunks,allowed=SupportPipeline(index=index).grounded_context(session,trace)
    assert allowed==['recording_files'] and trace.linked_evidence==['p5-2']
    validate_advice(build_advice(Selection(summary='待核实',step_ids=allowed,handoff='转人工'),session.issue),chunks)
