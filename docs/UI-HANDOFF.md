# UI Handoff — ai-digest-workflow

> 写给接手做 web UI 的 Cursor / Codex / 任意 AI 工具。
> 自包含。读完这一份就能开工。

## 一句话

后端已稳定（自动跑 + 多源接入 + 飞书/Notion 推送），现在需要一个**本地 web UI** 做"读库 / 浏览 / 调试 / 手动触发"，不替换后端。

> **基线 commit**：`96dbb3f`（main, 2026-05-06）
> Cursor 直接 clone main 即可。

---

## 当前后端状态（2026-05-06）

### 数据流

```
12 个 source（每 30 分钟 fetch）
  ↓ scripts/run_fetch.py        (launchd: ai-digest.fetch)
data/items.db (SQLite)
  ↓ classifier (DeepSeek)        (launchd: ai-digest.radar  每小时)
  ├── kind=event   → event_metadata 抽字段 → 飞书事件雷达卡片 + Notion
  └── kind=news/tool/other
        ↓ dedup → cluster (DeepSeek) → digest_builder
        (launchd: ai-digest.digest 每天 12:00)
        → 飞书资讯日报卡片
        → Notion DB（每个 item 一行）
        → data/digests/daily-{date}.md（OMC wiki 离线 ingest）
```

### Sources（12 个，全部 enabled）

| id | 类型 | 来源 |
|---|---|---|
| `linux_do` | rss | linux.do/latest.rss |
| `xhs_events` | xhs | 小红书 MCP（5 个关键词）|
| `kazik` `guang_github` `jiqizhixin` `qbitai` | rss | wewe-rss 公众号 |
| `nn_juejin` `nn_36kr` `nn_ithome` `nn_solidot` `nn_hackernews` `nn_producthunt` | newsnow | newsnow.busiyi.world 聚合 |

### Schema（关键表）

```sql
items (
  id TEXT PK,
  source_id TEXT,
  url TEXT,                 -- canonical
  raw_url TEXT,
  title TEXT,
  content TEXT,             -- HTML-stripped 正文
  author TEXT,
  published_at TIMESTAMP,
  fetched_at TIMESTAMP,
  kind TEXT,                -- event / news / tool / other / unclassified
  classified_at TIMESTAMP,
  notion_archived_at TIMESTAMP,
  UNIQUE(source_id, url)
);

event_metadata (
  item_id PK,
  event_date DATE,
  registration_deadline DATE,
  location TEXT,
  registration_url TEXT,
  extracted_at TIMESTAMP
);

event_pushes (
  item_id PK,               -- idempotency key for 飞书推送
  pushed_at TIMESTAMP,
  digest_id TEXT
);

topics (
  id PK uuid,
  name TEXT,
  summary TEXT,
  digest_date DATE,
  created_at TIMESTAMP
);

item_topic_assignments (
  item_id, topic_id, digest_date PK
);

digests (
  id PK uuid,
  digest_date DATE,
  kind TEXT,                -- event_batch / daily_digest
  content_md TEXT,
  pushed_at TIMESTAMP,
  archived_at TIMESTAMP,
  push_attempts INT,
  UNIQUE(digest_date, kind)
);

sources (
  id PK,
  display_name, fetcher_type, config_json,
  enabled INT,
  health_score REAL,        -- EWMA 健康打分
  last_success_at, last_error
);

xhs_note_details (
  feed_id PK,
  xsec_token, title, content, fetched_at
);
```

### 自动化（已装）

3 个 launchd job 在 `~/Library/LaunchAgents/com.txie.ai-digest.{fetch,radar,digest}.plist`：

| Job | 频率 | 入口 |
|-----|------|------|
| fetch | 每 30 分钟 | `scripts/run_fetch.py` |
| radar | 每小时 | `scripts/run_radar.py` |
| digest | 每天 12:00 本地 | `scripts/run_digest.py` |

包装脚本 `deploy/launchd/run.sh` 负责加载 `.env` + `deploy/wewe-rss/.env`。
日志在 `data/logs/{fetch,radar,digest}.{out,err}.log`。

### 测试 / 质量

- 193/193 pytest passing
- ruff clean, mypy strict clean
- GitHub Actions CI 还没接（小 TODO）

### 自上次 handoff 以来的变化（参考用）

写完此文档（commit `d2831e4`）后又改了几次，**对 UI 接口没影响**但需知道：

- `Classifier.classify_many(items, concurrency=8)` 加了，并发 4-5x 加速。UI 不用动这条路径
- `insert_digest` 删了，只剩 `upsert_event_batch_digest` / `upsert_daily_digest`
- README 重写过，新人可以读 README.md 入门

---

## UI 需求

### 用户视角

我（用户）想：

1. **看今天的资讯日报 + 事件雷达**，不用打开飞书
2. **看历史日报 / 主题归档**（按日期翻）
3. **看每个 item 的全文**（HTML stripped 后的 content，配 source 标签 + classifier 给的 kind）
4. **手动改 item 的 kind**（classifier 错分时，我能拖到 event/news/tool/other）
5. **看 source health**（哪个源挂了 / 抓了多少条 / 最近一次成功）
6. **手动触发 fetch / radar / digest**（不想等下个调度点）
7. **看 Notion 同步状态**（哪些 item 已归档，retry queue 里有什么）

### 不要做

- ❌ **不要做账号系统** —— 单用户本地工具
- ❌ **不要替代飞书 push** —— 飞书是核心通道
- ❌ **不要重写 fetch/cluster/push 逻辑** —— 全在 `src/digest/` 里跑得好好的，UI 只读 + 调用 CLI
- ❌ **不要做移动端响应式** —— 桌面浏览器够用

---

## 推荐架构

### 选型：FastAPI + HTMX + SQLite 直读

理由：
- 后端已是 Python，再加 FastAPI 就一个 `pip install`
- HTMX 不要 build step，HTML over the wire，AI 工具最容易写
- 直读 `data/items.db`（read-only mode），零状态同步问题
- 子进程触发 CLI（fetch/radar/digest）就是 `subprocess.run([".venv/bin/python", "scripts/run_X.py"])`

不要的复杂度：React/SvelteKit/Vue（build step 给 AI 工具增加错率）、Postgres / 任何额外服务。

### 目录建议

```
ui/
├── pyproject.toml      # 独立 deps（fastapi, jinja2, uvicorn, htmx-via-cdn）
├── main.py             # FastAPI app + routes
├── db.py               # 只读 sqlite3 helpers（复用 src/digest/store.py 的 schema 知识）
├── templates/          # Jinja2 + HTMX
│   ├── base.html
│   ├── digest.html     # 今天 / 某日的 digest
│   ├── items.html      # 全部 item 表格 + filter
│   ├── sources.html    # health dashboard
│   └── trigger.html    # 触发 fetch/radar/digest 按钮
└── static/             # CSS（用 Pico.css 或 simple.css，零 JS）
```

### 启动方式

```bash
cd ui && uv pip install -e .   # or pip
.venv/bin/uvicorn main:app --reload --port 8765
```

可选：第 4 个 launchd job 跑 uvicorn 服务，随系统自启。

---

## API 草案（5 个端点够用）

```
GET  /                         → 重定向到 /digest
GET  /digest                   → 今天 digest（topics + items 渲染）
GET  /digest/{YYYY-MM-DD}      → 历史 digest
GET  /items                    → 表格视图，querystring filter:
                                 ?source=linux_do&kind=event&since=2026-05-01&q=claude
GET  /items/{item_id}          → 单条详情（title + url + source + content + kind + event_metadata）
PATCH /items/{item_id}/kind    → 手动改 kind（写 items.kind + 重置 classified_at）
GET  /sources                  → health dashboard（source + count + health_score + last_success_at）
POST /trigger/{job}            → 触发 launchctl kickstart com.txie.ai-digest.{job}
                                 job ∈ fetch / radar / digest
GET  /events                   → 还没推 + 还没过期的 event 列表（事件雷达视图）
```

---

## 实现要点（给 AI 工具）

### 1. 用 sqlite3 read-only 模式打开

```python
import sqlite3
conn = sqlite3.connect("file:data/items.db?mode=ro", uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
```

避免 UI 锁住数据库阻塞 cron。

### 2. PATCH /items/{id}/kind 是唯一写操作

```python
# 加新连接（writable）
import sqlite3
with sqlite3.connect("data/items.db") as w:
    w.execute(
        "UPDATE items SET kind=?, classified_at=NULL WHERE id=?",
        (new_kind, item_id),
    )
```

`classified_at=NULL` 让下次 radar 重新分类（如果用户改完想反悔）。或者只设 kind 不动 classified_at，让用户手动覆盖永久生效。设计选其一。

### 3. 触发 CLI 用 launchctl，不要直接 subprocess python

理由：launchctl kickstart 走 launchd 的并发管控（同一时刻不会双重 fetch），还能拿到现成 logs。

```python
import subprocess, os
def trigger(job: str):
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{uid}/com.txie.ai-digest.{job}"],
        check=True,
    )
```

### 4. 不在 UI 里重新实现 cluster/push

如果你想"在 UI 里看 dry-run digest"，直接调 `scripts/run_digest.py --dry-run` 把 stdout 抓回来 render；别 import `digest.daily_digest` 在 web 进程跑（会 block 几秒）。

### 5. Notion archive 状态

`items.notion_archived_at IS NOT NULL` 表示已归档。
失败 queue 在 `data/notion_retry_queue.jsonl`（每行一条 JSON）。

### 6. Style

用 [Pico.css](https://picocss.com/)（一行 CDN）或 [Simple.css](https://simplecss.org/)。
零 JS 框架。HTMX 用 `<script src="https://unpkg.com/htmx.org@2.0.4"></script>`。

---

## 优先级（建议 PR 顺序）

1. **PR1 骨架** — FastAPI hello + 数据库连接 + base.html（30 分钟）
2. **PR2 /digest** — 今天 digest 视图（直读 topics + assignments + items）
3. **PR3 /items** — 表格 + filter + /items/{id} 详情页
4. **PR4 /sources** — health dashboard
5. **PR5 /trigger** — 4 个按钮触发 launchctl
6. **PR6 PATCH kind** — UI 上拖动改分类
7. **PR7 /events** — 待推 event 视图

每个 PR 独立可用。先做 1+2 就能用了。

---

## 关键路径速查

```
~/projects/ai-digest-workflow/
├── README.md                       # 旧 PR1 时期，下次更新
├── TODO.md                         # 已完成 + 剩余 12 项（其中 UI 不算 TODO）
├── ai-digest-workflow.md           # spec v2 双轨架构
├── pyproject.toml                  # backend deps
├── .env                            # DEEPSEEK_API_KEY / FEISHU_WEBHOOK_URL / NOTION_TOKEN / NOTION_DATABASE_ID
├── config/sources.yaml             # 12 sources
├── src/digest/
│   ├── classifier.py               # LLM 分类（DeepSeek/Anthropic/Qwen 多 provider）
│   ├── event_radar.py              # 事件雷达编排
│   ├── daily_digest.py             # 资讯日报编排
│   ├── dedup.py                    # URL + trigram 24h 窗口
│   ├── cluster.py                  # DeepSeek 主题聚类
│   ├── digest_builder.py           # ≤2500 字 markdown 渲染
│   ├── push_feishu.py              # 飞书卡片（事件 / 日报）
│   ├── archive_notion.py           # Notion DB 归档 + retry queue
│   ├── store.py                    # SQLite schema + helpers
│   ├── url_canonical.py            # URL 规范化
│   ├── fetchers/
│   │   ├── base.py                 # Fetcher Protocol
│   │   └── rss.py                  # RSS + HTML strip
│   └── sources/
│       ├── newsnow.py              # newsnow API fetcher
│       └── xhs_skill_bridge.py     # XHS subprocess 桥
├── scripts/
│   ├── run_fetch.py                # CLI 入口
│   ├── run_radar.py                # CLI 入口（含 --dry-run）
│   ├── run_digest.py               # CLI 入口（含 --dry-run）
│   ├── setup_notion_db.py          # Notion DB 一次性建表
│   └── wewe_feeds_to_yaml.py       # wewe-rss 订阅 → yaml 条目
├── deploy/
│   ├── launchd/                    # 自动化（install.sh / uninstall.sh / run.sh / README.md）
│   └── wewe-rss/                   # docker-compose.yml + .env + README.md
├── data/
│   ├── items.db                    # 主数据库（gitignored）
│   ├── digests/                    # daily-{date}.md（gitignored）
│   ├── logs/                       # launchd 日志（gitignored）
│   └── notion_retry_queue.jsonl    # Notion 失败队列（gitignored）
└── tests/                          # 192 pytest（pure unit + 1 e2e）
```

## 用户偏好（来自 ~/CLAUDE.md，强相关）

- **不自动 push** — commit 后停下，等用户确认
- **每个改动展示 diff**（不止口头说"改好了"）
- **小 PR 优先**（<400 行）
- **提交前本地验证** — pytest + ruff + mypy 全绿才 commit
- **关键假设必须显式确认** — 架构方向不可逆决策必须先问
- **结构化提问** — 模糊意图给 2-4 个文字选项

## 给 Cursor / Codex 的开场白

> 我接手做 ai-digest-workflow 的 web UI。后端已稳定不要碰。
> 我会先实现 PR1（FastAPI 骨架）+ PR2（/digest 视图），跑起来给你看，
> 然后按 PR3-7 顺序推进。tech stack: FastAPI + HTMX + Jinja2 + sqlite3 直读。
> 数据库 schema 在 src/digest/store.py 的 SCHEMA 常量里。
> 启动命令：`cd ui && uvicorn main:app --reload --port 8765`。
