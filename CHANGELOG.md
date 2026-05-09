# Changelog

All notable changes to ai-digest-workflow.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Added
- 公众号源 `ai_camp` + `infoq`（XHS 风控期 event 供给补充）— `9d7b731`
- 活动行 AI 活动源 `hdx_ai` + `run_fetch` dispatch — `1fbc69f`
- 活动行（huodongxing）AI 活动 fetcher — `319fd4e`
- XHS `enrich_detail` master switch（默认 True，可 opt-out）— `40a2616`
- Feishu Calendar 同步链路（client + store + script + launchd）— `188773a`
- Calendar `--include-undated` 兜底无 `event_date` 的 event — `71487ee`
- 本地 dashboard（FastAPI + HTMX + Jinja2 + 只读 SQLite）— `5338457`
- macOS launchd 自动化（fetch / radar / digest）— `6e403e1`
- newsnow 聚合源接入（6 个 AI/tech 站点）— `864e36a`
- 公众号 wewe-rss 接入 e2e（4 公众号订阅入库）— `c41a120`
- PR-B.1 dedup（URL exact + trigram + 24h window）— `7544c4f`
- PR-B.2 DeepSeek 主题聚类（3-5 主题 / 2-4 条）— `b015d3b`
- PR-B.3 资讯日报 builder + 飞书推 + wiki archive — `c617f00`
- PR-B.4 Notion DB 归档 + 失败 retry queue — `428ecc6`
- Classifier 并发分类（ThreadPoolExecutor，concurrency=8）— `d614850`
- 同日重推 header 加 `#N` 序号 — `dede322`

### Changed
- 活动行 city 子域归一到 `www` 避免跨 city dedup 失效 — `e3f70a6`
- XHS URL canonical 去除 `xsec_token` / `xsec_source` 用于 dedup — `5d809d4`
- Cluster 提高筛选门槛，丢求助/闲聊/凡尔赛 — `294ce9b`
- Card 单行紧凑格式 + 发布时间倒序 — `1e0f1cf` `9c11444`
- Radar 只推未开始活动（`event_date >= today` 或 `NULL`）— `a4c3cbc`

### Fixed
- 招募/找队友/复盘 → other（从 event 踢出）— `f00f6f8`
- RSS `_strip_html` — CDATA + style/script + 标签 + 实体 + 空白 — `2c65932`
- XHS `_extract_detail_fields` 支持 `data.note` 嵌套路径 — `3e94c0b`
- Radar 同日重推不再 FK 崩（`upsert_event_batch_digest`）— `2616dd1`

### Removed
- 临时关闭 `xhs_events`，预设 `enrich_detail=false` — `9911752`
- 删 `insert_digest` 死代码 — `96dbb3f`

---

## [0.1.0] — 初始可运行版本

双轨架构（fetch / radar / digest）+ 12 sources + launchd + Notion 归档。
README 重写于 `fab3e6d`。
