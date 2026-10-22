"""SPEC-005：确定性追问、来源校验和会话数据结构。"""
import base64
import binascii
import io
import json
import re
from pathlib import Path
from typing import Literal, TypedDict

from PIL import Image
from pydantic import BaseModel, Field
from support_demo.retrieval_types import RetrievalTrace
from support_demo.memory_types import SessionMemory

ROOT = Path(__file__).parent
MODEL = 'A119 MINI 2'
Issue = Literal['wifi', 'recording', 'power']

class Chunk(TypedDict):
    id: str
    page: int
    text: str

class Question(BaseModel):
    key: str
    text: str
    reason: str
    options: list[str]
    chunk_id: str | None = None
    page: int | None = None
    quote: str | None = None

QUESTIONS: dict[str, list[Question]] = {
    'wifi': [
        Question(key='wifi_display', text='相机屏幕是否显示 Wi-Fi 名称和密码？', reason='先确认相机 Wi-Fi 已开启，依据 PDF 第 10 页。', options=['已显示', '未显示', '不确定']),
        Question(key='phone_connection', text='手机现在连接到相机的 Wi-Fi 了吗？', reason='手机连接相机 Wi-Fi 是打开 App 前的必要步骤，依据 PDF 第 10 页。', options=['已连接', '找不到网络', '连接失败', '不确定']),
        Question(key='app_state', text='在 VIOFO App 中进行到哪一步？', reason='区分手机联网阶段与 App 选择设备阶段，依据 PDF 第 10 页。', options=['尚未打开 App', '已选择型号但无画面', '其他情况，请补充'])],
    'recording': [
        Question(key='card', text='存储卡的容量和速度等级是什么？', reason='核对说明书要求的 Class-10 或 UHS-I、最大 512GB，依据 PDF 第 5 页。', options=['不确定，请指导查看卡面', '已确认，请填写容量和等级']),
        Question(key='symptom', text='是屏幕熄灭、明确停止录像，还是提示存储卡错误？', reason='熄屏不代表停止录像，需区分现象，依据 PDF 第 14 页。', options=['屏幕熄灭，录像状态不确定', '明确没有录像文件', '显示存储卡错误', '其他情况，请补充'])],
    'power': [
        Question(key='power_source', text='当前使用哪种供电方式？', reason='说明书介绍配套车充连接 12V/24V 插座，其他布线不能直接套用，依据 PDF 第 7 页。', options=['配套车充接车辆插座', '降压线或其他接线', '不确定']),
        Question(key='power_state', text='车辆已供电时，相机的屏幕或指示灯是什么状态？', reason='区分断电、屏幕休眠和延迟启动，依据 PDF 第 8、14 页。', options=['屏幕和灯均无反应', '灯亮但屏幕不亮', '反复重启', '其他情况，请补充'])]
}
CHUNK_IDS: dict[str, set[str]] = {
    'wifi': {'p10-2', 'p14-2'},
    'recording': {'p5-2', 'p6-1', 'p8-2', 'p14-2'},
    'power': {'p7-1', 'p7-2', 'p8-1', 'p8-2', 'p14-2'}
}

class Evidence(BaseModel):
    chunk_id: str
    page: int
    quote: str = Field(min_length=12, max_length=600)

class Step(BaseModel):
    action: str = Field(min_length=1, max_length=700)
    expected: str = Field(min_length=1, max_length=400)
    evidence: list[Evidence] = Field(min_length=1, max_length=3)

class Advice(BaseModel):
    summary: str = Field(min_length=1, max_length=1000)
    steps: list[Step] = Field(min_length=1, max_length=3)
    handoff: str = Field(min_length=1, max_length=700)

class Identification(BaseModel):
    brand: str = ''
    model: str = ''
    visible_text: str = ''
    readable: bool

class Session(BaseModel):
    id: str
    model: str
    issue: Issue
    description: str
    answers: dict[str, str] = Field(default_factory=dict)
    version: int = 0
    status: Literal['collecting', 'ready', 'advised', 'resolved', 'handoff'] = 'collecting'
    advice: Advice | None = None
    workflow_version: int = 1
    questions: list[Question] = Field(default_factory=list)
    retrieval_history: list[RetrievalTrace] = Field(default_factory=list)
    memory: SessionMemory | None = None

def canonical_model(value: str) -> str | None:
    """只匹配完整型号，MINI 与 MINI 2 不能混用。"""
    compact = re.sub(r'[\s_-]', '', value).upper()
    return MODEL if compact in {'A119MINI2', 'VIOFOA119MINI2'} else None

def chunks_for(issue: Issue) -> list[Chunk]:
    chunks: list[Chunk] = json.loads((ROOT / 'manuals/chunks.json').read_text(encoding='utf-8'))
    return [chunk for chunk in chunks if chunk['id'] in CHUNK_IDS[issue]]

def next_question(session: Session) -> Question | None:
    if session.workflow_version == 3 and session.memory is not None:
        from support_demo.memory import question_key
        return next((q for q in session.questions if question_key(q) not in session.memory.facts), None)
    questions = session.questions if session.workflow_version == 2 else QUESTIONS[session.issue]
    return next((q for q in questions if q.key not in session.answers), None)

def validate_advice(advice: Advice, chunks: list[Chunk]) -> None:
    """逐字引用校验只保证出处存在；语义仍需真实样本人工验收。"""
    by_id = {chunk['id']: chunk for chunk in chunks}
    for step in advice.steps:
        # 本版不输出有数据损失或拆装风险的操作，命中时拒绝整个结果而非改写模型答案。
        if re.search(r'格式化|刷机|拆机|恢复出厂|format|firmware|disassembl|factory reset', step.action, re.I):
            raise ValueError('unsafe_step')
        if re.search(r'\[\s*\]|图标缺失|缺失图标', step.action):
            raise ValueError('unusable_step')
        if re.search(r'(取出|拔出|插入|移除).{0,12}(卡|microSD)|取卡|拔卡|插卡', step.action, re.I):
            if not re.search(r'关机|关闭相机|关闭设备', step.action) or not re.search(r'拔.{0,6}电源|断开电源', step.action):
                raise ValueError('unsafe_card_handling')
        for evidence in step.evidence:
            source = by_id.get(evidence.chunk_id)
            if not source or source['page'] != evidence.page or evidence.quote not in source['text']:
                raise ValueError('invalid_evidence')

def validate_image(data_url: str) -> str:
    """验证真实图片内容，拒绝远程 URL，避免服务端读取任意地址。"""
    match = re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)', data_url)
    if not match or len(data_url) > 7_000_000:
        raise ValueError('invalid_image')
    try:
        raw = base64.b64decode(match[2], validate=True)
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError('image_too_large')
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != {'png': 'PNG', 'jpeg': 'JPEG', 'webp': 'WEBP'}[match[1]]:
                raise ValueError('image_mime_mismatch')
            if image.width * image.height > 16_000_000 or min(image.size) < 32:
                raise ValueError('image_dimensions')
            image.verify()
    except (OSError, binascii.Error, Image.DecompressionBombError) as exc:
        raise ValueError('invalid_image') from exc
    return data_url
