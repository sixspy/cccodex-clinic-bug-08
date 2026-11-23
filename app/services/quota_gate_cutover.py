"""配额闸切流账册（甲路收口面）。

落台即锁当刻指纹、拒回刷、读轨切到 cutover_book；本模块的账册写入统一走
quota_gate.commit_action 的单事务物化，不再只记软笔记。
"""
from __future__ import annotations

from app.db import get_db
from app.services.quota_gate import (
    _SLUG,
    active_read_ref,
    commit_action,
    cutover_fp,
    dashboard,
    panel_chip,
    reject_refresh,
    truth_flag,
)
from app.services.errors import ServiceError


def append_cutover(ref_id: int, note: str, db_path: str | None = None) -> dict:
    """切流账册落台：委托唯一写入口完成物化 + 指纹锁定 + 读轨切换。"""
    result = commit_action(
        {"ref_id": int(ref_id or 0), "note": note or "配额钉定"}, db_path
    )
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (f"{_SLUG}_cutover_note_{int(ref_id or 0)}", note or "配额闸"),
        )
    return {
        "ref_id": int(ref_id or 0),
        "note": note,
        "panel": panel_chip(db_path),
        "truth": truth_flag(db_path),
        "active_read": active_read_ref(db_path),
        "cutover_fp": cutover_fp(db_path),
        "reject_refresh": reject_refresh(db_path),
        "aligned": result["aligned"],
    }


def cutover_rows(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_cutover_%",),
        ).fetchall()
        return [
            {
                "key": r["key"],
                "value": r["value"],
                "chip": panel_chip(db_path),
                "active_read": active_read_ref(db_path),
            }
            for r in rows
        ]


def board(db_path: str | None = None) -> dict:
    dash = dashboard(db_path)
    dash["cutover"] = cutover_rows(db_path)
    dash["fate"] = "cutover_book"
    dash["panel_pack_mismatch"] = dash.get("panel_pack_mismatch", False)
    return dash
