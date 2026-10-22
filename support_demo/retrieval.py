"""SPEC-007：BM25、FAISS 与按名次融合，不把不同量纲分数直接相加。"""
import hashlib
import json
import os
import re
from pathlib import Path
from collections.abc import Sequence

import faiss
import httpx
import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi

from support_demo.retrieval_types import (Expansion, IntentResult, KnowledgeDocument,
                                         KnowledgeManifest, RetrievalTrace, SearchHit)

EMBEDDING_MODEL = 'text-embedding-3-small'
DIMENSIONS = 1536
KB_ROOT = Path(__file__).parent / 'data/knowledge'
STOP_WORDS = set('a an the is are was were be been being to of for from in on at by and or as it its this that these those i me my we our you your please help find search information about how what which do does did can could would should want'.split())

def search_text(text: str) -> str:
    """型号已用于索引范围过滤，去除其重复词项，避免封面因型号相同排在故障段之前。"""
    return re.sub(r'\bA119\s*MINI\s*2\b', '', text, flags=re.I).strip()

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def tokenize(text: str) -> list[str]:
    """中英同一规则：英文词/数字、中文双字与卡灯屏电关键词；保留错误码。"""
    text = search_text(text)
    tokens = [token for token in re.findall(r'[a-z0-9]+', text.lower()) if token not in STOP_WORDS]
    for run in re.findall(r'[\u4e00-\u9fff]+', text):
        tokens.extend(char for char in run if char in '卡灯屏电')
        tokens.extend(run[i:i+2] for i in range(len(run)-1))
    return tokens

def query_variants(original: str, expansion: Expansion, initial_query: str | None = None) -> list[str]:
    """初始问题独立保留，避免补充问答改变全部查询的重心。"""
    values = ([initial_query] if initial_query is not None else []) + [original, expansion.chinese, expansion.english]
    return list(dict.fromkeys(search_text(value) for value in values if search_text(value)))

def document_text(doc: KnowledgeDocument) -> str:
    return '\n'.join([doc.text] + [q.chinese+' '+q.english for q in doc.search_questions])

def index_passages(docs: list[KnowledgeDocument]) -> tuple[list[str], list[int]]:
    """小节原文和预生成问法分别索引，保留章节上下文，不逐句拆散操作。"""
    passages: list[str] = []
    parents: list[int] = []
    for parent, doc in enumerate(docs):
        prefix = ' > '.join(doc.chapter_path)
        candidates = [prefix+'\n'+doc.text if prefix else doc.text]
        candidates += [question.chinese+' '+question.english for question in doc.search_questions]
        for text in dict.fromkeys(candidates):
            passages.append(text)
            parents.append(parent)
    return passages, parents

class EmbeddingRow(BaseModel):
    index: int = Field(ge=0)
    embedding: list[float]

class EmbeddingResponse(BaseModel):
    data: list[EmbeddingRow]

async def embed(texts: list[str]) -> NDArray[np.float32]:
    """校验真实 API 返回的顺序、维度与数值，不生成替代向量。"""
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post('https://api.agicto.cn/v1/embeddings',
            headers={'Authorization': 'Bearer '+os.environ['AGI_KEY']},
            json={'model': EMBEDDING_MODEL, 'input': texts, 'encoding_format': 'float'})
        response.raise_for_status()
    parsed = EmbeddingResponse.model_validate(response.json())
    rows = sorted(parsed.data, key=lambda row: row.index)
    if [row.index for row in rows] != list(range(len(texts))):
        raise ValueError('embedding_order_mismatch')
    array = np.asarray([row.embedding for row in rows], dtype=np.float32)
    if array.shape != (len(texts), DIMENSIONS) or not np.isfinite(array).all():
        raise ValueError('embedding_dimensions_mismatch')
    if (np.linalg.norm(array, axis=1) == 0).any():
        raise ValueError('embedding_zero_vector')
    return array

def normalize(vectors: NDArray[np.float32]) -> NDArray[np.float32]:
    result = np.array(vectors, dtype=np.float32, order='C', copy=True)
    if result.ndim != 2 or not np.isfinite(result).all() or (np.linalg.norm(result, axis=1) == 0).any():
        raise ValueError('invalid_vectors')
    faiss.normalize_L2(result)
    return result

def reciprocal_rank_fusion(lanes: Sequence[Sequence[int]], k: int = 60) -> dict[int, float]:
    scores: dict[int, float] = {}
    for lane in lanes:
        seen: set[int] = set()
        for item in lane:
            if item in seen:
                continue
            seen.add(item)
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + len(seen))
    return scores

class HybridIndex:
    def __init__(self, docs: list[KnowledgeDocument], index: faiss.IndexFlatIP,
                 manifest: KnowledgeManifest) -> None:
        parents = manifest.passage_parents if manifest.index_format == 'sections_v3' else list(range(len(docs)))
        passages = manifest.passages if manifest.index_format == 'sections_v3' else [document_text(doc) for doc in docs]
        if manifest.index_format == 'sections_v3' and (passages, parents) != index_passages(docs):
            raise ValueError('passage_metadata_mismatch')
        if len(parents) != index.ntotal or len(passages) != index.ntotal or index.d != manifest.dimensions:
            raise ValueError('index_shape_mismatch')
        if any(parent < 0 or parent >= len(docs) for parent in parents):
            raise ValueError('invalid_passage_parent')
        if manifest.document_count != len(docs) or index.metric_type != faiss.METRIC_INNER_PRODUCT:
            raise ValueError('index_manifest_mismatch')
        if len({doc.chunk_id for doc in docs}) != len(docs) or not docs:
            raise ValueError('duplicate_or_empty_documents')
        if any(doc.model != manifest.model or doc.version != manifest.version for doc in docs):
            raise ValueError('document_scope_mismatch')
        for doc in docs:
            if any(question.quote not in doc.text for question in [*doc.search_questions, *doc.followups]):
                raise ValueError('knowledge_evidence_mismatch')
        self.docs = docs
        self.index = index
        self.manifest = manifest
        self.by_id = {doc.chunk_id: doc for doc in docs}
        self.parents = parents
        self.bm25 = BM25Okapi([tokenize(text) for text in passages])

    @classmethod
    def load(cls, root: Path = KB_ROOT) -> 'HybridIndex':
        current = (root/'current.txt').read_text(encoding='utf-8').strip()
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', current):
            raise ValueError('invalid_index_id')
        directory = root/current
        manifest = KnowledgeManifest.model_validate_json((directory/'manifest.json').read_text(encoding='utf-8'))
        manual_root = Path(__file__).parent/'manuals'
        if manifest.embedding_model != EMBEDDING_MODEL or manifest.dimensions != DIMENSIONS:
            raise ValueError('embedding_configuration_changed_rebuild_required')
        from support_demo.chunking import CHUNKING_VERSION, structured_chunks
        if manifest.index_format != 'sections_v3' or manifest.chunking_version != CHUNKING_VERSION:
            raise ValueError('index_format_changed_rebuild_required')
        if manifest.index_id != current or sha256((manual_root/'a119-mini2.pdf').read_bytes()) != manifest.source_sha256 or sha256((manual_root/'chunks.json').read_bytes()) != manifest.chunks_sha256:
            raise ValueError('source_changed_rebuild_required')
        docs_bytes = (directory/'documents.json').read_bytes()
        index_bytes = (directory/'vectors.faiss').read_bytes()
        if sha256(docs_bytes) != manifest.documents_sha256 or sha256(index_bytes) != manifest.index_sha256:
            raise ValueError('index_checksum_mismatch')
        docs = [KnowledgeDocument.model_validate(item) for item in json.loads(docs_bytes)]
        expected = structured_chunks(manual_root)
        actual = [(doc.chunk_id,doc.source_id,doc.text,doc.page,doc.chapter_path,doc.start,doc.end) for doc in docs]
        if actual != [(c['id'],c['source_id'],c['text'],c['page'],c['chapter_path'],c['start'],c['end']) for c in expected]:
            raise ValueError('chunk_structure_changed_rebuild_required')
        index = faiss.deserialize_index(np.frombuffer(index_bytes, dtype=np.uint8))
        return cls(docs, index, manifest)

    def search(self, original: str, expansion: Expansion, query_vectors: NDArray[np.float32],
               model: str, intent: IntentResult, lane_k: int = 8, fused_k: int = 6,
               initial_query: str | None = None) -> RetrievalTrace:
        # 每个索引仅含一个已确认型号，检索前检查，杜绝先检索后截断导致串型号。
        if model != self.manifest.model:
            raise ValueError('model_scope_mismatch')
        queries = query_variants(original, expansion, initial_query)
        vectors = normalize(query_vectors)
        if vectors.shape != (len(queries), self.index.d):
            raise ValueError('query_dimensions_mismatch')
        passage_scores = np.max(np.stack([self.bm25.get_scores(tokenize(query)) for query in queries]), axis=0)
        scores = np.zeros(len(self.docs))
        for passage, parent in enumerate(self.parents):
            scores[parent] = max(scores[parent], passage_scores[passage])
        bm_order = sorted((i for i in range(len(self.docs)) if scores[i] > 0), key=lambda i: (-float(scores[i]), self.docs[i].chunk_id))[:lane_k]
        # 当前单说明书规模很小，精确搜索全部子片段后按父切片取最大分，
        # 防止同一小节的多个问法占满Top-K，随后才截断为每路8个切片。
        similarities, ids = self.index.search(vectors, self.index.ntotal)
        vector_scores: dict[int, float] = {}
        for row_scores, row_ids in zip(similarities, ids):
            for score, identity in zip(row_scores, row_ids):
                if identity >= 0:
                    identity = self.parents[int(identity)]
                    vector_scores[identity] = max(vector_scores.get(identity, -1.0), float(score))
        vector_order = sorted(vector_scores, key=lambda i: (-vector_scores[i], self.docs[i].chunk_id))[:lane_k]
        fused = reciprocal_rank_fusion([bm_order, vector_order])
        order = sorted(fused, key=lambda i: (-fused[i], self.docs[i].chunk_id))[:fused_k]
        bm_ranks = {identity: rank for rank, identity in enumerate(bm_order, 1)}
        vec_ranks = {identity: rank for rank, identity in enumerate(vector_order, 1)}
        def hit(identity: int) -> SearchHit:
            doc = self.docs[identity]
            return SearchHit(chunk_id=doc.chunk_id, page=doc.page, text=doc.text,
                source_id=doc.source_id, chapter_path=doc.chapter_path,
                bm25_rank=bm_ranks.get(identity), bm25_score=float(scores[identity]) if identity in bm_ranks else None,
                vector_rank=vec_ranks.get(identity), vector_score=vector_scores[identity] if identity in vec_ranks else None,
                rrf_score=fused[identity], search_questions=doc.search_questions)
        return RetrievalTrace(intent=intent, original=original, expansion=expansion, model_filter=model,
            queries=queries,
            index_id=self.manifest.index_id, embedding_model=self.manifest.embedding_model, dimensions=self.index.d,
            lane_k=lane_k, fused_k=fused_k, bm25=[hit(i) for i in bm_order],
            vector=[hit(i) for i in vector_order], fused=[hit(i) for i in order])
