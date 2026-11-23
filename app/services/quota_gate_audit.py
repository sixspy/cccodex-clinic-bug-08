"""配额闸读轨审计：纯读汇总切流账册（甲路）与双写骨架（乙路未选）。"""
from __future__ import annotations

from app.db import get_db
from app.services.quota_gate_cutover import board as cutover_board
from app.services.quota_gate_dualwrite import dual_board, half_dual


def audit_list(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            ("quota_gate_evt_%",),
        ).fetchall()
        return [{"key": r["key"], "value": r["value"]} for r in rows]


def audit_summary(db_path: str | None = None) -> dict:
    return {
        "title": "配额闸审计",
        "events": audit_list(db_path),
        "cutover": cutover_board(db_path),
        "dualwrite": dual_board(db_path),
        "half": half_dual(db_path),
    }
