"""配额闸双写两轨（乙路骨架，未选边）。

本仓已拍板甲路（切流账册 + 物化包，读侧专认物化包且拒回刷）。乙路只保留只读
命运骨架，任何写入请求都明确拒绝，避免与甲路并存把读轨拧花。
"""
from __future__ import annotations

from app.services.errors import ServiceError
from app.services.quota_gate import (
    active_read_ref,
    archive_rows,
    dashboard,
    live_flag,
    truth_flag,
)

_TITLE = "配额闸"


def dual_chip(db_path: str | None = None) -> str:
    # 只读：直接反映甲路真徽标，不再单独维护镜像芯片
    return truth_flag(db_path)


def apply_dualwrite(note: str = "", db_path: str | None = None) -> dict:
    raise ServiceError(
        "乙路（双写两轨迁入只读历史）未选中：本仓配额闸已走甲路物化收口，"
        "禁止乙路并行写读轨；请改用切流账册落台",
        409,
    )


def dual_board(db_path: str | None = None) -> dict:
    dash = dashboard(db_path)
    return {
        "dual_chip": truth_flag(db_path),
        "truth": truth_flag(db_path),
        "active_read": active_read_ref(db_path),
        "archive": archive_rows(db_path),
        "live_flag": live_flag(db_path),
        "dash": dash,
        "fate": "dualwrite",
        "selected": False,
        "panel_pack_mismatch": dash["panel_pack_mismatch"],
        "title": f"{_TITLE}双写面（未选边·只读骨架）",
    }


def half_dual(db_path: str | None = None) -> dict:
    # 纯读，绝不亮镜像灯、绝不改读轨
    return {
        "dual_chip": truth_flag(db_path),
        "truth": truth_flag(db_path),
        "active_read": active_read_ref(db_path),
    }
