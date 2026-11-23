"""配额钉定（甲路：切流账册 + 物化包）。

收口形态：
- 落台提交在单事务内把每个中心的真实已用配额物化为 pack 行，锁当刻指纹，
  读轨切到 cutover_book，并置拒回刷；旧轨状态写入 archive 后不再被读侧采信。
- 读侧（snapshot/dashboard/pack_rows 等）只读，绝不回写 meta；徽标由物化包与
  业务库实况比对计算，漂移即红，禁止“芯片亮、读轨旧”的假绿。
- 提交经进程锁串行 + lock_version 乐观守卫，争写时后到者收 409，不会双成功。
- 入库失败抛 ServiceError(500) 并随事务回滚，不留半截状态。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading

from app.db import get_db
from app.services.errors import ServiceError

_SLUG = "quota_gate"
_OK = "配额已亮"
_BAD = "已用配额旧"
_TITLE = "配额闸"
_OLD = "old_track"
_CUTOVER = "cutover_book"
_LIVE = "live_flag"
_MODE = "cutover_book"

# 对外常量，便于服务层与测试引用
OK = _OK
BAD = _BAD
OLD = _OLD
CUTOVER = _CUTOVER
LIVE = _LIVE

# 落台提交串行锁（进程内）；跨连接/跨进程由 SQLite BEGIN IMMEDIATE 事务锁兜底
_commit_lock = threading.Lock()


def _meta_get(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _meta_set(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (key, value),
    )


def _full_key(name: str) -> str:
    return f"{_SLUG}_{name}"


def _pack_key(site_id: int) -> str:
    return _full_key(f"pack_{site_id}")


# ---------------- 业务库实况 ----------------

def _live_usage(conn) -> dict[int, dict]:
    """每个中心的真实已用配额（访视行计数）与配额上限。"""
    rows = conn.execute(
        """
        SELECT s.id AS site_id, s.site_code, s.quota AS quota,
               COUNT(r.id) AS used_live
        FROM trial_sites s
        LEFT JOIN visit_batches b ON b.site_id = s.id
        LEFT JOIN visit_rows r ON r.batch_id = b.id
        GROUP BY s.id
        ORDER BY s.id
        """
    ).fetchall()
    return {
        r["site_id"]: {
            "site_id": r["site_id"],
            "site_code": r["site_code"],
            "quota": r["quota"],
            "used_live": r["used_live"],
        }
        for r in rows
    }


def site_pack_map(db_path: str | None = None) -> dict[int, dict]:
    """读侧专用：物化包内容（未收口时为空映射）。"""
    packs: dict[int, dict] = {}
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_pack_%",),
        ).fetchall()
        for r in rows:
            try:
                entry = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                continue
            sid = entry.get("site_id")
            if isinstance(sid, int):
                packs[sid] = entry
    return packs


def site_states(db_path: str | None = None) -> list[dict]:
    """中心列表页后备：实况、物化包与徽标态一次性下发。"""
    with get_db(db_path) as conn:
        live = _live_usage(conn)
        packs = {}
        for r in conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ?",
            (f"{_SLUG}_pack_%",),
        ).fetchall():
            try:
                entry = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(entry.get("site_id"), int):
                packs[entry["site_id"]] = entry
        active_read = _meta_get(conn, _full_key("active_read"), _OLD)
    out = []
    for sid, info in live.items():
        pack = packs.get(sid)
        used_pack = pack["used_pack"] if pack else info["used_live"]
        if active_read == _OLD:
            state = "未收口"
        elif info["used_live"] != used_pack:
            state = _BAD
        elif info["used_live"] > info["quota"]:
            state = _BAD
        else:
            state = _OK
        out.append(
            {
                "site_id": sid,
                "site_code": info["site_code"],
                "quota": info["quota"],
                "used_live": info["used_live"],
                "used_pack": used_pack,
                "quota_state": state,
            }
        )
    return out


# ---------------- 只读访问面 ----------------

def panel_chip(db_path: str | None = None) -> str:
    with get_db(db_path) as conn:
        return _meta_get(conn, _full_key("panel_chip"), _BAD)


def truth_flag(db_path: str | None = None) -> str:
    return _OK if snapshot(db_path)["aligned"] else _BAD


def active_read_ref(db_path: str | None = None) -> str:
    with get_db(db_path) as conn:
        return _meta_get(conn, _full_key("active_read"), _OLD)


def cutover_fp(db_path: str | None = None) -> str:
    with get_db(db_path) as conn:
        return _meta_get(conn, _full_key("cutover_fp"), "")


def reject_refresh(db_path: str | None = None) -> bool:
    with get_db(db_path) as conn:
        return _meta_get(conn, _full_key("reject_refresh"), "0") == "1"


def live_flag(db_path: str | None = None) -> str:
    with get_db(db_path) as conn:
        return _meta_get(conn, _full_key("live_flag"), "")


def archive_rows(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_archive_%",),
        ).fetchall()
        return [{"key": r["key"], "value": r["value"]} for r in rows]


def pack_rows(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_pack_%",),
        ).fetchall()
        return [{"key": r["key"], "value": r["value"]} for r in rows]


def list_events(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_evt_%",),
        ).fetchall()
        return [{"key": r["key"], "note": r["value"]} for r in rows]


def snapshot(db_path: str | None = None) -> dict:
    """纯读：比对物化包与业务库实况，算出对齐态与真假绿。"""
    with get_db(db_path) as conn:
        chip = _meta_get(conn, _full_key("panel_chip"), _BAD)
        ref = _meta_get(conn, _full_key("active_read"), _OLD)
        fp = _meta_get(conn, _full_key("cutover_fp"), "")
        reject = _meta_get(conn, _full_key("reject_refresh"), "0") == "1"
        flag = _meta_get(conn, _full_key("live_flag"), "")
        mode = _meta_get(conn, _full_key("mode"), "")
        pack_entries = {}
        for r in conn.execute(
            "SELECT value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_pack_%",),
        ).fetchall():
            try:
                entry = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(entry.get("site_id"), int):
                pack_entries[entry["site_id"]] = entry
        live = _live_usage(conn)
        archive_count = conn.execute(
            "SELECT COUNT(*) AS c FROM meta WHERE key LIKE ?",
            (f"{_SLUG}_archive_%",),
        ).fetchone()["c"]

    packs_match = set(pack_entries.keys()) == set(live.keys())
    over_quota = False
    if packs_match:
        for sid, info in live.items():
            entry = pack_entries[sid]
            if entry.get("used_pack") != info["used_live"]:
                packs_match = False
                break
            if info["used_live"] > info["quota"]:
                over_quota = True

    aligned = (
        ref == _CUTOVER
        and reject
        and fp not in ("", "stale-fp")
        and packs_match
        and not over_quota
    )
    truth = _OK if aligned else _BAD
    return {
        "title": _TITLE,
        "panel_chip": chip,
        "truth": truth,
        "pack_count": len(pack_entries),
        "active_read": ref,
        "cutover_fp": fp,
        "reject_refresh": reject,
        "archive_count": archive_count,
        "live_flag": flag,
        "mode": mode,
        "aligned": aligned,
        "gate_bypass": False,
        "panel_pack_mismatch": chip == _OK and not aligned,
        "site_states": [
            {
                **info,
                "used_pack": pack_entries.get(sid, {}).get("used_pack", info["used_live"]),
            }
            for sid, info in sorted(live.items())
        ],
    }


def dashboard(db_path: str | None = None) -> dict:
    snap = snapshot(db_path)
    return {
        "title": _TITLE,
        "chip": snap["truth"],
        "panel_chip": snap["panel_chip"],
        "truth": snap["truth"],
        "pack_count": snap["pack_count"],
        "durable_count": snap["pack_count"],
        "rows": pack_rows(db_path),
        "active_read": snap["active_read"],
        "cutover_fp": snap["cutover_fp"],
        "reject_refresh": snap["reject_refresh"],
        "archive_count": snap["archive_count"],
        "live_flag": snap["live_flag"],
        "mode": snap["mode"],
        "aligned": snap["aligned"],
        "gate_bypass": False,
        "panel_pack_mismatch": snap["panel_pack_mismatch"],
        "site_states": snap["site_states"],
        "ok_chip": _OK,
        "bad_chip": _BAD,
        "label": "配额钉定",
        "read_tracks": {
            "old_track": _OLD,
            "cutover_book": _CUTOVER,
            "live_flag": _LIVE,
        },
    }


# ---------------- 初始化与展示面（不得改读轨） ----------------

def seed_skew(db_path: str | None = None) -> None:
    """种子歪斜态：芯片假绿、读轨仍旧、闸默认关闭。"""
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
            (_full_key("active_read"), _OLD),
        )
        _meta_set(conn, _full_key("panel_chip"), _OK)
        _meta_set(conn, _full_key("truth"), _BAD)
        _meta_set(conn, _full_key("counter"), "0")
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES (?, '0')",
            (_full_key("lock_version"),),
        )
        _meta_set(conn, _full_key("gate_bypass"), "0")
        _meta_set(conn, _full_key("active_read"), _OLD)
        _meta_set(conn, _full_key("cutover_fp"), "")
        _meta_set(conn, _full_key("reject_refresh"), "0")
        _meta_set(conn, _full_key("live_flag"), "")
        _meta_set(conn, _full_key("mode"), "")
        _meta_set(conn, _full_key("old_track"), "pending|stale")


def paint_ok(db_path: str | None = None) -> str:
    """仅展示面亮灯；拒回刷开启后不得改读轨、不得碰指纹。"""
    with get_db(db_path) as conn:
        if _meta_get(conn, _full_key("reject_refresh"), "0") == "1":
            return _meta_get(conn, _full_key("panel_chip"), _OK)
        _meta_set(conn, _full_key("panel_chip"), _OK)
        _meta_set(conn, _full_key("display_only"), "1")
    return _OK


def half_fix_display(db_path: str | None = None) -> dict:
    paint_ok(db_path)
    return {
        "panel_chip": panel_chip(db_path),
        "truth": truth_flag(db_path),
        "active_read": active_read_ref(db_path),
        "archive_count": len(archive_rows(db_path)),
        "half_fixed": True,
    }


def reconcile_display(db_path: str | None = None) -> dict:
    return half_fix_display(db_path)


def record_event(note: str, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        c = int(_meta_get(conn, _full_key("counter"), "0") or "0") + 1
        _meta_set(conn, _full_key("counter"), str(c))
        _meta_set(conn, _full_key(f"evt_{c}"), note or _BAD)
    return dashboard(db_path)


# ---------------- 落台提交（甲路唯一写入口） ----------------

def commit_action(payload: dict | None = None, db_path: str | None = None) -> dict:
    """单事务物化 + 切流 + 锁指纹；SQLite 事务锁串行 + 乐观版本守卫。

    指纹按当刻物化内容计算，内容不变则钉点不漂移；版本号在锁内以库内真实值
    比对，后到的陈旧提交收 409，不会双成功把钉点踩掉。
    """
    payload = payload or {}
    expected_version = payload.get("expected_version")
    with _commit_lock:
        from pathlib import Path

        from app.config import Config

        db_file = Path(db_path) if db_path else Config.DATABASE_PATH
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            # IMMEDIATE 立即拿写锁：其余仓管在此排队，争写被串行化
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES (?, '0')",
                (_full_key("lock_version"),),
            )
            held_version = int(
                _meta_get(conn, _full_key("lock_version"), "0") or "0"
            )
            if expected_version is not None and int(expected_version) != held_version:
                conn.rollback()
                raise ServiceError(
                    "配额闸已被其他仓管更新（版本过期），请刷新后重新落台", 409
                )

            live = _live_usage(conn)
            counter = int(
                _meta_get(conn, _full_key("counter"), "0") or "0"
            ) + 1

            # 旧轨状态归档（只读历史），不再被读侧采信
            old_ref = _meta_get(conn, _full_key("active_read"), _OLD)
            old_fp = _meta_get(conn, _full_key("cutover_fp"), "")
            _meta_set(
                conn,
                _full_key(f"archive_{counter}"),
                json.dumps(
                    {
                        "active_read": old_ref,
                        "cutover_fp": old_fp,
                        "payload": str(payload)[:120],
                    },
                    ensure_ascii=False,
                ),
            )

            canonical = json.dumps(
                [{"site_id": sid, **info} for sid, info in sorted(live.items())],
                ensure_ascii=False,
                sort_keys=True,
            )
            # 内容指纹：只由当刻真实已用配额决定，与提交者/次数无关
            fp = "fp-" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]

            # 清掉旧物化包后按当刻实况重写
            conn.execute(
                "DELETE FROM meta WHERE key LIKE ?", (f"{_SLUG}_pack_%",)
            )
            for sid, info in sorted(live.items()):
                entry = {
                    "site_id": sid,
                    "site_code": info["site_code"],
                    "quota": info["quota"],
                    "used_live": info["used_live"],
                    "used_pack": info["used_live"],
                    "state": _OK if info["used_live"] <= info["quota"] else _BAD,
                    "fp": fp,
                }
                _meta_set(conn, _pack_key(sid), json.dumps(entry, ensure_ascii=False))

            _meta_set(conn, _full_key("counter"), str(counter))
            _meta_set(conn, _full_key("lock_version"), str(held_version + 1))
            _meta_set(conn, _full_key("cutover_fp"), fp)
            _meta_set(conn, _full_key("active_read"), _CUTOVER)
            _meta_set(conn, _full_key("reject_refresh"), "1")
            _meta_set(conn, _full_key("gate_bypass"), "0")
            _meta_set(conn, _full_key("mode"), _MODE)
            _meta_set(conn, _full_key("panel_chip"), _OK)
            _meta_set(conn, _full_key("truth"), _OK)
            _meta_set(conn, _full_key("live_flag"), "")
            _meta_set(conn, _full_key("last_payload"), str(payload)[:200])
            _meta_set(conn, _full_key(f"evt_{counter}"), f"落台提交 fp={fp}")
            conn.commit()
        except ServiceError:
            conn.rollback()
            conn.close()
            raise
        except sqlite3.Error as exc:
            conn.rollback()
            conn.close()
            raise ServiceError(
                f"配额闸入库失败，已回滚未落任何半截状态：{exc}", 500
            )
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            conn.close()
            raise ServiceError(
                f"配额闸入库失败，已回滚未落任何半截状态：{exc}", 500
            )
        else:
            conn.close()

    return {
        "ok": True,
        "chip": _OK,
        "title": _TITLE,
        "counter": counter,
        "lock_version": held_version + 1,
        "active_read": active_read_ref(db_path),
        "cutover_fp": cutover_fp(db_path),
        "reject_refresh": reject_refresh(db_path),
        "archive_count": len(archive_rows(db_path)),
        "live_flag": live_flag(db_path),
        "aligned": snapshot(db_path)["aligned"],
        "message": f"{_TITLE}已落台",
    }


# ---------------- 业务落点闸 ----------------

def gate_allows_pipeline(db_path: str | None = None) -> bool:
    snap = snapshot(db_path)
    if snap["active_read"] == _OLD:
        # 闸尚未收口：沿用旧轨行为，不拦既有主链
        return True
    return bool(snap["aligned"])


def ensure_site_writable(site_id: int, db_path: str | None = None) -> None:
    """抓取/写入业务落点前的配额闸校验。

    - 漂移（徽标与已用配额对不上）：让徽标转红提示重新落台，但不拦让漂移发生
      的当笔写入——否则正常抓取永远迈不出第一步；漂移期间继续抓取会在越过
      配额上限时被下面的硬挡截住，且页面红色徽标已要求先收口。
    - 超配额：已用配额达上限仍要写入，一律 409 硬挡，杜绝超配额抓取灌脏。
    - 未收口（旧轨）：不拦既有主链。
    """
    snap = snapshot(db_path)
    if snap["active_read"] == _OLD:
        return
    entry = next(
        (s for s in snap["site_states"] if s["site_id"] == site_id), None
    )
    if entry is None:
        raise ServiceError("配额闸缺少该中心的物化包，已挡下抓取", 409)
    if entry["used_live"] >= entry["quota"]:
        raise ServiceError(
            f"中心 {entry['site_code']} 已用配额 {entry['used_live']} 达上限 "
            f"{entry['quota']}，配额闸挡下超配额抓取",
            409,
        )


def require_pack_or_raise(db_path: str | None = None) -> None:
    snap = snapshot(db_path)
    if not snap["aligned"]:
        raise ServiceError(f"{_TITLE}读轨未对齐，禁止放行", 409)
