from backend.domain import validate_reviews
import asyncio
import csv
import io
import pytest
from fastapi.testclient import TestClient
from backend.domain import Insight, check_limits, build_report
from backend.service import Service, Store
from backend.export import export_csv, export_html
from backend.app import create_app

def review(identity="R1", body="Good camera", **changes):
    return dict(review_id=identity, body=body, rating=5, expanded=True, **changes)

def insight(ids=("R1",), quote="Good"):
    return Insight.model_validate({"summary":"样本观察", "priorities":[{"title":"验证画质", "reason":"保留核心优势", "topic_names":["画质"], "next_step":"复核样张", "owner":"产品", "timing":"第一阶段", "success_criteria":"形成日夜对照记录"}], "topics":[{"name":"画质", "sentiment":"positive", "observation":"画质好", "suggestion":"核查画质体验", "hypothesis":"", "evidence":[{"review_id":identity,"quote":quote} for identity in ids]}]})

def snapshot(reviews, complete=True):
    return {"input_url":"https://www.amazon.com/dp/B0CC582PX1","url":"https://www.amazon.com/dp/B0CC582PX1","site":"amazon.com","asin":"B0CC582PX1","title":"Camera","variant":None,"captured_at":"2026-10-22T00:00:00Z","complete":complete,"error":None,"reviews":reviews}

def test_tc08_duplicate_identity():
    review = {"review_id": "R1", "body": "Good camera", "rating": 5, "expanded": True}
    result = validate_reviews([review, review.copy()])
    assert len(result["valid"]) == 1
    assert result["duplicates"] == 1

def test_spec004_tc01_required_actions_and_valid_topics() -> None:
    from pydantic import ValidationError
    value = insight().model_dump()
    del value['priorities']
    with pytest.raises(ValidationError):
        Insight.model_validate(value)
    result = insight()
    result.priorities[0].topic_names = ['不存在的主题']
    with pytest.raises(ValueError, match='action_topic_mismatch'):
        build_report(result, [review()])
    result = insight()
    result.priorities.append(result.priorities[0].model_copy(update={'title': '后续验证'}))
    assert [a['title'] for a in build_report(result, [review()])['priorities']] == ['验证画质', '后续验证']

def test_spec004_tc02_html_actions_and_history(tmp_path) -> None:
    service = Service(Store(tmp_path/'db'), None)
    batch = service.create(snapshot([review()]))
    value = insight()
    value.priorities[0].next_step = '<script>后续执行</script>'
    batch['report'] = build_report(value, batch['validation']['valid'])
    html = export_html(batch)
    assert html.index('整体结论') < html.index('优先建议与后续行动') < html.index('<h2>主题与原文证据')
    assert '&lt;script&gt;后续执行&lt;/script&gt;' in html and '<script>' not in html
    assert '形成日夜对照记录' in html
    del batch['report']['priorities']
    assert '历史报告尚未生成行动建议' in export_html(batch)

def test_tc10_missing_identity():
    result = validate_reviews([{"review_id": None, "body": "Good camera", "rating": 5, "expanded": True}])
    assert result["valid"] == []
    assert result["excluded"][0]["reason"] == "identity_missing"

def test_tc09_tc11_counts_and_identical_text():
    rows = [review(),review(),review("R2"),review("R3"),review(None)]
    result=validate_reviews(rows)
    assert (result["found"],len(result["valid"]),result["duplicates"],len(result["excluded"]))==(5,3,1,1)

@pytest.mark.parametrize("changes,reason",[({"body":" "},"body_empty"),({"expanded":False},"expansion_failed"),({"rating":0},"rating_invalid"),({"rating":True},"rating_invalid"),({"rating":4.5},"rating_invalid")])
def test_tc10_invalid_fields(changes,reason):
    row=review();row.update(changes)
    assert validate_reviews([row])["excluded"][0]["reason"]==reason

def test_tc07_optional_missing():
    assert len(validate_reviews([review()])["valid"])==1

@pytest.mark.parametrize("ids,quote",[(('UNKNOWN',),'Good'),(('R1',),'不是原文'),(('R1',),' ')])
def test_tc14_invalid_evidence(ids,quote):
    with pytest.raises(ValueError): build_report(insight(ids,quote),[review()])

def test_tc15_program_counts():
    result=insight(('R1','R1','R2'))
    second=result.topics[0].model_copy(deep=True);second.name='安装';second.evidence=insight(('R2','R3','R4')).topics[0].evidence
    result.topics.append(second)
    report=build_report(result,[review(f'R{i}') for i in range(1,5)])
    assert [(t['count'],t['percent']) for t in report['topics']]==[(2,50),(3,75)]

def test_spec003_tc02_repeated_word_in_quote_is_rejected() -> None:
    """真实德国站回归：模型重复zu不能放宽为有效引用。"""
    original = 'deswegen ist der BT Knopf eine zu empfehlende Möglichkeit'
    altered = 'deswegen ist der BT Knopf eine zu zu empfehlende Möglichkeit'
    with pytest.raises(ValueError, match='evidence_mismatch'):
        build_report(insight(quote=altered), [review(body=original)])
    assert build_report(insight(quote=original), [review(body=original)])['sample_size'] == 1

def test_tc19_limits():
    check_limits([review(str(i)) for i in range(100)])
    check_limits([review(body='x'*100000)])
    with pytest.raises(ValueError):check_limits([review(str(i)) for i in range(101)])
    with pytest.raises(ValueError):check_limits([review(body='x'*100001)])

def test_tc16_tc17_failure_retry_snapshot(tmp_path):
    calls=[]
    async def analyzer(rows):
        calls.append(rows)
        if len(calls)==1:raise TimeoutError('SECRET_API_KEY')
        return insight()
    service=Service(Store(tmp_path/'db'),analyzer)
    original=snapshot([review()]);batch=service.create(original)
    original['reviews'].append(review('NEW'))
    result=asyncio.run(service.run(batch['id']))
    assert result['status']=='analysis_failed' and 'SECRET' not in result['error']
    result=asyncio.run(service.run(batch['id']))
    assert result['status']=='completed' and len(calls[1])==1

def test_tc05_tc06_no_analysis(tmp_path):
    async def forbidden(rows):raise AssertionError('不应调用模型')
    service=Service(Store(tmp_path/'db'),forbidden)
    assert service.create(snapshot([],True))['status']=='no_valid_reviews'
    batch=service.create(snapshot([review()],False))
    assert batch['status']=='collection_failed'
    with pytest.raises(ValueError):asyncio.run(service.run(batch['id']))

def test_tc18_batch_history(tmp_path):
    async def analyzer(rows):return insight()
    service=Service(Store(tmp_path/'db'),analyzer)
    first=service.create(snapshot([review()]));second=service.create(snapshot([review()]))
    assert first['id']!=second['id'] and len(service.store.list())==2

def test_tc20_tc22_exports(tmp_path):
    service=Service(Store(tmp_path/'db'),None)
    batch=service.create(snapshot([review(body='=SUM(1,2)<script>中文ä</script>')]))
    batch['report']=build_report(insight(quote='<script>中文ä</script>'),batch['validation']['valid'])
    output=export_csv(batch)
    assert output.startswith('\ufeff')
    rows=list(csv.DictReader(io.StringIO(output.lstrip('\ufeff'))))
    assert rows[0]['body'].startswith("'=")
    html=export_html(batch)
    assert '<script>' not in html and '&lt;script&gt;中文ä' in html
    assert batch['snapshot']['url'] in html

def test_tc21_api_auth_and_source(tmp_path):
    app=create_app(Store(tmp_path/'db'),'test-secret');client=TestClient(app)
    assert client.get('/health').status_code==401
    headers={'x-demo-token':'test-secret'}
    assert client.get('/health',headers=headers).status_code==200
    assert client.get('/health',headers={**headers,'origin':'https://www.amazon.com'}).status_code==401
    invalid=snapshot([]);invalid['url']='https://evil.com/dp/B0CC582PX1'
    assert client.post('/batches',json=invalid,headers=headers).status_code==422

def test_tc05_incomplete_keeps_data(tmp_path):
    service=Service(Store(tmp_path/'db'),None)
    batch=service.create(snapshot([review()],False))
    assert len(service.store.get(batch['id'])['validation']['valid'])==1

def test_tc18_concurrent_model_requests_rejected(tmp_path):
    async def scenario():
        entered=asyncio.Event();release=asyncio.Event()
        async def analyzer(rows):
            entered.set();await release.wait();return insight()
        service=Service(Store(tmp_path/'db'),analyzer)
        batch=service.create(snapshot([review()]))
        running=asyncio.create_task(service.run(batch['id']));await entered.wait()
        with pytest.raises(ValueError,match='task_running'):await service.run(batch['id'])
        release.set();await running
    asyncio.run(scenario())

def test_spec003_tc01_tc05_tc21_model_input_and_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.analysis as module
    from langchain_core.runnables import RunnableLambda
    from langchain_core.prompt_values import PromptValue
    captured: dict[str, object] = {}
    def model_factory(**kwargs: object) -> RunnableLambda:
        captured.update(kwargs)
        def invoke(prompt: PromptValue) -> str:
            captured['messages']=prompt.to_string()
            return insight().model_dump_json()
        return RunnableLambda(invoke)
    monkeypatch.setenv('AGI_KEY','TEST_ONLY_SECRET')
    monkeypatch.setattr(module,'ChatOpenAI',model_factory)
    assert asyncio.run(module.analyze([review()])).summary=='样本观察'
    assert captured['model'] == 'deepseek-flash'
    assert captured['base_url'] == 'https://api.agicto.cn/v1/'
    assert captured['api_key'] == 'TEST_ONLY_SECRET'
    assert captured['http_socket_options']==() and captured['max_retries']==0
    assert captured['use_responses_api'] is False
    assert 'TEST_ONLY_SECRET' not in captured['messages']
    assert 'Good camera' in captured['messages']
