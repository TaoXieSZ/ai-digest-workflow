# TODO

> Living list of open items for ai-digest-workflow. Order ≠ priority.

## 业务功能

- [x] ~~**PR-B 资讯日报**~~ — B.1 dedup / B.2 cluster / B.3 builder+push / B.4 Notion archive 全部完成（commits `7544c4f` `b015d3b` `c617f00` `428ecc6`）。
- [x] ~~**PR-B 字段抽取微调**~~ — budget 500 → 2500，4-5 主题完整渲染（commit `0f9b42e`）
- [ ] **PR-B retry queue 重放脚本** — `data/notion_retry_queue.jsonl` 里失败的 item 现在没人重放。补 `scripts/replay_notion_queue.py`：读 jsonl → 重 POST → 成功的从 queue 移除（用 .tmp 重写）。
- [x] ~~**PR-B linux_do 扩源**~~ — 已加 4 公众号 + 6 newsnow 源（共 12 sources），dedup 实际有效（commit `c41a120` `0f9b42e` `864e36a`）
- [ ] **OMC wiki 自动 ingest** — `data/digests/daily-{date}.md` 已经写出来了，但还要手动跑 `wiki_ingest`。可以加一个 cron Claude session 每天调一下。
- [ ] **抽取率提升 — `registration_deadline` / `registration_url`** — 端到端验证后这两个字段填充率仍很低（1/41 和 1/41）。原因是 XHS 帖子习惯写"私信" / 微信二维码而非真实链接 / 截止日期。改进点：
  - prompt 加 few-shot 示例让模型对模糊截止日期（"本周内"、"3 天内"）做相对推断
  - 抽取微信号 / 私信关键词作为 `registration_url` 的 fallback（schema 可能要扩字段）
- [ ] **post-detail rate-limit 优化** — 跑 45 条 XHS feed，17 条 (~38%) 拿到 `isError`。怀疑是 MCP 侧节流。可加：
  - 串行调用之间小睡 200-500ms（目前是连续打）
  - 单 feed 失败重试一次（曝出错误码后再判断）

## 部署 / 运维

- [ ] **scheduled-tasks 接入** — 把 `run_fetch.py` + `run_radar.py` 接 launchd / cron / GitHub Actions schedule，每小时跑一次。`deploy/wewe-rss/` 已有部署文档，但 radar 自己的定时还没接。
- [x] ~~**wewe-rss 接公众号 RSS**~~ — Docker 跑起，4 公众号订阅入库（commits `c41a120` 等）
- [ ] **CI** — 项目根有 `.pre-commit-config.yaml` 但本地没跑 `pre-commit install`；GitHub 也没接 Actions。补一个 `.github/workflows/ci.yml` 跑 pytest + ruff + mypy on PR。

## 代码债

- [x] ~~**`insert_digest` 的 `INSERT OR IGNORE` 隐患**~~ — PR-B.3 加了 `upsert_daily_digest`，daily_digest 路径不再用 `insert_digest`；它现在零调用方，可以择期删除。
- [ ] **删掉 `insert_digest`** — 现在零调用方，留着是死代码。
- [ ] **xhs `_extract_detail_fields` shape 容错** — 真实路径是 `inner.data.note.{title,desc}`，已加二级嵌套尝试。但 xiaohongshu-mcp 升级后 shape 可能变。可考虑加一个 metric 在 cache 表里区分 "成功抽到 vs fallback to title"，能给后续做 alerting。
- [ ] **classifier 的 batch 化** — 当前每条 item 一次 HTTP 请求到 DeepSeek。50 条 batch 跑完要 30-50 秒。DeepSeek 没原生 batch API 但可并发 5-10 路 asyncio 提速。

## 数据 / 一次性

- [ ] **重新分类 linux_do 65 条** — 这次只重置了 xhs_events，`linux_do` items 还是用 Bug 1 / Bug 2 修复前的分类结果。如果有 event 被错分到 news/other 不会被推。建议执行：
  ```sql
  UPDATE items SET kind='unclassified', classified_at=NULL WHERE source_id='linux_do';
  DELETE FROM event_metadata WHERE item_id IN (SELECT id FROM items WHERE source_id='linux_do');
  ```
  然后跑 `run_radar.py --dry-run` 检查再决定要不要正式跑。
- [x] ~~**清理已推 28 条事件的 event_pushes**~~ — 已用新格式重推 25 条 ✓
- [x] ~~**招募/找队友/复盘 prompt 修复**~~ — commit `f00f6f8` 已修，event 41→27 ✓

## 文档

- [ ] **README** — 当前 README 是 PR1 时期的，没反映双轨架构 + provider 抽象 + 详情 cache。重写 README 让 GitHub repo 第一眼能看懂跑什么。
- [ ] **CHANGELOG** — 5 个 commit 已经在 git log 里能看到，但没结构化的 CHANGELOG.md。如果要发布 v0.1 可以补。

---

_当前测试: 92 pass / ruff clean / mypy strict clean_
_GitHub: https://github.com/TaoXieSZ/ai-digest-workflow_
