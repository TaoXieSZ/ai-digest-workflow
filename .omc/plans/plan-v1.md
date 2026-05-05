# Plan v1 — AI Digest Workflow

> Iteration 1 / Planner draft（待 Architect + Critic review）

## RALPLAN-DR Summary

### Principles（5）

1. **中文网络环境兼容** — 全链路在深圳本地 / 国内云无 VPN 可跑（约束硬性来自 spec）
2. **失败隔离** — 单个源挂掉不应阻塞整个 pipeline，digest 应能"少一个源照常发"
3. **最简先行** — 个人自用项目，不上微服务/Kubernetes/消息队列；优先现成工具链
4. **可调试** — 每个阶段（fetch / parse / cluster / digest / push）可独立重跑，避免一处错就全链路重跑
5. **配置外置** — 源列表、Notion DB 字段、TG bot token、关键词、prompt 模板都在 yaml/env，不进代码

### Decision Drivers（top 3）

1. **抓取稳定性**（最大不确定性来自 XHS/微博反爬）
2. **运维成本**（个人维护，每月 ≤30 分钟人工干预为合格）
3. **LLM 成本/质量平衡**（每天 4 次轮询 × N 源 × 聚类调用，月成本目标 < ¥50）

### Viable Options

#### Option A: 纯 Python 本地 + launchd（推荐）

**架构**：单一 Python 项目 `ai-digest-workflow/`，launchd 定时器触发，SQLite 中转，Claude API 聚类，Notion + TG 出口。

| 阶段 | 实现 |
|------|------|
| 调度 | macOS launchd（4h cron，本地跑） |
| 抓取 | `httpx` + `BeautifulSoup` for V2EX/即刻/掘金/少数派/linux.do (有公开 web/RSS)；XHS/微博 用 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 做 best-effort |
| 中转 | SQLite（`items.db`），原始条目 + 主题分配 |
| 聚类 | Claude Haiku (cheap) 跑跨源主题聚类，每日一次；prompt 外置 yaml |
| 推送 | python-telegram-bot SDK |
| 归档 | `notion-client` SDK 写入 Notion DB |
| 配置 | `config.yaml` (源列表)、`.env`（API keys） |

**Pros**：
- 调试最简单（一个 venv 就跑）
- 失败隔离天然——每个源独立 fetcher，try/except 即可
- 成本可控（Haiku ¥<¥30/月预估）
- 完全离线可调试（SQLite 可断点重跑）

**Cons**：
- launchd 需要电脑开着；睡眠中会漏窗口
- 没有"团队订阅"扩展性（spec 已声明非目标，OK）

#### Option B: Claude Code agent + ScheduleWakeup + 文件系统

**架构**：Claude Code 写一个 agent prompt，由 ScheduleWakeup 定时唤醒，直接读 RSS / web 抓取，结果写 markdown，TG/Notion 通过 MCP 出口。

**Pros**：
- 无需自己写 LLM 调用代码（agent 自然语言驱动）
- 与 llm-wiki 工具栈一致（你已经在用 Claude Code）

**Cons**：
- ScheduleWakeup 不适合 4h 间隔的长周期 cron（设计用途是"短期会话内续作"），跨多天调度不可靠
- Claude Code agent 每次唤醒会消耗 input token（系统 prompt + 工具定义），4h × 30 天 = 180 次唤醒，token 成本 >> 直接 API
- XHS/微博抓取在 agent 沙盒里很难调试
- ❌ **失效**：runtime 不匹配场景

#### Option C: n8n self-hosted + 现成节点

**架构**：本地或 VPS 跑 n8n，用其 RSS / HTTP / Notion / Telegram 节点拼。

**Pros**：
- 可视化工作流，改 source 不用改代码
- 节点丰富

**Cons**：
- 中文社区站点没有现成 n8n 节点，仍需自己写 HTTP 节点 + 解析
- 主题聚类需要外接 LLM 节点，调试比纯 Python 麻烦
- 多一个服务进程要维护
- VPS 成本（如不本地跑）

### 单一可行方案的失效判定

如果只剩一个方案：
- **B 失效**：runtime（ScheduleWakeup）与 4h × 跨日的工作场景不匹配；token 成本远高于 API 直调
- **C 失效**：可视化优势在中文站抓取场景不成立（仍需自己写 HTTP），多了 n8n 维护负担

**选定 Option A**。

## Architecture Decision Record (ADR)

### Decision

采用 **Option A：纯 Python + launchd + SQLite + Claude Haiku + Notion API + TG Bot**。

### Drivers

1. 抓取稳定性 → 直接控制 HTTP 请求最透明，失败可重试
2. 运维成本 → 单一 Python 进程，无外部服务依赖（除 API）
3. 成本 → Haiku 聚类 + SQLite 本地存储，每月 < ¥30 估算

### Alternatives Considered

- Option B（Claude Code agent）：runtime 不匹配，已 invalidate
- Option C（n8n）：中文源场景下可视化无收益，已 invalidate

### Why Chosen

A 在三个 driver 上都最优，且与用户 CLAUDE.md "最简方案优先" 直接一致。

### Consequences

- ✅ 调试和迭代速度最快
- ✅ 总代码量预估 800-1200 行，单人可维护
- ⚠️ 依赖电脑常开（夜间睡眠会漏 1-2 个抓取窗口；可接受，因为 4h 颗粒度本身就有冗余）
- ⚠️ 未来若要扩展到团队订阅，需重构（spec 已标为 non-goal）

### Follow-ups

- 若 launchd 漏窗口频繁（>10%），考虑迁移到 1Panel / VPS（5-10 美元/月）
- 若 XHS/微博持续抓不动 >2 周，砍掉 best-effort 源，专注骨干

## Implementation Plan

### Module Layout

```
ai-digest-workflow/
├── ai-digest-workflow.md          # spec（已存在）
├── README.md                       # 本 plan + 部署步骤
├── pyproject.toml                  # uv 管理依赖
├── config/
│   ├── sources.yaml               # 源列表 + 抓取策略
│   └── prompts/
│       └── topic-cluster.txt      # 聚类 prompt 模板
├── src/digest/
│   ├── __init__.py
│   ├── fetchers/                  # 每源一个文件，便于失败隔离
│   │   ├── base.py                # Fetcher 抽象基类
│   │   ├── linux_do.py
│   │   ├── jike.py                # 即刻
│   │   ├── v2ex.py
│   │   ├── juejin.py              # 掘金
│   │   ├── sspai.py               # 少数派
│   │   └── xhs.py                 # XHS（best-effort，调用 MediaCrawler）
│   ├── store.py                   # SQLite schema + CRUD
│   ├── cluster.py                 # Claude Haiku 主题聚类
│   ├── digest_builder.py          # 紧凑 digest markdown 生成
│   ├── push_telegram.py           # TG 推送
│   ├── archive_notion.py          # Notion DB 写入
│   └── pipeline.py                # 主入口：fetch → store → (daily) cluster → push + archive
├── scripts/
│   ├── run_fetch.py               # 单跑抓取（4h cron 触发）
│   ├── run_daily.py               # 单跑日报（每日 8am 触发）
│   └── backfill.py                # 历史回填工具
├── data/
│   ├── items.db                   # SQLite（gitignore）
│   └── logs/                      # 抓取/聚类日志
└── .env.example                   # API keys 模板
```

### Schema (SQLite)

```sql
CREATE TABLE sources (
  id TEXT PRIMARY KEY,             -- e.g. "linux_do"
  display_name TEXT,
  health_score REAL DEFAULT 1.0,   -- 0-1 滑动平均
  last_success_at TIMESTAMP
);

CREATE TABLE items (
  id TEXT PRIMARY KEY,             -- hash(source_id + url)
  source_id TEXT REFERENCES sources(id),
  url TEXT UNIQUE,
  title TEXT,
  content TEXT,
  author TEXT,
  published_at TIMESTAMP,
  fetched_at TIMESTAMP,
  topic_id TEXT,                   -- nullable, set by cluster step
  pushed BOOLEAN DEFAULT 0,
  archived BOOLEAN DEFAULT 0
);

CREATE INDEX idx_items_fetched ON items(fetched_at);
CREATE INDEX idx_items_topic ON items(topic_id);

CREATE TABLE topics (
  id TEXT PRIMARY KEY,
  name TEXT,                        -- LLM 生成的主题名
  summary TEXT,
  date DATE,                        -- 该主题归属哪天的 digest
  item_ids TEXT                     -- JSON array
);
```

### Stage Contracts

| Stage | Input | Output | 失败行为 |
|-------|-------|--------|----------|
| fetch (per source) | source config | rows in `items` | 单源失败 log + 降健康分，不 throw |
| dedup | last 7 days items | `items.is_dup` flag | 失败回退到 url-only dedup |
| cluster (daily) | 24h 新条目 | `topics` rows + items.topic_id | LLM 失败重试 1 次，再失败回退到"按源分组" |
| digest_build | today's topics | markdown ≤500 字 | 主题数 <3 时退化为"今日要闻"列表 |
| push_telegram | digest md | TG message | 失败 log + 重试 3 次 |
| archive_notion | items + topics | Notion pages | 失败入队列待下次重试，不阻塞 push |

### Acceptance Criteria → Tests

| Spec criterion | Test |
|----------------|------|
| 骨干源抓取成功率 ≥95% | `pytest tests/test_fetchers.py`（mock HTTP，断言 5/5 骨干源 fetcher 在 200 + 4xx + 5xx + timeout 下行为正确） |
| 紧凑 digest ≤500 字 | `test_digest_size`（sample 数据，断言字数） |
| 7 天去重 | `test_dedup_window`（同 url 7 天内只保留首次） |
| 空日不推 | `test_empty_day_silent`（mock 0 条目，断言 push 函数未被调用） |
| 单源失败不阻塞 | `test_isolated_failure`（模拟 jike 抛异常，其他源仍写入） |

### Implementation Sequence

按依赖顺序 6 个增量 PR：

1. **PR1**：项目骨架 + SQLite schema + base fetcher 抽象 + 1 个 fetcher（linux.do，最稳）+ 端到端单元测试
2. **PR2**：扩展 4 个骨干源 fetcher（V2EX, 即刻, 掘金, 少数派）
3. **PR3**：dedup + cluster (Claude Haiku) + topic 存储；prompt yaml 外置
4. **PR4**：digest_build + TG push（先打通推送）
5. **PR5**：Notion archive
6. **PR6**：launchd plist + 部署文档；XHS/微博 best-effort fetcher（独立 PR，可不合）

每个 PR 严格遵循用户 CLAUDE.md 第 2.4 条（一个 PR 一件事），完成后停下让用户 review。

### Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| XHS/微博 反爬持续失效 | High | Low | 已标为 best-effort，骨干 5 源覆盖 |
| Claude API key 泄露 | Low | High | `.env` + gitignore；Notion/TG token 同 |
| LLM 聚类质量差 | Medium | Medium | Prompt yaml 可迭代；提供回退到"按源分组"模式 |
| launchd 漏窗口 | Medium | Low | 4h 颗粒已带冗余；单日漏 1 次不影响日报 |
| Notion API rate limit | Low | Medium | 异步队列 + 指数退避 |

### Out of Scope（按 spec 显式排除）

- ❌ 雷达预警 / 关键词触发即时推送
- ❌ 二次创作素材库独立 layer
- ❌ 英文源
- ❌ 实时性（小时级以下）
- ❌ 多人共享 / 团队订阅
- ❌ Web UI（命令行 + TG/Notion 即出口）

## Open Questions for Architect/Critic

1. SQLite 中转是否必要？还是直接读取后 in-memory 处理 + 直写 Notion 即可？（trade: 简单 vs 可重跑/可调试）
2. 聚类用 Haiku 是否足够？还是用 Sonnet？（成本 vs 质量）
3. fetcher 抽象是过度设计还是合理（5 个源 + 2 个 best-effort 一共 7 个）？
4. PR 分割粒度是否合理？
