# TODO

> Living list of open items for ai-digest-workflow. Order ≠ priority.

## 业务功能

- [x] ~~**PR-B 资讯日报**~~ — B.1 dedup / B.2 cluster / B.3 builder+push / B.4 Notion archive 全部完成（commits `7544c4f` `b015d3b` `c617f00` `428ecc6`）。
- [x] ~~**PR-B 字段抽取微调**~~ — budget 500 → 2500，4-5 主题完整渲染（commit `0f9b42e`）
- [ ] **PR-B retry queue 重放脚本** — `data/notion_retry_queue.jsonl` 里失败的 item 现在没人重放。补 `scripts/replay_notion_queue.py`：读 jsonl → 重 POST → 成功的从 queue 移除（用 .tmp 重写）。
- [x] ~~**PR-B linux_do 扩源**~~ — 已加 4 公众号 + 6 newsnow 源（共 12 sources），dedup 实际有效（commit `c41a120` `0f9b42e` `864e36a`）
- [x] ~~**OMC wiki 自动 ingest**~~ — 加 launchd 第 5 个 job `com.txie.ai-digest.wiki_ingest`，每天 12:30（digest 跑完后 30 min）spawn `claude -p` 调 OMC `wiki_ingest` MCP tool。runner: `deploy/launchd/wiki_ingest.sh`，找不到当天 digest 时静默 skip。年成本 ~$5。
- [x] ~~**抽取率提升 — `registration_deadline` / `registration_url`**~~ — prompt 加相对日期推断规则（"本周内"→今周日 / "3天内"→today+3 / "五一前"→2026-04-30）；新增 `registration_contact` 字段（schema + classifier + push_feishu + calendar_sync）专门承接非 URL 的 私信/微信号/扫码 报名方式。push_feishu 渲染 `📩 contact`，calendar_sync 描述加 `报名方式: contact`，url 优先级高于 contact。
- [ ] **post-detail rate-limit 优化** — 跑 45 条 XHS feed，17 条 (~38%) 拿到 `isError`。怀疑是 MCP 侧节流。可加：
  - 串行调用之间小睡 200-500ms（目前是连续打）
  - 单 feed 失败重试一次（曝出错误码后再判断）

## 部署 / 运维

- [x] ~~**scheduled-tasks 接入**~~ — launchd 装好（fetch/30min, radar/1h, digest/12:00），install.sh / uninstall.sh / run.sh 全套（commit `6e403e1`）
- [x] ~~**wewe-rss 接公众号 RSS**~~ — Docker 跑起，4 公众号订阅入库（commits `c41a120` 等）
- [ ] **CI** — 项目根有 `.pre-commit-config.yaml` 但本地没跑 `pre-commit install`；GitHub 也没接 Actions。补一个 `.github/workflows/ci.yml` 跑 pytest + ruff + mypy on PR。

## 代码债

- [x] ~~**`insert_digest` 的 `INSERT OR IGNORE` 隐患**~~ — PR-B.3 加了 `upsert_daily_digest`，daily_digest 路径不再用 `insert_digest`；它现在零调用方，可以择期删除。
- [x] ~~**删掉 `insert_digest`**~~ — 已删（commit `d614850` 之后）
- [ ] **xhs `_extract_detail_fields` shape 容错** — 真实路径是 `inner.data.note.{title,desc}`，已加二级嵌套尝试。但 xiaohongshu-mcp 升级后 shape 可能变。可考虑加一个 metric 在 cache 表里区分 "成功抽到 vs fallback to title"，能给后续做 alerting。
- [x] ~~**classifier 的 batch 化**~~ — 用 ThreadPoolExecutor concurrency=8，50 条 30s → 7s（commit `d614850`）

## 数据 / 一次性

- [x] ~~**重新分类 linux_do**~~ — 已核查无需执行。唯一的 classifier prompt 修复（commit `f00f6f8`，5/5 18:10）方向是 `招募/找队友/复盘 event→other`（减 event），不会有"event 被错分到 non-event"的情况；5/5 之前 95 条样本标题也不含相关关键词。0 个 linux_do event，event_pushes/event_metadata 都不受影响。
- [x] ~~**清理已推 28 条事件的 event_pushes**~~ — 已用新格式重推 25 条 ✓
- [x] ~~**招募/找队友/复盘 prompt 修复**~~ — commit `f00f6f8` 已修，event 41→27 ✓

## 文档

- [x] ~~**README**~~ — 重写完，反映双轨架构 + 12 sources + launchd + Notion（commit `fab3e6d`）
- [ ] **CHANGELOG** — 5 个 commit 已经在 git log 里能看到，但没结构化的 CHANGELOG.md。如果要发布 v0.1 可以补。

---

_当前测试: 92 pass / ruff clean / mypy strict clean_
_GitHub: https://github.com/TaoXieSZ/ai-digest-workflow_
