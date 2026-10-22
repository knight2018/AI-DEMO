"""SPEC-001：安全导出原文和报告。"""
import csv
import io
from html import escape
from backend.service import Batch

def csv_value(value: object) -> str:
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text

def export_csv(batch: Batch) -> str:
    output = io.StringIO(newline="")
    fields = ["batch_id", "site", "asin", "source_url", "captured_at", "quality", "review_id", "url", "rating", "title", "body", "date_text", "country", "variant", "verified"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    records = [(r, "valid") for r in batch["validation"]["valid"]]
    records += [(r["review"], r["reason"]) for r in batch["validation"]["excluded"]]
    for review, quality in records:
        values = dict(review)
        values.update(batch_id=batch["id"], site=batch["snapshot"]["site"], asin=batch["snapshot"]["asin"], source_url=batch["snapshot"]["url"], captured_at=batch["snapshot"]["captured_at"], quality=quality)
        writer.writerow({field: csv_value(values.get(field)) for field in fields})
    return "\ufeff" + output.getvalue()

def export_html(batch: Batch) -> str:
    if batch["report"] is None:
        raise ValueError("report_unavailable")
    report = batch["report"]
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><title>评论洞察报告</title><style>body{max-width:960px;margin:48px auto;font:16px/1.8 system-ui;padding:24px;color:#172e30}section{border-top:1px solid #ccc;padding:18px 0}blockquote{background:#f3f6f5;padding:14px;white-space:pre-wrap}</style><h1>评论洞察报告</h1>']
    parts.append(f'<p>{escape(batch["snapshot"]["title"])} · {escape(batch["snapshot"]["site"])}</p><p>来源：{escape(batch["snapshot"]["url"])}<br>采集时间：{escape(batch["snapshot"]["captured_at"])}</p>')
    parts.append(f'<p>{escape(report["scope"])} 样本数：{report["sample_size"]}</p><h2>整体结论</h2><p>{escape(report["summary"])}</p>')
    parts.append('<h2>优先建议与后续行动</h2><p>以下为基于本批样本的建议顺序；负责角色、阶段和验收标准均为建议。</p>')
    if 'priorities' not in report:
        parts.append('<p>历史报告尚未生成行动建议。</p>')
    else:
        for rank, action in enumerate(report['priorities'], 1):
            parts.append(f'<section><h3>优先 {rank}：{escape(action["title"])}</h3>')
            for label, key in [('排序理由', 'reason'), ('下一步', 'next_step'), ('建议负责', 'owner'), ('建议阶段', 'timing'), ('验收标准', 'success_criteria')]:
                parts.append(f'<p><strong>{label}：</strong>{escape(action[key])}</p>')
            parts.append(f'<p>依据主题（见下方原文证据）：{escape("、".join(action["topic_names"]))}</p></section>')
    parts.append('<h2>主题与原文证据</h2>')
    for topic in report["topics"]:
        parts.append(f'<section><h2>{escape(topic["name"])} · {topic["count"]}条 / {topic["percent"]}%</h2>')
        for label, key in [("观察", "observation"), ("建议", "suggestion"), ("待核实原因", "hypothesis")]:
            parts.append(f'<p>{label}：{escape(topic[key])}</p>')
        for evidence in topic["evidence"]:
            parts.append(f'<blockquote>{escape(evidence["review_id"])}：{escape(evidence["quote"])}</blockquote>')
        parts.append('</section>')
    parts.append('</html>')
    return "".join(parts)
