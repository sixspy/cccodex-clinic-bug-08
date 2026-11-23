"""配额闸假绿修复（甲路：物化包 + 切流账册，读侧专认物化包且拒回刷）。

验收口径：
1. 落台提交后配额闸必须真绿：读轨切到 cutover_book、指纹非空且落库、拒回刷=1；
2. 物化包按中心记录真实已用配额，徽标由物化包实况计算，禁止假绿；
3. 读侧只读：任何 dashboard/snap/list 调用不得改动落库状态；
4. 超配额抓取必须被配额闸挡下（对齐状态下超配额、未对齐状态下一律先挡）；
5. 提交须即时持久化；落库失败必须抛错且不留半截状态；
6. 并发争写必须加锁，只允许一次提交成功；
7. 中心列表页带配额徽标与真实已用配额（live/pack 两列）。
"""
from __future__ import annotations

import sqlite3
import threading

import pytest

from app.db import get_db
from app.services import quota_gate as gate
from app.services import sites as sites_svc
from app.services import visits as visits_svc
from app.services.errors import ServiceError


def _meta(db_path: str, key: str) -> str:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key=?", (f"quota_gate_{key}",)
        ).fetchone()
        return row["value"] if row else ""


def _open_batch(db_path: str) -> dict:
    site = next(s for s in sites_svc.list_sites(db_path) if s["status"] == "启用")
    return visits_svc.create_batch(site["id"], note="配额闸测试批", db_path=db_path), site


# ---------- 1. 落台即真绿，命运骨架全部落库 ----------

def test_commit_makes_gate_truly_green_and_durable(db_path):
    result = gate.commit_action({"ref": 1}, db_path=db_path)

    assert result["ok"] is True
    assert _meta(db_path, "active_read") == gate.CUTOVER
    assert _meta(db_path, "reject_refresh") == "1"
    fp = _meta(db_path, "cutover_fp")
    assert fp and fp != "stale-fp"
    # 提交即时落库：另开连接读得到同一指纹
    assert gate.cutover_fp(db_path) == fp
    snap = gate.snapshot(db_path)
    assert snap["aligned"] is True
    assert snap["truth"] == gate.OK
    assert snap["panel_chip"] == gate.OK
    assert snap["gate_bypass"] is False


def test_commit_writes_materialized_pack_per_site(db_path):
    gate.commit_action({}, db_path=db_path)
    pack = gate.pack_rows(db_path)
    # 每个中心一条物化包，且没有 decoy 占位
    site_count = len(sites_svc.list_sites(db_path))
    durable = [r for r in pack if not r["key"].endswith("_decoy")]
    assert len(durable) == site_count
    parsed = gate.site_pack_map(db_path)
    site = sites_svc.list_sites(db_path)[0]
    assert site["id"] in parsed
    entry = parsed[site["id"]]
    assert entry["quota"] == site["quota"]
    assert entry["used_live"] == entry["used_pack"]
    assert entry["used_live"] >= 0


def test_archive_rows_written_on_commit(db_path):
    before = len(gate.archive_rows(db_path))
    gate.commit_action({}, db_path=db_path)
    after = len(gate.archive_rows(db_path))
    assert after > before


# ---------- 2. 假绿必须被戳破：漂移后徽标转红，直到重新落台 ----------

def test_drift_after_crawl_turns_chip_red_until_recommit(db_path):
    gate.commit_action({}, db_path=db_path)
    assert gate.snapshot(db_path)["aligned"] is True

    batch, site = _open_batch(db_path)
    gate.commit_action({}, db_path=db_path)  # 新批次入册后重新收口
    # 抓取改变真实已用配额，物化包停在旧值
    visits_svc.mock_crawl(batch["id"], count=3, mix_bad=False, db_path=db_path)

    snap = gate.snapshot(db_path)
    assert snap["aligned"] is False
    assert snap["truth"] == gate.BAD
    assert snap["panel_pack_mismatch"] is True

    # 重新落台：物化包追上实况，恢复真绿
    gate.commit_action({}, db_path=db_path)
    snap2 = gate.snapshot(db_path)
    assert snap2["aligned"] is True
    assert snap2["truth"] == gate.OK
    entry = gate.site_pack_map(db_path)[site["id"]]
    assert entry["used_pack"] == entry["used_live"]


def test_drift_does_not_block_first_write_but_keeps_red_badge(db_path):
    # 漂移不拦让漂移发生的当笔写入（否则抓取无法起步），但徽标必须转红
    gate.commit_action({}, db_path=db_path)
    batch, _ = _open_batch(db_path)
    gate.commit_action({}, db_path=db_path)
    out = visits_svc.mock_crawl(batch["id"], count=1, mix_bad=False, db_path=db_path)
    assert out["created"] == 1
    assert gate.snapshot(db_path)["aligned"] is False
    assert gate.truth_flag(db_path) == gate.BAD


# ---------- 3. 读侧专认物化包、只读且拒回刷 ----------

def test_read_apis_are_read_only(db_path):
    gate.commit_action({}, db_path=db_path)
    fp_before = _meta(db_path, "cutover_fp")
    counter_before = _meta(db_path, "counter")

    gate.snapshot(db_path)
    gate.dashboard(db_path)
    gate.panel_chip(db_path)
    gate.truth_flag(db_path)
    gate.active_read_ref(db_path)
    gate.pack_rows(db_path)
    gate.archive_rows(db_path)
    gate.list_events(db_path)

    assert _meta(db_path, "cutover_fp") == fp_before
    assert _meta(db_path, "counter") == counter_before
    # 已落台后读轨不得退回旧轨
    assert _meta(db_path, "active_read") == gate.CUTOVER


def test_reject_refresh_blocks_old_track_writeback(db_path):
    gate.commit_action({}, db_path=db_path)
    # 任何“只亮芯片”的半修都不得把读轨刷回 old_track
    gate.paint_ok(db_path)
    gate.half_fix_display(db_path)
    gate.reconcile_display(db_path)
    assert _meta(db_path, "active_read") == gate.CUTOVER
    assert _meta(db_path, "reject_refresh") == "1"


def test_seed_skew_state_is_not_green(db_path):
    # 种子歪斜态（未落台）必须明确是红的，不能假装已亮
    gate.seed_skew(db_path)
    snap = gate.snapshot(db_path)
    assert snap["aligned"] is False
    assert snap["truth"] == gate.BAD
    assert snap["gate_bypass"] is False


# ---------- 4. 超配额抓取被配额闸挡下 ----------

def test_over_quota_crawl_blocked_when_aligned(db_path):
    # 选一个小配额中心，把它压到配额边缘
    site = sites_svc.create_site(
        "配额闸小中心", quota=2, region="测试区", principal="测试员",
        note="", db_path=db_path,
    )
    batch = visits_svc.create_batch(site["id"], db_path=db_path)
    gate.commit_action({}, db_path=db_path)  # 收口，配额=2、已用=0

    visits_svc.mock_crawl(batch["id"], count=2, mix_bad=False, db_path=db_path)
    gate.commit_action({}, db_path=db_path)  # 已用=2=配额，仍对齐

    with pytest.raises(ServiceError) as exc:
        visits_svc.mock_crawl(batch["id"], count=5, mix_bad=False, db_path=db_path)
    assert exc.value.status_code == 409
    # 被挡后业务库没有任何新行落进去
    detail = visits_svc.get_batch(batch["id"], db_path)
    assert detail["item_count"] == 2


def test_crawl_blocked_when_gate_unaligned(db_path):
    # 小配额中心：收口后抓取造成漂移，继续放量到配额上限即被硬挡
    site = sites_svc.create_site(
        "漂移闸小中心", quota=2, region="测试区", principal="测试员",
        note="", db_path=db_path,
    )
    batch = visits_svc.create_batch(site["id"], db_path=db_path)
    gate.commit_action({}, db_path=db_path)  # 已用=0、配额=2、对齐

    visits_svc.mock_crawl(batch["id"], count=2, mix_bad=False, db_path=db_path)
    # 此刻已用=2=配额，物化包仍停在 0（漂移、徽标转红）
    assert gate.snapshot(db_path)["aligned"] is False
    before = visits_svc.get_batch(batch["id"], db_path)["item_count"]
    with pytest.raises(ServiceError) as exc:
        visits_svc.mock_crawl(batch["id"], count=2, mix_bad=False, db_path=db_path)
    assert exc.value.status_code == 409
    # 被挡后不产生半截新行
    assert visits_svc.get_batch(batch["id"], db_path)["item_count"] == before


def test_manual_row_also_enforces_gate(db_path):
    site = sites_svc.create_site(
        "手工闸小中心", quota=1, region="测试区", principal="测试员",
        note="", db_path=db_path,
    )
    batch = visits_svc.create_batch(site["id"], db_path=db_path)
    gate.commit_action({}, db_path=db_path)
    visits_svc.add_visit_row(
        batch["id"], "GK-0001", "V1",
        payload='{"visit_date":"2026-11-20","investigator":"医生"}',
        db_path=db_path,
    )
    # 已用达配额上限，再手工追加必挡
    with pytest.raises(ServiceError) as exc:
        visits_svc.add_visit_row(
            batch["id"], "GK-0002", "V1",
            payload='{"visit_date":"2026-11-20","investigator":"医生"}',
            db_path=db_path,
        )
    assert exc.value.status_code == 409


# ---------- 5. 落库失败必须说清楚，且不留半截 ----------

def test_commit_persistence_failure_raises_and_rolls_back(db_path, monkeypatch):
    real_meta_set = gate._meta_set

    def boom(conn, key, value):
        if key == "quota_gate_cutover_fp":
            raise sqlite3.OperationalError("disk I/O error")
        real_meta_set(conn, key, value)

    monkeypatch.setattr(gate, "_meta_set", boom)
    with pytest.raises(ServiceError) as exc:
        gate.commit_action({}, db_path=db_path)
    assert exc.value.status_code == 500
    # 回滚：没有半截指纹、读轨没有被切成 cutover（保持旧轨语义）
    assert _meta(db_path, "cutover_fp") in ("", "stale-fp")
    assert _meta(db_path, "active_read") != gate.CUTOVER
    assert gate.active_read_ref(db_path) == gate.OLD


# ---------- 6. 争用加锁：并发提交只许一次成功，钉点不被踩掉 ----------

def test_concurrent_commits_are_serialized(db_path):
    errors: list[Exception] = []
    results: list[dict] = []

    def worker() -> None:
        try:
            results.append(gate.commit_action({"by": threading.get_ident()}, db_path))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 全部提交被串行消化：无错误、计数=8、版本号连续到 8
    assert errors == []
    assert len(results) == 8
    assert int(_meta(db_path, "counter")) == 8
    assert int(_meta(db_path, "lock_version")) == 8
    # 内容指纹只由当刻真实数据决定：所有提交看到同一份数据，钉点唯一
    assert len({r["cutover_fp"] for r in results}) == 1
    assert _meta(db_path, "active_read") == gate.CUTOVER
    snap = gate.snapshot(db_path)
    assert snap["aligned"] is True


def test_concurrent_commit_conflict_reports_409(db_path):
    # 两个仓管：后到者持陈旧 expected_version 提交，必须收 409 而不是双成功
    first = gate.commit_action({}, db_path=db_path)
    stale_version = first["lock_version"] - 1

    with pytest.raises(ServiceError) as exc:
        gate.commit_action(
            {"expected_version": stale_version}, db_path=db_path
        )
    assert exc.value.status_code == 409
    # 被挡后版本号没有被这次提交推进
    assert int(_meta(db_path, "lock_version")) == first["lock_version"]


def test_cross_connection_stale_write_loses(db_path):
    # 模拟第二连接基于旧版本号抢写：乐观守卫直接挡回
    first = gate.commit_action({}, db_path=db_path)
    with pytest.raises(ServiceError) as exc:
        gate.commit_action(
            {"expected_version": first["lock_version"] - 1}, db_path=db_path
        )
    assert exc.value.status_code == 409


# ---------- 7. 中心列表页后备：真实已用配额随接口下发 ----------

def test_site_list_carries_live_and_pack_quota(db_path):
    gate.commit_action({}, db_path=db_path)
    items = sites_svc.list_sites(db_path)
    site = items[0]
    assert "used_live" in site and "used_pack" in site and "quota_state" in site
    assert site["used_live"] >= 0
    assert site["used_pack"] == site["used_live"]

    batch = visits_svc.create_batch(site["id"], db_path=db_path)
    visits_svc.mock_crawl(batch["id"], count=1, mix_bad=False, db_path=db_path)
    drifted = next(s for s in sites_svc.list_sites(db_path) if s["id"] == site["id"])
    assert drifted["used_live"] > drifted["used_pack"]
    assert drifted["quota_state"] == gate.BAD


def test_site_quota_state_green_means_within_quota(db_path):
    gate.commit_action({}, db_path=db_path)
    for s in sites_svc.list_sites(db_path):
        if s["quota_state"] == gate.OK:
            assert s["used_pack"] <= s["quota"]
