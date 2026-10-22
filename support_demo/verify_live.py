"""SPEC005-TC09/SPEC006-TC02：显式运行真实模型验收并保存证据。"""
import asyncio
import base64
import io
import json
import time
from pathlib import Path

from PIL import Image

from support_demo.ai import advise, identify
from support_demo.domain import ROOT, MODEL, Session, chunks_for

async def main() -> None:
    directory = ROOT / 'data/qa'
    directory.mkdir(parents=True, exist_ok=True)
    stream = io.BytesIO()
    Image.new('RGB', (256, 256), 'white').save(stream, 'PNG')
    images = {'manual-cover': (ROOT/'manuals/cover.png').read_bytes(), 'blank': stream.getvalue()}

    async def vision(name: str, raw: bytes) -> None:
        started = time.monotonic()
        try:
            result = await identify('data:image/png;base64,'+base64.b64encode(raw).decode())
            record = {'case': name, 'kind': 'vision', 'seconds': round(time.monotonic()-started, 2), 'result': result.model_dump()}
        except Exception as exc:
            record = {'case': name, 'kind': 'vision', 'error_type': type(exc).__name__}
        (directory/f'{name}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(record, ensure_ascii=False), flush=True)

    sessions = [
        Session(id='live-wifi', model=MODEL, issue='wifi', description='手机连接了相机Wi-Fi，但App没有画面。', answers={'wifi_display':'已显示名称与密码', 'phone_connection':'已连接', 'app_state':'尚未选择设备型号'}, status='ready'),
        Session(id='live-recording', model=MODEL, issue='recording', description='开机后屏幕过一会儿变黑，不知道是不是停止录像。', answers={'card':'128GB UHS-I', 'symptom':'屏幕熄灭，录像状态不确定'}, status='ready'),
        Session(id='live-power', model=MODEL, issue='power', description='车已停稳且通电，相机没有反应。', answers={'power_source':'配套车充接车辆插座', 'power_state':'屏幕和灯均无反应'}, status='ready')
    ]
    async def text(session: Session) -> None:
        started = time.monotonic()
        try:
            result = await advise(session, chunks_for(session.issue))
            record = {'case': session.issue, 'kind':'advice', 'seconds':round(time.monotonic()-started,2), 'input': session.model_dump(), 'result': result.model_dump()}
        except Exception as exc:
            record = {'case': session.issue, 'kind':'advice', 'error_type':type(exc).__name__}
            if str(exc) in {'invalid_evidence', 'unsafe_step', 'unusable_step', 'unsafe_card_handling'}:
                record['validation_error'] = str(exc)
        (directory/f'{session.issue}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(record, ensure_ascii=False), flush=True)

    await asyncio.gather(*(vision(name, raw) for name,raw in images.items()), *(text(session) for session in sessions))

if __name__ == '__main__':
    asyncio.run(main())
