"""下发工作台状态的 SQLite 持久化。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.dispatch.ledger import new_board


class DispatchStore:
    def __init__(self, path):
        self.memory = path == ":memory:"
        self.path = Path(path)
        if not self.memory:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._memory_connection = None
        else:
            self._memory_connection = sqlite3.connect(":memory:", timeout=8, check_same_thread=False)
            self._memory_connection.row_factory = sqlite3.Row
        self.init()

    def connect(self):
        if self.memory:
            return self._memory_connection
        connection = sqlite3.connect(str(self.path), timeout=8)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self):
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS dispatch_boards(
                  board_id TEXT PRIMARY KEY,
                  revision INTEGER NOT NULL,
                  state TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                """
            )

    def load(self, board_id: str = "default"):
        with self.connect() as db:
            row = db.execute("SELECT state FROM dispatch_boards WHERE board_id=?", (board_id,)).fetchone()
        if row is None:
            return new_board(board_id)
        state = json.loads(row["state"])
        state.setdefault("board_id", board_id)
        return state

    def save(self, board) -> None:
        board_id = board.get("board_id", "default")
        now = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(board, ensure_ascii=False)
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO dispatch_boards(board_id, revision, state, updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(board_id) DO UPDATE SET
                  revision=excluded.revision,
                  state=excluded.state,
                  updated_at=excluded.updated_at
                """,
                (board_id, int(board.get("revision", 0)), payload, now),
            )

    def transact(self, board_id: str, mutation: Callable[[dict], tuple[dict, object] | object]):
        """加载 board、执行变更并保存。

        mutation 可以返回 ``(board, result)`` 或仅返回 result；若返回的
        第一个值是 board dict，则保存它。
        """
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM dispatch_boards WHERE board_id=?", (board_id,)).fetchone()
            board = json.loads(row["state"]) if row else new_board(board_id)
            result = mutation(board)
            if isinstance(result, tuple) and result and isinstance(result[0], dict) and "groups" in result[0]:
                board, output = result
            else:
                output = result
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                """
                INSERT INTO dispatch_boards(board_id, revision, state, updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(board_id) DO UPDATE SET
                  revision=excluded.revision,
                  state=excluded.state,
                  updated_at=excluded.updated_at
                """,
                (board_id, int(board.get("revision", 0)), json.dumps(board, ensure_ascii=False), now),
            )
            return output

    def reset(self, board_id: str = "default") -> dict:
        board = new_board(board_id)
        self.save(board)
        return board

    def load_board(self, board_id: str = "default"):
        return self.load(board_id)

    def save_board(self, board) -> None:
        return self.save(board)

    def list_board_ids(self):
        with self.connect() as db:
            rows = db.execute("SELECT board_id FROM dispatch_boards ORDER BY board_id").fetchall()
        return [row["board_id"] for row in rows]
