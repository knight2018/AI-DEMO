"""SPEC-012：直接从已保存会话整理人工交接信息，不调用模型。"""
from support_demo.domain import QUESTIONS, Session
from support_demo.memory import field_labels


def handoff_summary(session: Session) -> str:
    lines = ['人工客服交接简报', f'设备：{session.model}', '状态：仍未解决，待人工核查']
    if session.memory is not None:
        memory = session.memory
        labels = {**field_labels(session), 'wifi_led_status': 'Wi-Fi指示灯状态'}
        problem = memory.facts.get('problem')
        lines.append('当前问题：' + (problem.value if problem and problem.status == 'known' else '待核实'))
        facts, pending = [], []
        for key, fact in memory.facts.items():
            if key == 'problem':
                continue
            label = labels.get(key, '补充情况')
            if fact.status == 'known':
                facts.append(f'{label}：{fact.value}')
            else:
                pending.append(f'{label}（' + ('用户不确定' if fact.status == 'unknown' else '原结论已撤回') + '）')
        lines.append('已确认情况（用户报告）：' + ('；'.join(facts) or '暂无'))
        states = {'completed': '用户报告已执行', 'unable': '无法执行',
                  'suggested': '仅建议，尚未反馈', 'retracted': '执行结果已撤回'}
        lines.append('排查记录：')
        for action in memory.actions.values():
            # 撤回结果不再作为有效观察交给人工。
            result = action.result if action.status in {'completed', 'unable'} else ''
            lines.append(f'- {action.text}【{states[action.status]}】' + (f'：{result}' if result else ''))
        if not memory.actions:
            lines.append('- 暂无已记录操作')
        if memory.clarification:
            pending.append(memory.clarification)
        if pending:
            lines.append('待核实：' + '；'.join(pending))
    else:
        lines.append('用户描述：' + session.description)
        questions = session.questions if session.workflow_version == 2 else QUESTIONS[session.issue]
        names = {q.key: q.text for q in questions}
        lines.append('已收集信息：' + ('；'.join(f'{names[key]}：{value}' for key, value in session.answers.items()) or '暂无'))
        lines.append('已给出建议（执行结果未记录）：' + ('；'.join(step.action for step in session.advice.steps) if session.advice else '暂无'))
    lines.extend(['请人工继续核查未解决原因，结合以上记录避免重复询问。', f'会话编号：{session.id}'])
    return '\n'.join(lines)
