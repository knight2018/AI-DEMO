"""SPEC-009：会话记忆与模型更新契约，事实和原话分别保存。"""
from typing import Literal
from pydantic import BaseModel, Field

FactStatus = Literal['known', 'unknown', 'retracted']
ActionStatus = Literal['suggested', 'completed', 'unable', 'retracted']

class FactUpdate(BaseModel):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{1,60}$')
    value: str = Field(min_length=1, max_length=500)
    status: FactStatus
    quote: str = Field(min_length=1, max_length=2000)
    depends_on_action: str | None = None

class ActionUpdate(BaseModel):
    action_id: str
    status: Literal['completed', 'unable', 'retracted']
    result: str = Field(min_length=1, max_length=700)
    quote: str = Field(min_length=1, max_length=2000)

class MemoryPatch(BaseModel):
    facts: list[FactUpdate] = Field(default_factory=list, max_length=15)
    actions: list[ActionUpdate] = Field(default_factory=list, max_length=5)
    clarification: str = Field(default='', max_length=500)
    unrelated: bool = False
    new_session_required: bool = False

class Turn(BaseModel):
    id: int
    role: Literal['user', 'assistant']
    text: str
    timestamp: str
    question_key: str | None = None
    action_id: str | None = None

class FactEvent(FactUpdate):
    id: int
    source_turn: int
    supersedes: int | None = None

class ActionEvent(BaseModel):
    action_id: str
    status: ActionStatus
    result: str
    quote: str
    source_turn: int

class ActionMemory(ActionEvent):
    text: str

class AdviceArchive(BaseModel):
    revision: int
    content: str
    reason: str

class SessionMemory(BaseModel):
    revision: int = 0
    turns: list[Turn] = Field(default_factory=list)
    facts: dict[str, FactEvent] = Field(default_factory=dict)
    events: list[FactEvent] = Field(default_factory=list)
    actions: dict[str, ActionMemory] = Field(default_factory=dict)
    action_events: list[ActionEvent] = Field(default_factory=list)
    advice_history: list[AdviceArchive] = Field(default_factory=list)
    clarification: str = ''
    notice: str = ''
    last_question: str | None = None
