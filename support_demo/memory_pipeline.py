"""SPEC-009：仅新会话启用记忆，保留旧版本工作流。"""
import asyncio
import uuid
from typing import cast

from support_demo import ai, streaming
from support_demo.domain import Advice, Session, Issue
from support_demo.memory import (Extractor, apply_patch, current_context, extract_patch, get_memory,
    sync_progress)
from support_demo.memory_types import SessionMemory
from support_demo.pipeline import IntakeResult, SupportPipeline, PROGRAM_REPLIES
from support_demo.retrieval import embed, query_variants
from support_demo.retrieval_types import IntentResult, RetrievalTrace

class NoRemainingActions(Exception):
    def __init__(self, trace: RetrievalTrace) -> None:
        super().__init__('no_remaining_actions')
        self.trace=trace

class MemoryPipeline(SupportPipeline):
    extractor: Extractor = staticmethod(extract_patch)

    async def intake(self, description: str, model: str) -> IntakeResult:
        await streaming.status('我先确认一下问题属于哪种情况。')
        intent = await self.classifier(description)
        if intent.name in PROGRAM_REPLIES:
            return IntakeResult(intent=intent, reply=PROGRAM_REPLIES[intent.name], session=None)
        session = Session(id=uuid.uuid4().hex, model=model, issue=cast(Issue, intent.name),
                          description=description, workflow_version=3, memory=SessionMemory())
        trace = await super().retrieve(session, intent)
        candidates = self.question_candidates(trace)
        session.questions = candidates
        await streaming.status('已找到相关内容，正在确认还需要了解哪些信息。')
        # 候选字段齐全后，并行选择追问与提取记忆；保留非核心字段的初始事实。
        # 先取得完整结果再应用，避免把尚未筛选的追问写进聊天记录。
        async with asyncio.TaskGroup() as group:
            selection = group.create_task(self.selector(description, candidates))
            extraction = group.create_task(self.extractor(session, description, None, None))
        keys = selection.result()
        by_key = {question.key: question for question in candidates}
        if len(keys) != len(set(keys)) or any(key not in by_key for key in keys):
            raise ValueError('invalid_question_selection')
        # 先在完整候选字段范围内验证并应用初始事实，随后只保留筛选后的待问项。
        apply_patch(session, description, extraction.result(), update_progress=False)
        if 'problem' not in get_memory(session).facts or get_memory(session).facts['problem'].status!='known':
            raise ValueError('initial_problem_required')
        session.questions = [by_key[key] for key in keys]
        # 导出答案只对应保留的问题；完整初始事实仍保存在会话记忆中。
        session.answers = {key: value for key, value in session.answers.items() if key in keys}
        session.retrieval_history = [trace]
        sync_progress(session)
        for trace in session.retrieval_history:trace.memory_revision=get_memory(session).revision
        return IntakeResult(intent=intent, reply='', session=session)

    async def retrieve(self, session: Session, intent: IntentResult) -> RetrievalTrace:
        if session.workflow_version!=3:return await super().retrieve(session,intent)
        await streaming.status('正在根据你补充的信息重新核对说明书。')
        query=current_context(session,for_search=True)
        if not query:raise ValueError('current_problem_unknown')
        expansion=await self.expander(query)
        vectors=await embed(query_variants(query,expansion))
        trace=self.index.search(query,expansion,vectors,session.model,intent)
        trace.original=current_context(session)
        trace.memory_revision=get_memory(session).revision
        return trace

    async def advise(self, session: Session) -> tuple[Advice,RetrievalTrace]:
        if session.workflow_version!=3:return await super().advise(session)
        # 事实版本未变时沿用已验证的召回；新回答或纠正必须重新检索。
        latest = session.retrieval_history[-1]
        trace = (latest.model_copy(deep=True) if latest.memory_revision == get_memory(session).revision
                 else await self.retrieve(session, session.retrieval_history[0].intent))
        chunks,allowed=self.grounded_context(session,trace)
        actions=get_memory(session).actions
        allowed=[key for key in allowed if key not in actions or actions[key].status not in {'completed','unable'}]
        if not allowed:raise NoRemainingActions(trace)
        await streaming.status('正在整理适合当前情况的排查建议。')
        result=await ai.advise(session,chunks,allowed)
        return result,trace
