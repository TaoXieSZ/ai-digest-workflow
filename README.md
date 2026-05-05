# AI Digest Workflow

中文 AI 圈每日资讯工作流：抓取 → LLM 主题聚合 → 紧凑日报 + 持久库。

- Spec：[`ai-digest-workflow.md`](./ai-digest-workflow.md)
- 实现计划：[`.omc/plans/plan-v2.md`](./.omc/plans/plan-v2.md)

## 当前状态：PR1（项目骨架 + linux.do RSS）

完成范围：
- 项目结构（src layout）+ ruff + mypy + pre-commit + gitleaks
- URL canonicalization（剥 utm/fbclid/spm/from 等中外追踪参数）
- SQLite schema：`sources` / `items` / `topics` / `item_topic_assignments` / `digests`
- 通用 `RSSFetcher`（feedparser-backed）
- linux.do 单源接入
- `scripts/run_fetch.py` 抓取入口
- 单元测试：`test_url_canonical.py` / `test_store.py` / `test_rss_fetcher.py`

未实现（按 plan-v2 PR 切片往后推）：
- PR2 扩 4 个骨干源（v2ex / 即刻 / 掘金 / 少数派）
- PR3 dedup 三阶段
- PR4 cluster (Claude Haiku) + topics + 关联表
- PR5 digest builder + 飞书 push + idempotency
- PR6 Notion archive + 重试队列
- PR7 scheduled-tasks MCP 集成
- PR8（可选）MediaCrawler subprocess + XHS/微博

## 安装

```bash
cd ~/projects/ai-digest-workflow
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

## 跑 PR1 范围

```bash
# 抓一次 linux.do 写入 SQLite
python scripts/run_fetch.py

# 看结果
sqlite3 data/items.db "SELECT id, title, fetched_at FROM items LIMIT 5"
```

## 本地验证（提交前）

```bash
pytest -q
ruff check .
mypy src
```

## 配置

- API keys：复制 `.env.example` → `.env`（PR4+ 才用到）
- 源列表：`config/sources.yaml`
