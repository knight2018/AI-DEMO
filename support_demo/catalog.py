"""SPEC-005：从官方说明书核对的操作，模型只能选择而不能改写。"""
from pydantic import BaseModel, Field
from support_demo.domain import Advice, Evidence, Issue, Step, chunks_for, validate_advice

class Selection(BaseModel):
    summary: str = Field(min_length=1, max_length=1000)
    step_ids: list[str] = Field(min_length=1, max_length=3)
    handoff: str = Field(min_length=1, max_length=700)

def evidence(chunk_id: str, quote: str) -> Evidence:
    return Evidence(chunk_id=chunk_id, page=int(chunk_id.split('-')[0][1:]), quote=quote)

CATALOG: dict[str, dict[str, Step]] = {
    'wifi': {
        'wifi_enable': Step(action='在相机菜单中开启 Wi-Fi，查看屏幕是否出现 Wi-Fi 名称（SSID）和密码。',
            expected='屏幕显示相机 Wi-Fi 名称与密码；若没有显示，记录屏幕现象。',
            evidence=[evidence('p10-2', 'When the Wi-Fi is on, a Wi-Fi status icon, Wi-Fi name (SSID) and the password appear on the camera LCD screen.')]),
        'wifi_connect': Step(action='在手机 Wi-Fi 设置中选择相机屏幕上显示的 A119 MINI 2 网络，使用相机屏幕显示的密码连接，再观察相机 LED。',
            expected='手机连接到相机网络后，相机 LED 应常亮蓝色；记录找不到网络、密码错误或其他实际提示。',
            evidence=[evidence('p10-2', 'Step 1: Use the phone to connect A119 Mini 2 Wi-Fi. When the phone is connected with the dashcam, the LED light on the camera shows solid blue.'), evidence('p10-2', 'When the Wi-Fi is on, a Wi-Fi status icon, Wi-Fi name (SSID) and the password appear on the camera LCD screen.')]),
        'wifi_app': Step(action='打开 VIOFO App，点击“Connect New Device”，选择 A119 MINI 2，查看是否进入实时预览。',
            expected='App 出现相机实时画面；若仍为空白，保留 App 的实际提示供人工核查。',
            evidence=[evidence('p10-2', 'Step 2: Open the VIOFO app. Tap "Connect New Device" and select the dashcam model you want to connect to your phone for control.')]),
        'wifi_led': Step(action='手机保持连接相机 Wi-Fi，观察相机 LED 的颜色和是否常亮。',
            expected='说明书描述连接后 LED 常亮蓝色；记录实际状态，不能仅凭灯光认定硬件损坏。',
            evidence=[evidence('p10-2', 'When the phone is connected with the dashcam, the LED light on the camera shows solid blue.')])
    },
    'recording': {
        'card_specs': Step(action='核对存储卡包装或卡面上的容量与速度等级。若需要取出卡片，必须先关闭相机并拔掉电源线，再取出存储卡。',
            expected='说明书要求 Class-10 或 UHS-I，容量不超过 512GB；看不清时记录为未知，不能猜测兼容性。',
            evidence=[evidence('p5-2', 'The microSD card must have a Class-10 or UHS-I rating and the capacity is up to 512GB.'), evidence('p5-2', 'Please unplug the power cable before inserting or removing the microSD card.'), evidence('p5-2', 'Ensure that the camera is turned off, and then push the edge of the memory card with your fingernail.')]),
        'screen_saver': Step(action='在设置菜单中查看“Screen Saver”的当前设置，对照屏幕熄灭的时间。',
            expected='屏幕保护会在设定时间后熄屏；熄屏本身不能证明录像停止，还需要检查录像文件。',
            evidence=[evidence('p14-2', 'Screen Saver: Turn the screen OFF after a set time.')]),
        'recording_files': Step(action='车辆停稳后，先关闭相机并拔掉电源线，再取出存储卡；用电脑查看已有的 DCIM/Movie 录像，核对是否覆盖屏幕熄灭后的时间段。',
            expected='若有覆盖该时间段的可播放文件，可确认该时段存在录像；若没有，记录缺失时段交给人工继续核查。',
            evidence=[evidence('p5-2', 'Please unplug the power cable before inserting or removing the microSD card.'), evidence('p5-2', 'Ensure that the camera is turned off, and then push the edge of the memory card with your fingernail.'), evidence('p8-2', 'Loop recording files are saved to SD card: \\DCIM\\Movie.')]),
    },
    'power': {
        'power_connections': Step(action='仅适用于配套车充：车辆停稳后，先关闭相机，再核对车充 USB 线连接的是相机支架 USB Type-C 口，以及适配器连接的是车辆 12V/24V 插座。不要改动车辆电路或气囊区域的布线。',
            expected='记录两个连接位置是否与说明书一致、是否有松动。若使用降压线或不清楚供电方式，直接交由专业人员核查。',
            evidence=[evidence('p7-1', "Insert the car charger USB cable’s male port into the camera mount's USB Type-C port. Plug the USB power adapter into your vehicle’s 12V / 24V female power socket."), evidence('p7-2', 'Do not install / mount the camera or cables near an airbag panel or within the airbag’s working range.'), evidence('p8-1', 'Note: When you want to plug in and unplug the camera, please turn it off first.')]),
        'boot_delay': Step(action='在车辆停稳、相机已接通原有供电的情况下，观察接通电源后 10 秒内屏幕或指示灯是否出现变化，不拆卸或重新接线。',
            expected='说明书提供关闭、5 秒、10 秒的延迟启动设置；记录实际变化。超过该时间仍无反应不能直接认定硬件故障，应转人工。',
            evidence=[evidence('p14-2', 'Boot Delay: The camera will boot seconds later when powered on. The settings are Off / 5s / 10s.')]),
        'power_screen': Step(action='若屏幕可以唤醒并进入菜单，查看“Screen Saver”和“LED”的当前设置；若无法进入菜单，记录为无法检查并交给人工。',
            expected='屏幕保护可关闭屏幕，LED 设置也可关闭指示灯；仅凭屏幕或灯灭无法证明设备停止工作。',
            evidence=[evidence('p14-2', 'Screen Saver: Turn the screen OFF after a set time.'), evidence('p14-2', 'LED: Turn the LED light ON / OFF.')])
    }
}

def build_advice(selection: Selection, issue: Issue) -> Advice:
    """未知或重复步骤直接报错，不能替换为默认建议。"""
    if len(set(selection.step_ids)) != len(selection.step_ids) or any(key not in CATALOG[issue] for key in selection.step_ids):
        raise ValueError('invalid_step_selection')
    advice = Advice(summary=selection.summary, steps=[CATALOG[issue][key].model_copy(deep=True) for key in selection.step_ids], handoff=selection.handoff)
    validate_advice(advice, chunks_for(issue))
    return advice
