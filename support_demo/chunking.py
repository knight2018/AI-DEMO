"""SPEC-008：用已核实的 PDF 标题版式定位原文，不重写证据。"""
import json
import re
from pathlib import Path

import pymupdf

from support_demo.domain import Chunk

CHUNKING_VERSION = 'a119-headings-paragraphs-v2'

class StructuredChunk(Chunk):
    source_id: str
    chapter_path: list[str]
    start: int
    end: int

def compact(text: str) -> str:
    return ' '.join(text.split())

def paragraph_ranges(text: str, boundaries: list[int], limit: int = 1200) -> list[tuple[int, int]]:
    """长度是软限制；编号操作及注意事项整体保留，避免丢掉执行前提。"""
    if len(text) <= limit or re.search(r'\b(?:Note|Warning|Caution|Step)\b|(?:^|\s)\d+\.', text, re.I):
        return [(0, len(text))]
    result: list[tuple[int, int]] = []
    start = 0
    previous = 0
    for end in sorted(set([*boundaries, len(text)])):
        if not 0 < end <= len(text):
            continue
        if end-start > limit and previous > start:
            result.append((start, previous))
            start = previous
        previous = end
    result.append((start, len(text)))
    return result

def structured_chunks(manual_root: Path) -> list[StructuredChunk]:
    parents: list[Chunk] = json.loads((manual_root/'chunks.json').read_text(encoding='utf-8'))
    result: list[StructuredChunk] = []
    chapter = 'A119 MINI 2'
    section = ''
    subsection = ''
    with pymupdf.open(manual_root/'a119-mini2.pdf') as pdf:
        for parent in parents:
            page = pdf[parent['page']-1]
            half = int(parent['id'].split('-')[1])-1
            clip = pymupdf.Rect(half*page.rect.width/2, 0, (half+1)*page.rect.width/2, page.rect.height-20)
            headings: dict[int, tuple[str, float]] = {}
            heading_rows: list[tuple[float, str, float]] = []
            paragraphs: list[int] = []
            for block in page.get_text('dict', clip=clip)['blocks']:
                if 'lines' not in block:
                    continue
                block_text = compact(' '.join(span['text'] for line in block['lines'] for span in line['spans']))
                offset = parent['text'].find(block_text)
                if offset >= 0:
                    paragraphs.append(offset)
                for line in block['lines']:
                    spans = line['spans']
                    if not spans or 'Bold' not in spans[0]['font'] or spans[0]['size'] < 6.9:
                        continue
                    title = compact(' '.join(span['text'] for span in spans if 'Bold' in span['font']))
                    # 说明书中的提示、步骤和表格标题属于正文，不构成新主题。
                    if not title or re.match(r'^(?:Note|Warning|Caution|Tips?|[•\d]|Step\s)', title, re.I) or title in {'Recording Mode','Behavior','Buttons','Mode','LED','LED Status'}:
                        continue
                    heading_rows.append((line['bbox'][1], title, spans[0]['size']))
            # 按视觉纵坐标顺序定位，避免把 G-sensor 错匹配到 Parking G-sensor 内。
            cursor = 0
            for _, title, size in sorted(heading_rows):
                position = parent['text'].find(title, cursor)
                if position >= 0:
                    headings[position] = (title.rstrip(': '), size)
                    cursor = position + len(title)
            points = sorted({0, *headings, len(parent['text'])})
            ordinal = 0
            for start, end in zip(points, points[1:]):
                if start in headings:
                    title, size = headings[start]
                    if size >= 8.8:
                        chapter, section, subsection = title, '', ''
                    elif size >= 7.8:
                        section, subsection = title, ''
                    else:
                        subsection = title
                text = parent['text'][start:end]
                if not text.strip() or (start in headings and text.strip().rstrip(': ') == headings[start][0]):
                    continue
                path = list(dict.fromkeys(part for part in [chapter, section, subsection] if part))
                for local_start, local_end in paragraph_ranges(text, [pos-start for pos in paragraphs if start < pos < end]):
                    a, b = start+local_start, start+local_end
                    while a < b and parent['text'][a].isspace(): a += 1
                    while b > a and parent['text'][b-1].isspace(): b -= 1
                    if a == b: continue
                    ordinal += 1
                    result.append(StructuredChunk(id=f"{parent['id']}-s{ordinal:02d}", page=parent['page'],
                        text=parent['text'][a:b], source_id=parent['id'], chapter_path=path.copy(), start=a, end=b))
    return result
