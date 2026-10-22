"""SPEC-001：评论校验与证据统计。"""
from typing import TypedDict, Literal, NotRequired
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field

class Review(TypedDict, total=False):
    review_id: str | None
    body: str
    rating: int | float | str | None
    expanded: bool
    local_id: str
    title: str | None
    url: str | None
    date_text: str | None
    country: str | None
    variant: str | None
    verified: bool | None
    captured_at: str

class Excluded(TypedDict):
    review: Review
    reason: str

class Validated(TypedDict):
    valid: list[Review]
    duplicates: int
    excluded: list[Excluded]
    found: int

class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)

class Topic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    sentiment: Literal["positive", "negative", "mixed", "neutral"]
    observation: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)
    hypothesis: str
    evidence: list[Evidence] = Field(min_length=1)

class PriorityAction(BaseModel):
    """SPEC-004：建议执行顺序由列表位置表达，依据回连已校验主题。"""
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    topic_names: list[str] = Field(min_length=1)
    next_step: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    timing: str = Field(min_length=1)
    success_criteria: str = Field(min_length=1)

class ActionReport(TypedDict):
    title: str
    reason: str
    topic_names: list[str]
    next_step: str
    owner: str
    timing: str
    success_criteria: str

class Insight(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1)
    topics: list[Topic] = Field(min_length=1)
    priorities: list[PriorityAction] = Field(min_length=1, max_length=3)

class TopicReport(TypedDict):
    name: str
    sentiment: str
    observation: str
    suggestion: str
    hypothesis: str
    evidence: list[dict[str, str]]
    count: int
    percent: float

class Report(TypedDict):
    summary: str
    topics: list[TopicReport]
    sample_size: int
    scope: str
    priorities: NotRequired[list[ActionReport]]

def validate_reviews(reviews: list[Review]) -> Validated:
    """批次只属于一个站点；不同ID的相同正文不合并。"""
    result: Validated = {"valid": [], "duplicates": 0, "excluded": [], "found": len(reviews)}
    seen: set[str] = set()
    for raw in reviews:
        review = raw.copy()
        review["local_id"] = uuid4().hex
        identity = review.get("review_id")
        reason = ""
        if not identity:
            reason = "identity_missing"
        elif identity in seen:
            result["duplicates"] += 1
            continue
        else:
            seen.add(identity)
        if not reason and not review.get("body", "").strip():
            reason = "body_empty"
        if not reason and not review.get("expanded", False):
            reason = "expansion_failed"
        rating = review.get("rating")
        if not reason and (type(rating) is not int or rating not in range(1, 6)):
            reason = "rating_invalid"
        if reason:
            result["excluded"].append({"review": review, "reason": reason})
        else:
            result["valid"].append(review)
    return result

def check_limits(reviews: list[Review]) -> None:
    if len(reviews) > 100 or sum(len(r["body"]) for r in reviews) > 100_000:
        raise ValueError("sample_limit_exceeded")

def build_report(insight: Insight, reviews: list[Review]) -> Report:
    """引用必须逐字对应输入；数量只能由程序计算。"""
    source = {r["review_id"]: r["body"] for r in reviews}
    if not source:
        raise ValueError("no_valid_reviews")
    topics: list[TopicReport] = []
    names: set[str] = set()
    for topic in insight.topics:
        if topic.name.strip() in names:
            raise ValueError("duplicate_topic")
        names.add(topic.name.strip())
        ids: set[str] = set()
        for evidence in topic.evidence:
            if evidence.review_id not in source or not evidence.quote.strip() or evidence.quote not in source[evidence.review_id]:
                raise ValueError("evidence_mismatch")
            ids.add(evidence.review_id)
        topics.append(TopicReport(
            name=topic.name, sentiment=topic.sentiment, observation=topic.observation,
            suggestion=topic.suggestion, hypothesis=topic.hypothesis,
            evidence=[e.model_dump() for e in topic.evidence], count=len(ids),
            percent=round(len(ids) / len(source) * 100, 2),
        ))
    priorities: list[ActionReport] = []
    for action in insight.priorities:
        if any(name not in names for name in action.topic_names):
            raise ValueError("action_topic_mismatch")
        priorities.append(ActionReport(title=action.title, reason=action.reason,
            topic_names=action.topic_names, next_step=action.next_step, owner=action.owner,
            timing=action.timing, success_criteria=action.success_criteria))
    return {"summary": insight.summary, "topics": topics, "priorities": priorities, "sample_size": len(source), "scope": "本次详情页可见评论样本；主题占比不是产品故障率，多主题占比之和可超过100%。"}
