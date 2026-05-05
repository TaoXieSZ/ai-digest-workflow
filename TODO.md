# TODO

> Living list of open items for ai-digest-workflow. Order ≠ priority.

## 业务功能

- [ ] **PR-B 资讯日报** — 把当前进库后无处可去的 `news` / `tool` / `other` 条目（占 ~70%）做 dedup（URL → trigram → LLM 三阶段）+ 主题聚类 + 每日摘要 ≤ 500 字 + 飞书 push + Notion 归档。预计 ~500 行，分 3-4 个内部 commit。spec 在 `ai-digest-workflow.md`。
- [ ] **抽取率提升 — `registration_deadline` / `registration_url`** — 端到端验证后这两个字段填充率仍很低（1/41 和 1/41）。原因是 XHS 帖子习惯写"私信" / 微信二维码而非真实链接 / 截止日期。改进点：
  - prompt 加 few-shot 示例让模型对模糊截止日期（"本周内"、"3 天内"）做相对推断
  - 抽取微信号 / 私信关键词作为 `registration_url` 的 fallback（schema 可能要扩字段）
- [ ] **post-detail rate-limit 优化** — 跑 45 条 XHS feed，17 条 (~38%) 拿到 `isError`。怀疑是 MCP 侧节流。可加：
  - 串行调用之间小睡 200-500ms（目前是连续打）
  - 单 feed 失败重试一次（曝出错误码后再判断）

## 部署 / 运维

- [ ] **scheduled-tasks 接入** — 把 `run_fetch.py` + `run_radar.py` 接 launchd / cron / GitHub Actions schedule，每小时跑一次。`deploy/wewe-rss/` 已有部署文档，但 radar 自己的定时还没接。
- [ ] **wewe-rss 接公众号 RSS** — 文档写了 docker-compose，用户没跑。等于第三个数据源还没启用。
- [ ] **CI** — 项目根有 `.pre-commit-config.yaml` 但本地没跑 `pre-commit install`；GitHub 也没接 Actions。补一个 `.github/workflows/ci.yml` 跑 pytest + ruff + mypy on PR。

## 代码债

- [ ] **`insert_digest` 的 `INSERT OR IGNORE` 隐患** — 已经在 `event_batch` 路径用 `upsert_event_batch_digest` 修了。但 `insert_digest` 还在，PR-B 的 `daily_digest` 用它会再次踩同样的 FK 坑。要么把 `insert_digest` 也改成 upsert + RETURNING，要么彻底删掉只保留 `upsert_*` 系列。
- [ ] **xhs `_extract_detail_fields` shape 容错** — 真实路径是 `inner.data.note.{title,desc}`，已加二级嵌套尝试。但 xiaohongshu-mcp 升级后 shape 可能变。可考虑加一个 metric 在 cache 表里区分 "成功抽到 vs fallback to title"，能给后续做 alerting。
- [ ] **classifier 的 batch 化** — 当前每条 item 一次 HTTP 请求到 DeepSeek。50 条 batch 跑完要 30-50 秒。DeepSeek 没原生 batch API 但可并发 5-10 路 asyncio 提速。

## 数据 / 一次性

- [ ] **重新分类 linux_do 65 条** — 这次只重置了 xhs_events，`linux_do` items 还是用 Bug 1 / Bug 2 修复前的分类结果。如果有 event 被错分到 news/other 不会被推。建议执行：
  ```sql
  UPDATE items SET kind='unclassified', classified_at=NULL WHERE source_id='linux_do';
  DELETE FROM event_metadata WHERE item_id IN (SELECT id FROM items WHERE source_id='linux_do');
  ```
  然后跑 `run_radar.py --dry-run` 检查再决定要不要正式跑。
- [ ] **清理已推 28 条事件的 event_pushes** — 这 28 条事件用旧格式（每条 ~7 行）推到飞书过。新格式（每条 1 行）更紧凑。要不要让它们用新格式重推一次？（需要清 `event_pushes`，会再触发飞书通知。）

## 文档

- [ ] **README** — 当前 README 是 PR1 时期的，没反映双轨架构 + provider 抽象 + 详情 cache。重写 README 让 GitHub repo 第一眼能看懂跑什么。
- [ ] **CHANGELOG** — 5 个 commit 已经在 git log 里能看到，但没结构化的 CHANGELOG.md。如果要发布 v0.1 可以补。

---

_当前测试: 92 pass / ruff clean / mypy strict clean_
_GitHub: https://github.com/TaoXieSZ/ai-digest-workflow_
