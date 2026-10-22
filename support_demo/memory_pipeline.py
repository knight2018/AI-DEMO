"""SPEC-009：仅新会话启用记忆，保留旧版本工作流。"""
from support_demo import ai
from support_demo.domain import Advice, Session
from support_demo.memory import (Extractor, current_context, extract_patch, get_memory,
    pending_questions, process_message, sync_progress)
from support_demo.memory_types import SessionMemory
from support_demo.pipeline import IntakeResult, SupportPipeline
from support_demo.retrieval import embed, query_variants
from support_demo.retrieval_types import IntentResult, RetrievalTrace

class NoRemainingActions(Exception):
    def __init__(self, trace: RetrievalTrace) -> None:
        super().__init__('no_remaining_actions')
        self.trace=trace

class MemoryPipeline(SupportPipeline):
    extractor: Extractor = staticmethod(extract_patch)

    async def intake(self, description: str, model: str) -> IntakeResult:
        result=await super().intake(description,model)
        if result.session is None:return result
        session=result.session
        session.workflow_version=3
        session.memory=SessionMemory()
        session.questions=pending_questions(session,session.questions)
        session=await process_message(session,description,self.extractor)
        if 'problem' not in get_memory(session).facts or get_memory(session).facts['problem'].status!='known':
            raise ValueError('initial_problem_required')
        sync_progress(session)
        for trace in session.retrieval_history:trace.memory_revision=get_memory(session).revision
        result.session=session
        return result

    async def retrieve(self, session: Session, intent: IntentResult) -> RetrievalTrace:
        if session.workflow_version!=3:return await super().retrieve(session,intent)
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
        trace=await self.retrieve(session,session.retrieval_history[0].intent)
        chunks,allowed=self.grounded_context(session,trace)
        actions=get_memory(session).actions
        allowed=[key for key in allowed if key not in actions or actions[key].status not in {'completed','unable'}]
        if not allowed:raise NoRemainingActions(trace)
        result=await ai.advise(session,chunks,allowed)
        return result,trace
