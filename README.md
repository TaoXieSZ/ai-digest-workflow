# AI Digest Workflow

抓中文 AI 圈的活动 + 资讯，**双轨**处理：

- 📡 **事件雷达** — 黑客松 / meetup / 沙龙 → 整点批量推飞书结构化卡片，含活动日期 / 报名截止 / 地点 / 报名链接
- 📰 **资讯日报** — 模型发布 / 工具更新 / 行业动态 → DeepSeek 聚类成 3-5 主题日报 → 飞书 + Notion DB 归档 + 本地 markdown（OMC wiki ingest 用）

完全本地跑，不依赖外部服务（除 LLM API + 飞书 webhook + Notion API）。已通过 launchd 自动化每天跑。

```
┌───────── 12 sources (fetch /30min) ─────────┐
│ linux_do │ xhs_events │ 4 公众号 │ 6 newsnow│
└──────────────────┬──────────────────────────┘
                   ↓ DeepSeek 分类 (radar /1h)
       ┌───────────┴───────────┐
       │ kind=event            │ kind=news/tool/other
       ↓                       ↓ dedup → cluster (digest /daily 12:00)
   📡 飞书事件雷达卡          📰 飞书资讯日报 + Notion DB + data/digests/
   (整点 + idempotent)        (≤2500 字 / 4-5 主题 / 1-4 条/主题)
```

## 快速开始

```bash
# 1. 装依赖
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. 配置 .env（参考 .env.example）
cp .env.example .env
# 填：DEEPSEEK_API_KEY  FEISHU_WEBHOOK_URL  NOTION_TOKEN  NOTION_DATABASE_ID

# 3. 配置 sources.yaml（默认 12 个源全开）
# 公众号需要先起 wewe-rss，见 deploy/wewe-rss/README.md
# XHS 事件需要起 xiaohongshu-mcp（github.com/xiaohongshu-mcp）

# 4. 手动跑一轮看效果
python scripts/run_fetch.py    # 抓所有源
python scripts/run_radar.py    # 分类 + 推事件
python scripts/run_digest.py   # 聚合 + 推日报（含 --dry-run 看预览）

# 5. 装 launchd 自动化（可选，macOS only）
deploy/launchd/install.sh
```

## Sources

| ID | Type | 说明 |
|---|---|---|
| `linux_do` | rss | linux.do/latest.rss |
| `xhs_events` | xhs | 小红书 MCP，5 个关键词搜活动 |
| `kazik` `guang_github` `jiqizhixin` `qbitai` | rss (wewe-rss) | 微信公众号本地代理 |
| `nn_juejin` `nn_36kr` `nn_ithome` `nn_solidot` `nn_hackernews` `nn_producthunt` | newsnow | newsnow.busiyi.world 聚合 |

新增源：编辑 `config/sources.yaml`，已实现的 fetcher_type 是 `rss` / `xhs` / `newsnow`。

## 自动化（macOS launchd）

```bash
deploy/launchd/install.sh                  # 装 3 个 LaunchAgent
launchctl list | grep ai-digest            # 看状态
tail -F data/logs/{fetch,radar,digest}.{out,err}.log
```

详细见 [`deploy/launchd/README.md`](deploy/launchd/README.md)。

| Job | 频率 | 入口 |
|-----|------|------|
| fetch | 每 30 分钟 | `scripts/run_fetch.py` |
| radar | 每小时 | `scripts/run_radar.py` |
| digest | 每天本地 12:00 | `scripts/run_digest.py` |

## 输出

- **飞书事件雷达卡** — 每条事件含 📆 日期 / ⏰ 截止 / 📍 地点 / 报名链接 / 🕒 发布时间，按发布时间倒序，过滤已过期
- **飞书资讯日报卡** — `📰 AI 资讯日报 · YYYY-MM-DD`（同日重推自动加 `#2 #3` 序号）
- **Notion DB** — 7 列 schema（`Title` / `kind` / `digest_date` / `source` / `url` / `summary` / `topic`）。失败入 `data/notion_retry_queue.jsonl`
- **本地 markdown** — `data/digests/daily-{date}.md`，给 OMC `wiki_ingest` 离线用

## 关键路径

```
src/digest/
├── classifier.py        # LLM 分类（DeepSeek/Anthropic/Qwen 多 provider）
├── event_radar.py       # 事件雷达编排
├── daily_digest.py      # 资讯日报编排
├── dedup.py             # URL + char-trigram + 24h 窗口
├── cluster.py           # DeepSeek 主题聚类
├── digest_builder.py    # markdown 渲染
├── push_feishu.py       # 飞书卡片
├── archive_notion.py    # Notion DB 归档
├── store.py             # SQLite schema + helpers
├── url_canonical.py
├── fetchers/rss.py      # feedparser + HTML strip
└── sources/
    ├── newsnow.py       # newsnow API
    └── xhs_skill_bridge.py  # 小红书 MCP 桥
```

## 测试 / 开发

```bash
pytest -q
ruff check .
mypy src
```

192/192 pytest passing, ruff clean, mypy strict clean.

## 文档

- [`ai-digest-workflow.md`](ai-digest-workflow.md) — 产品 spec v2（双轨架构）
- [`docs/UI-HANDOFF.md`](docs/UI-HANDOFF.md) — 给接手做 web UI 的工具
- [`TODO.md`](TODO.md) — 待办
- [`deploy/launchd/README.md`](deploy/launchd/README.md) — macOS 自动化
- [`deploy/wewe-rss/README.md`](deploy/wewe-rss/README.md) — 公众号 RSS 部署
- [`scripts/setup_notion_db.py`](scripts/setup_notion_db.py) — 一次性建 Notion DB

## 设计取舍

- **本地优先** — 数据库 SQLite + 配置 yaml + 自动化 launchd，全在本机跑
- **分类用 LLM 不用规则** — 维护规则库工作量大，LLM prompt 改一句就生效
- **公众号走代理 (wewe-rss)** — 不用申请微信开放平台、不用绕 GFW
- **聚合站走 newsnow** — 站点改版只影响 newsnow，不影响我们
- **Notion = 人类视图，飞书 = 推送通道，wiki = LLM 视图** — 三个 sink 扇出，不互相依赖

## License

私人项目，未发布。代码可读不可商用。
