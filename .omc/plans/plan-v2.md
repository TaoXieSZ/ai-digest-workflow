# Plan v2 — AI Digest Workflow（Architect + Critic 修订版）

> 用户选择"强制 autopilot + 安全默认"路径。本 plan 锁定 B 默认 + 修复 v1 全部 reviewer feedback。

## 锁定的架构决策（来自用户 B 选择）

| 决策点 | v1 | v2 | 理由 |
|--------|----|----|------|
| 推送通道 | Telegram | **飞书 webhook** | TG 在 GFW 受阻，飞书深圳直连 |
| 调度 | macOS launchd | **`mcp__scheduled-tasks` MCP** | 不依赖 Mac 开机，无睡眠漏窗口 |
| LLM | Claude Haiku | Claude Haiku（不变） | 用户正在用 Claude Code = API 可达 |
| 归档 | Notion API | Notion API（不变） | 用户深圳能用 Notion 应用 |
| 中转存储 | SQLite | **SQLite（保留）** | Architect 建议丢弃，但去重窗口 + 可重跑调试需要本地状态；保留 |

## 修复 v1 的 10 个 Reviewer 问题

### 来自 Architect

1. ~~`fetchers/base.py` + 7 个具体 fetcher~~ → **改为 `RSSFetcher` + `HTMLFetcher` 两个泛型类，dispatch 由 `sources.yaml` 决定**。骨干源至少 4/5 走 RSS（V2EX、掘金、即刻有公开 RSS；少数派 + linux.do 是 Discourse RSS）。
2. ~~`items.topic_id` 单字段~~ → **改为 `item_topic_assignments(item_id, topic_id, digest_date)` 关联表**，支持 7 天滚动重新聚类。
3. ~~push/archive 无 idempotency~~ → **每个 digest 生成 UUID 写 SQLite 后再调用网络 API；重试时按 UUID 检查 `pushed_at` / `archived_at`**。
4. ~~`health_score` 未定义衰减~~ → **EWMA α=0.3，连续 3 次失败健康分 < 0.3 自动跳过 24h**。
5. ~~MediaCrawler 同进程导入~~ → **subprocess + 30s timeout；XHS/微博 fetcher 标 `enabled: false` 默认关闭**，需要时手动启用。

### 来自 Critic

6. **环境可达性测试**：在 `scripts/healthcheck.py` 加 5 个端点 ping（anthropic / notion / 飞书 / 5 个骨干源）；CI 跑前必须 PASS。
7. **dedup 算法明确**：(a) URL canonical 精确匹配 → (b) 标题 trigram + 24h 窗口 → (c) Haiku LLM 兜底配对（每天最多 N=20 对）。
8. **Notion schema 测试**：`tests/test_notion_schema.py` 创建 sandbox page，断言主题 tag + 日期字段读写。
9. **fetcher 真实性**：单元测试只测 parse 正确性，**不再断言 ≥95%**；改为生产指标 `sources.health_score` + 7 天滑窗告警。
10. **Vague 缓解 → 具体**：
    - API key：加 `gitleaks` pre-commit hook
    - LLM 质量：维护 `tests/eval/clustering_pairs.jsonl`（20 对手标）+ 月度 review
    - Notion 重试：失败入 `data/notion_retry_queue.jsonl`，下次 cron 优先重放

### 来自 CLAUDE.md

11. lint/type-check：**ruff + mypy** + `.pre-commit-config.yaml`
12. URL canonicalization：剥 utm_*、fbclid、嵌入空 query 的 `?` 等
13. 每个 PR 完成后停下展示 diff，等用户确认（不自动 push）
14. commit message 格式 what+why

## 新数据 Schema（v2）

```sql
CREATE TABLE sources (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  fetcher_type TEXT NOT NULL,              -- 'rss' | 'html' | 'subprocess'
  config_json TEXT NOT NULL,                -- url, selectors, etc.
  enabled INTEGER DEFAULT 1,
  health_score REAL DEFAULT 1.0,            -- EWMA, α=0.3
  last_success_at TIMESTAMP,
  last_error TEXT
);

CREATE TABLE items (
  id TEXT PRIMARY KEY,                      -- sha256(source_id || canonical_url)[:16]
  source_id TEXT NOT NULL REFERENCES sources(id),
  url TEXT NOT NULL,                        -- canonical
  raw_url TEXT,                             -- original before canonicalization
  title TEXT,
  content TEXT,
  author TEXT,
  published_at TIMESTAMP,
  fetched_at TIMESTAMP NOT NULL,
  UNIQUE(source_id, url)
);
CREATE INDEX idx_items_fetched ON items(fetched_at);

CREATE TABLE topics (
  id TEXT PRIMARY KEY,                      -- uuid
  name TEXT NOT NULL,
  summary TEXT,
  digest_date DATE NOT NULL,
  created_at TIMESTAMP NOT NULL
);
CREATE INDEX idx_topics_date ON topics(digest_date);

CREATE TABLE item_topic_assignments (
  item_id TEXT NOT NULL REFERENCES items(id),
  topic_id TEXT NOT NULL REFERENCES topics(id),
  digest_date DATE NOT NULL,
  PRIMARY KEY (item_id, topic_id, digest_date)
);

CREATE TABLE digests (
  id TEXT PRIMARY KEY,                      -- uuid，先于网络调用写入
  digest_date DATE NOT NULL UNIQUE,
  content_md TEXT NOT NULL,
  pushed_at TIMESTAMP,                      -- nullable, set on success
  archived_at TIMESTAMP,                    -- nullable
  push_attempts INTEGER DEFAULT 0,
  archive_attempts INTEGER DEFAULT 0
);
```

## 修订后的 Module Layout

```
ai-digest-workflow/
├── ai-digest-workflow.md
├── README.md
├── pyproject.toml                 # ruff + mypy + pytest
├── .pre-commit-config.yaml         # ruff + mypy + gitleaks
├── .gitignore
├── .env.example
├── config/
│   ├── sources.yaml
│   └── prompts/
│       └── topic-cluster.txt
├── src/digest/
│   ├── __init__.py
│   ├── url_canonical.py            # NEW: 剥 utm_* etc.
│   ├── store.py                    # SQLite schema + CRUD
│   ├── fetchers/
│   │   ├── __init__.py
│   │   ├── base.py                 # Fetcher Protocol
│   │   ├── rss.py                  # 通用 RSS（feedparser）
│   │   ├── html.py                 # 通用 HTML（httpx + selectolax）
│   │   └── subprocess_fetcher.py   # MediaCrawler 兜底
│   ├── dedup.py                    # 3 阶段 dedup
│   ├── cluster.py                  # Claude Haiku 聚类
│   ├── digest_builder.py           # ≤500 字 markdown
│   ├── push_feishu.py              # 飞书 webhook
│   ├── archive_notion.py           # Notion API
│   └── pipeline.py                 # 编排：fetch → dedup → (daily) cluster → push + archive
├── scripts/
│   ├── run_fetch.py                # 单跑抓取
│   ├── run_daily.py                # 单跑日报
│   ├── healthcheck.py              # NEW: 端点连通性
│   └── backfill.py
├── data/
│   ├── items.db
│   ├── notion_retry_queue.jsonl    # NEW
│   └── logs/
└── tests/
    ├── test_url_canonical.py
    ├── test_store.py
    ├── test_rss_fetcher.py
    ├── test_html_fetcher.py
    ├── test_dedup.py
    ├── test_cluster.py
    ├── test_digest_builder.py
    ├── test_push_feishu.py
    ├── test_notion_schema.py
    └── eval/
        └── clustering_pairs.jsonl  # 20 对手标
```

## 修订后的 Acceptance Criteria → Tests 映射（修复 Critic table）

| Spec criterion | Test | 真实性 |
|----------------|------|--------|
| 3-5 主题 / 2-4 条/主题 / ≤500 字 | `test_digest_size`：断言主题数 ∈ [3,5]、每主题条数 ∈ [2,4]、总字数 ≤500 | ✅ |
| 原始链接 + 来源 + Notion 链接 | `test_digest_render_links`：sample digest，断言每条都有 3 个 link | ✅ |
| 跨源同事件 7 天去重 | `test_dedup_cross_source`：构造 2 源同事件不同 URL 的 fixture，断言 cluster 输出 1 条 | ✅（修正） |
| Notion 主题筛选/日期排序 | `test_notion_schema`：sandbox page 读写 | ✅ |
| 骨干源 ≥95% | 单测 = parse 正确性；生产 = `sources.health_score` 7 天滑窗 | ✅（修正） |
| 单源失败不阻塞 | `test_isolated_failure` | ✅ |
| 空日不推 | `test_empty_day_silent` | ✅ |
| 深圳无 VPN 可跑 | `scripts/healthcheck.py` 5 端点 ping | ✅ |

## PR 切片（修订版）

按用户 CLAUDE.md "一个 PR 一件事" + "<400 行优先"：

- **PR1**：项目骨架 + URL canonical + SQLite schema + 1 个 RSS fetcher（linux.do）+ 单元测试。**约 350 行。停下让用户 review。**
- **PR2**：扩 4 个 RSS 源 + healthcheck 脚本
- **PR3**：dedup 三阶段（URL → trigram → LLM）
- **PR4**：cluster (Haiku) + topics 表 + item_topic_assignments
- **PR5**：digest_builder + 飞书 push + idempotency
- **PR6**：Notion archive + 重试队列
- **PR7**：scheduled-tasks MCP 集成 + 部署文档
- **PR8（可选）**：MediaCrawler subprocess + XHS/微博 best-effort

## ADR

**Decision**：Python 3.11 + pip + SQLite + 飞书 + scheduled-tasks MCP + Haiku + Notion + ruff/mypy/pre-commit。

**Drivers**：CN 网络可达、个人维护成本最小、Architect+Critic 全部 critical 修复、CLAUDE.md 显式遵守。

**Alternatives Rejected**：
- TG（GFW）、launchd（睡眠漏窗）、n8n（中文站节点缺）、Claude Code agent + ScheduleWakeup（runtime 不匹配）

**Consequences**：
- ✅ 全链路 CN 可达
- ✅ Reviewer 10 项 issue 全部修复
- ⚠️ 飞书 webhook 需要你创建群机器人（一次性手工）
- ⚠️ scheduled-tasks MCP 假设你已配（Q3 你选了"你看哪个好用就哪个" 的隐式 A）

**Follow-ups**：
- 若飞书机器人速率限制（5/秒）触发，加 throttle
- 若 Haiku 聚类质量差（eval pairs <80% 准确）升 Sonnet

---

# 2026-05-04 Pivot 更新（spec v2 双轨）

## 触发

用户 #1 答复："上次错过线下黑客松展会"——真实痛点不是聚合，是不漏报名。spec 已改为**双轨**（事件雷达 + 资讯日报）。

## PR 切片重排（PR1 已完成，PR2-8 收编为 PR-A/B/C）

### PR-A 事件雷达 MVP（核心交付）

**目标**：发现 AI 活动事件 → 整点批量推送飞书。

**Schema 改动**：
- `items` 表加 `kind TEXT`（`event` / `news` / `tool` / `unclassified`）
- 新增 `event_metadata(item_id PK, event_date, registration_deadline, location, registration_url)` 表
- `digests` 表加字段 `kind`（`event_batch` / `daily_digest`）

**新模块**：
- `src/digest/classifier.py` — Claude Haiku 分类 + event 字段抽取（一次 LLM 调用做两件事）
- `src/digest/sources/xhs_skill_bridge.py` — 调用已装的 `xiaohongshu` skill 的 bash 脚本（subprocess）
- `src/digest/push_feishu.py` — 飞书 webhook，结构化卡片
- `src/digest/event_radar.py` — 整点扫未推送 event，批量推
- `scripts/run_radar.py` — 整点 cron 入口

**外部部署文档**（用户自己跑一次）：
- `deploy/wewe-rss/docker-compose.yml` + `README.md` — 自部署 we-mp-rss，用户填订阅的公众号清单
- 公众号 RSS 加进 `config/sources.yaml`（fetcher_type: rss，复用 PR1 的 RSSFetcher）

**Acceptance**：
- 给 fixture 数据（含 1 条黑客松招募）→ classifier 正确分类为 event 并抽出 event_date
- 整点跑一次 → 飞书收到结构化卡片
- 同一 event 跑 2 次只推 1 次（idempotency）
- 23:00-07:00 跑 → 不推

**预估**：~500-700 行 production + 200 行测试 + docker-compose + 部署 README。

### PR-B 资讯日报（次要）

**目标**：plan-v2 原 PR3-PR6 合并 — non-event 条目走主题聚合 daily digest。

- 加剩余源：即刻 / V2EX / 掘金 / 少数派（RSS）
- dedup 三阶段（URL → trigram → LLM）
- 主题聚类 (Claude Haiku) + topics 表 + item_topic_assignments
- digest builder（≤500 字）+ 飞书 push + Notion 归档 + 重试队列

**Acceptance**：当日 non-event 条目 ≥3 时生成 digest，否则静默；Notion DB 累积所有条目按 kind/date 可查。

**预估**：~500 行。

### PR-C 稳态运维

- scheduled-tasks MCP 集成 + cron 配置（4h 抓取 + 整点雷达 + 每日 digest）
- 微博 MCP 接入（[qinyuanpei/mcp-server-weibo](https://github.com/qinyuanpei/mcp-server-weibo) 优选）
- `scripts/healthcheck.py` 端点连通性检查
- LLM 聚类 eval harness（`tests/eval/clustering_pairs.jsonl` 20 对手标）
- gitleaks pre-commit + .env 检查

**预估**：~300 行 + 配置。

## 优先级 / 顺序

PR-A → 你看效果 + 自部署 we-mp-rss → PR-B → PR-C。

PR1 的所有代码（URL canonical / SQLite store / RSSFetcher / linux.do 接入）三个 PR 都复用，没浪费。

## 用户已确认的 PR-A 关键决策

- **推送时机**：整点批量（非即时；避免打扰）
- **wewe-rss 部署**：我写 docker-compose + 部署 README；用户跑 `docker compose up`，填订阅公众号清单
- **静默时段**：23:00-07:00 不推送（spec 新增）
- **XHS**：调用已装的 `xiaohongshu` skill，不写 fetcher
- **微博**：留到 PR-C 用 MCP，PR-A 不做
