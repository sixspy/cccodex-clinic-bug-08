# cc-clinic-lab · 临床试验访视采集台

数据工程产品线基线。题号前缀 `clinic-*`，默认端口 **7600**。

## 技术栈

- 后端：CherryPy + SQLite
- 前端：原生 JS SPA（顶栏薄荷绿丝带导航）
- 会话：itsdangerous 签名 Cookie
- 测试：pytest + httpx（依赖已列入 requirements）

## 登录

- 地址：`http://127.0.0.1:7600/login`
- 账号：`admin` / `123456`

## 页面

| 页面 | 能力 |
|------|------|
| 试验中心 | 中心登记、启停/观察、配额、方案版本生效 |
| 访视批次 | 开批、模拟抓取、追加访视行、合格入库、收齐/关闭 |
| 字段质控 | 按批/全量扫描、发现关闭/重开 |
| 缺口补采 | 排队、执行成功/失败、失败重试 |
| 访视对账 | 缺口/发现/补采对照、中心矩阵、审计 |
| 台参数 | 设置读写 |

## 主链

中心 → 访视批次抓取 → 质控发现 → 缺口补采 → 对账报告；设置与审计贯穿写操作。

## 配额闸（clinic-bug-08 收口）

配额闸解决“徽标亮（配额已亮）但读轨仍旧（已用配额旧）”的假绿问题，已拍板**甲路**：

- **切流账册 + 物化包**：落台提交在单事务（`BEGIN IMMEDIATE`）内把每个中心的真实已用
  配额物化为 `quota_gate_pack_<site_id>` 行，锁内容指纹 `cutover_fp`，读轨切到
  `cutover_book`，置 `reject_refresh=1`；旧轨状态写入 `quota_gate_archive_*` 只读历史。
- **读侧专认物化包且只读**：`snapshot/dashboard/pack_rows` 等只读，绝不回写；徽标由
  物化包与业务库实况比对计算，抓取造成漂移即转红（`已用配额旧`），重新落台才恢复真绿。
- **拒回刷**：`paint_ok/half_fix_display` 在拒回刷开启后不能改读轨、不能碰指纹。
- **业务落点硬挡超配额**：`visits.add_visit_row`（含模拟抓取）写入前过
  `ensure_site_writable`，已用配额达上限一律 409，杜绝超配额抓取灌脏。
- **争用加锁**：进程锁 + SQLite 事务锁串行化，`lock_version` 乐观守卫让陈旧提交收 409，
  不会双成功把钉点踩掉；入库失败抛 500 并随事务回滚，不留半截。
- **乙路（双写两轨）未选边**：保留只读命运骨架，任何写入请求返回 409，不与甲路并存。
- 中心列表页承载配额闸命运卡与“落台收口”按钮，并展示每个中心的真实已用配额、物化包
  已用配额与配额徽标三列；设置页仅保留只读骨架。

## 本地

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest
python run.py
```

Docker：`docker compose up -d --build`，健康检查打 `/login`。

## 后续题面预留切口

- 访视行跨中心去重与冲突裁决
- 方案换版后存量批次字段兼容扫描
- 补采分片与并发上限
- 对账差异工单与导出
