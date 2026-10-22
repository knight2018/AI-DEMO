"""SPEC-005：SQLite 保存会话，版本号避免并发覆盖。"""
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from support_demo.domain import Session

class ConflictError(Exception):
    pass

class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, version INTEGER NOT NULL, payload TEXT NOT NULL)')

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, session_id: str) -> Session:
        with self.connect() as connection:
            row = connection.execute('SELECT payload FROM sessions WHERE id=?', (session_id,)).fetchone()
        if row is None:
            raise KeyError(session_id)
        return Session.model_validate_json(row[0])

    def create(self, session: Session) -> None:
        with self.connect() as connection:
            connection.execute('INSERT INTO sessions VALUES (?, ?, ?)', (session.id, session.version, session.model_dump_json()))

    def save(self, session: Session, expected: int) -> Session:
        updated = session.model_copy(update={'version': expected + 1})
        with self.connect() as connection:
            cursor = connection.execute('UPDATE sessions SET version=?, payload=? WHERE id=? AND version=?',
                                        (updated.version, updated.model_dump_json(), updated.id, expected))
            if cursor.rowcount != 1:
                raise ConflictError('session_changed')
        return updated
