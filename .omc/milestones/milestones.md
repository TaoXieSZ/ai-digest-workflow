
### [Progress] Dashboard UI PR1-PR2 completed

- **时间**: 2026-05-06T21:35:00+08:00
- **项目**: ai-digest-workflow
- **上下文**: Implemented the first local dashboard UI slices after the backend handoff.
- **内容**: Added an isolated FastAPI/Jinja UI under `ui/`, with read-only SQLite helpers and `/digest` views for daily digest, event batch, topics, recent dates, and event radar items. Verified against the local `data/items.db` without modifying `src/digest/*`.
- **收获**: The UI can now evolve independently while preserving the stable backend and using the database as the integration contract.

### [Progress] 飞书日历同步链路上线（PR-cal-1..5）

- **时间**: 2026-05-06T22:10+08:00
- **项目**: ai-digest-workflow
- **上下文**: dashboard UI 完成后，把 kind='event' 的活动从 SQLite 自动同步到飞书自建应用的 Moltbot 主日历。
- **内容**:
  - `src/digest/feishu_calendar.py`：tenant_access_token + 列日历 + 创建 all-day event（独立 client，httpx，FeishuCalendarError 包错）。
  - `src/digest/calendar_sync.py`：用 Protocol 抽象 client（测试可替）；description 里塞 报名截止 / 报名链接 / 地点 / 原文 URL。
  - `feishu_calendar_events` 表 + `record_calendar_sync` / `is_calendar_synced` / `get_unsynced_calendar_events`：DB 层幂等性，ledger 不在表里就当未推。
  - `scripts/run_calendar_sync.py`：默认 dry-run，`--confirm` 才真发；每个 event 独立 commit，单条失败不污染其他。
  - `com.txie.ai-digest.calendar` launchd 每小时跑一次（StartInterval=3600，跟 radar 同频但不同步，不会重复推）。
  - dashboard `/digest` 加 Feishu Calendar Sync 面板（synced today + pending upcoming，table 不存在时优雅降级）。
  - 整个 PR 没碰任何已有后端代码 / 已有表 / 已有测试；227 tests 全过（从 193 加了 34 条新测试）。
- **收获**: 给外部 SaaS 写 sync 时，"DB ledger + 默认 dry-run + 单条独立 commit" 三件套很扎实——重跑安全、第一次 push 可逆、单条失败可重试。Protocol 抽象 client 让 sync 模块完全脱网测试。

### [Pitfall] `set -a; source .env` 污染 pytest 进程导致 classifier 测试假失败

- **时间**: 2026-05-06T22:00+08:00
- **项目**: ai-digest-workflow
- **上下文**: 跑 `scripts/feishu_list_calendars.py` 之前在同一个 shell 里 `set -a && source .env`，再跑 pytest 时 `LLM_MODEL=deepseek-chat` 被注入子进程，覆盖了 test 的 `monkeypatch.setenv("ANTHROPIC_MODEL", ...)` —— 因为 classifier 优先读 `LLM_MODEL`。
- **内容**: 现象是 `test_classifier_uses_default_model_from_env` 单独跑过、整套跑挂；clean env (`env -i PATH=$PATH HOME=$HOME pytest`) 立刻全过。
- **收获**: 任何 source .env 之后再跑测试都要警惕环境污染。最佳实践：CLI 类脚本直接 `python script.py`（用 click 自己读 env）+ pytest 永远在 clean env 里跑；或者把 `.env` 加载封到一个子 shell 里：`(set -a; source .env; set +a; python script.py)`。

