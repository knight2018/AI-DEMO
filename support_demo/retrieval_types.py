"""SPEC-007：知识库、意图识别与检索过程的明确数据结构。"""
from typing import Literal
from pydantic import BaseModel, Field

IntentName = Literal['wifi', 'recording', 'power', 'other_support', 'unrelated', 'unclear']

class IntentResult(BaseModel):
    name: IntentName
    reason: str = Field(min_length=1, max_length=500)

class Expansion(BaseModel):
    chinese: str = Field(min_length=1, max_length=1500)
    english: str = Field(min_length=1, max_length=1500)

class SearchQuestion(BaseModel):
    chinese: str = Field(min_length=1, max_length=300)
    english: str = Field(min_length=1, max_length=400)
    quote: str = Field(min_length=12, max_length=1800)

class FollowupSeed(BaseModel):
    intent: Literal['wifi', 'recording', 'power', 'general']
    field: str = Field(pattern=r'^[a-z][a-z0-9_]{1,50}$')
    text: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=300)
    options: list[str] = Field(min_length=1, max_length=5)
    quote: str = Field(min_length=12, max_length=1800)

class GeneratedQuestions(BaseModel):
    search_questions: list[SearchQuestion] = Field(default_factory=list, max_length=3)
    followups: list[FollowupSeed] = Field(default_factory=list, max_length=2)

class KnowledgeDocument(GeneratedQuestions):
    chunk_id: str
    page: int
    text: str
    model: str
    version: str
    source_id: str = ''
    chapter_path: list[str] = Field(default_factory=list)
    start: int = 0
    end: int = 0

class SearchHit(BaseModel):
    chunk_id: str
    page: int
    text: str
    bm25_rank: int | None = None
    bm25_score: float | None = None
    vector_rank: int | None = None
    vector_score: float | None = None
    rrf_score: float
    search_questions: list[SearchQuestion]
    source_id: str = ''
    chapter_path: list[str] = Field(default_factory=list)

class RetrievalTrace(BaseModel):
    intent: IntentResult
    original: str
    expansion: Expansion
    queries: list[str] = Field(default_factory=list)
    model_filter: str
    index_id: str
    embedding_model: str
    dimensions: int
    rrf_k: int = 60
    lane_k: int = 8
    fused_k: int = 6
    bm25: list[SearchHit]
    vector: list[SearchHit]
    fused: list[SearchHit]
    linked_evidence: list[str] = Field(default_factory=list)
    memory_revision: int | None = None

class KnowledgeManifest(BaseModel):
    index_id: str
    model: str
    version: str
    embedding_model: str
    dimensions: int
    document_count: int
    search_question_count: int
    followup_count: int
    source_sha256: str
    chunks_sha256: str
    documents_sha256: str
    index_sha256: str
    generator_model: str
    generator_version: str
    index_format: str = 'parent_v1'
    passages: list[str] = Field(default_factory=list)
    passage_parents: list[int] = Field(default_factory=list)
    chunking_version: str = ''
