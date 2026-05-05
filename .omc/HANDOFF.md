# AI Digest Workflow — Session Handoff

> 写给下一个会话开始的 Claude。本文档自包含，不需要回看历史聊天。
> 项目根目录：`~/projects/ai-digest-workflow/`

## 一句话状态

PR-A 端到端跑通。**Bug 1 修好且生产验证通过，Bug 2 修好但端到端验证被 XHS 登录卡住**。仓库已 git init + 1 个 baseline commit（未 push remote）。等用户做两个决定：(a) 重新登录 XHS MCP 跑完整端到端验 Bug 2；(b) 是否再推 14 条事件到飞书。

## 上下文回放（精简版）

1. **deep-interview 7 轮** → 落 spec `ai-digest-workflow.md`，14.5% ambiguity
2. **ralplan 1 轮** Architect+Critic 反馈 10 项 → plan-v2 修复
3. **PR1**：项目骨架 + URL canonical + SQLite + linux.do RSS
4. **架构 pivot**（用户实际痛点是"错过线下 AI 黑客松"）→ spec/plan 改为**双轨**：事件雷达 + 资讯日报
5. **PR-A 事件雷达 MVP**：classifier + 飞书 push + event_radar 编排 + xhs_skill_bridge + scheduled-tasks 部署文档
6. **PR-A.1 provider 切换**：DeepSeek/Qwen/OpenAI 兼容
7. **XHS MCP 部署 + 登录**：从 GitHub releases 下 darwin-arm64 二进制到 `~/.local/bin/`
8. **首次端到端**：14 个 event 推到飞书 200 OK
9. **本次会话（2026-05-05）**：修两个 bug，git init + baseline commit

## 真实状态（核对自最后一次跑，2026-05-05 10:43 UTC）

| 项 | 值 | 命令 |
|----|----|------|
| 测试 | **89/89 pass** / ruff clean / mypy strict clean | `.venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/mypy src` |
| linux_do items | 65 条 | `sqlite3 data/items.db "SELECT count(*) FROM items WHERE source_id='linux_do'"` |
| xhs_events items | 36 条（13 event + 1 news + 21 other + 1 tool）| 同上 source_id='xhs_events' |
| event_metadata 行 | 13（重新分类后）| `sqlite3 data/items.db "SELECT count(*) FROM event_metadata"` |
| event_pushes 行 | **14**（保留，未清理）| 同上 event_pushes |
| 已推送事件 | 14 条（首次推送，未做第二次）| 同上 |
| XHS MCP 进程 | **已起但未登录**（cookies 过期/丢失）| `~/.claude/skills/xiaohongshu/scripts/status.sh` |
| Git | **已 init**, 1 commit `eae589e`, 未 push remote | `git log --oneline` |
| 备份 | `data/items.db.bak-20260505-104253` | `ls data/*.bak-*` |

## 本次会话做了什么

### Bug 1 ✅ 完全修好

`src/digest/classifier.py`：
- 在 `_PROMPT` 模板里加一行 `今天日期：%(today)s`
- `Classifier.classify()` 传 `today=date.today().isoformat()`
- 加单元测试 `test_classifier_injects_today_into_prompt`

**生产验证**：清掉 14 个 event 的 classified_at 和 event_metadata，重新跑 `python scripts/run_radar.py --dry-run`，结果：
- 14 个 item 全部重新分类（13 个仍然 event，1 个变 news — 模型温度 0 但 DeepSeek 不完全确定性）
- 所有抽到的 event_date 都是 **2026 年**：
  - "4.23 Attrax 黑客松" → `2026-04-23` ✅（之前是 `2025-04-23`）
  - "5.5 深圳·南山五一 AI 交流" → `2026-05-05` ✅
  - "5.7-6.30 美团黑客松" → `2026-05-07` ✅
- 0 条带 2025 日期

### Bug 2 ✅ 代码 + 单元测试完整，端到端验证被登录卡住

新增 `src/digest/store.py` 表 `xhs_note_details(feed_id PK, xsec_token, title, content, fetched_at)` + `SqliteDetailCache` 类。

新增 `src/digest/sources/xhs_skill_bridge.py`：
- `XHSDetailCache` Protocol
- `XHSConfig.detail_cache: XHSDetailCache | None = None`（默认 None = 关掉这个特性）
- `_enrich_with_detail`：cache 命中跳过 subprocess；miss 调 `post-detail.sh <feed_id> <xsec_token>`，解析 MCP JSON-RPC 响应，写入 cache
- **fail-soft 设计**：post-detail subprocess 失败、超时、MCP isError、JSON 解析失败 — 任何一种异常都不会让整个 fetch 挂掉，只 log warning 并保留原 desc 作为 fallback
- `_extract_detail_fields`：尝试多种 inner JSON 形状（`inner.noteCard` / `inner.feed.noteCard` / `inner.data.noteCard`，desc 或 content 字段），因为我没有登录的 MCP 来探真实 shape

`scripts/run_fetch.py`：xhs 类型的 source 实例化 XHSConfig 时注入 `SqliteDetailCache(conn)`。

**单元测试** `tests/test_xhs_bridge.py`（17 个）：
- shape 容错（4 种 inner JSON 路径）
- cache 读写 round-trip
- enrich：cache hit 不调 subprocess、miss 调 + 写 cache、subprocess 非 0 退出 fail-soft、MCP isError fail-soft、subprocess timeout fail-soft、post-detail.sh 不存在跳过、feed 缺 id/xsec 跳过、cache=None 时 no-op

**为什么端到端没验**：XHS MCP 起来了但 cookies 过期/丢失，登录失败 → 抓不到新 feed → 拿不到 feed_id+xsec → 没法触发 post-detail 路径。需要用户扫二维码重新登录。

### Git 历史

只做了一个 commit（`eae589e`）。原因：开始干活时还没 git，PR1/PR-A/PR-A.1 + Bug 1 + Bug 2 的改动都已经混在工作树里，没法干净地按时间切分。备选方案是手工 revert 文件再分次提交，但易错。如果你想拆历史，可以用 `git rebase -i --root` + 把当前 commit 拆开（每次 reset 一些文件再分次 commit），但工作量不小。

### 数据库备份

跑 `--dry-run` 重分类前备份了 `data/items.db.bak-20260505-104253`。如果重分类结果不满意可以恢复。确认 OK 后可以 `rm data/items.db.bak-*`。

## 已知 Bug（PR-A 收尾）

### Bug 1 — 已修复 ✅（见上）

### Bug 2 — 代码已修但需要登录才能端到端验

`xhs_note_details` 表已建（schema 已迁移），cache 类已在 run_fetch 里 wired up，但因为 cookies 过期没登录，下次抓 XHS 之前必须扫码登录：

```bash
~/.claude/skills/xiaohongshu/scripts/login.sh   # 出二维码，用小红书 App 扫
```

登录后，**关键的端到端验证步骤**：

```bash
# 1. 抓一次 XHS（这一步会触发 detail 拉取并写 cache）
.venv/bin/python scripts/run_fetch.py

# 2. 看 cache 表是不是有内容了
sqlite3 data/items.db "SELECT count(*), avg(length(content)) FROM xhs_note_details"

# 3. 重置已分类的 xhs events（只清 event_metadata + classified_at；event_pushes 保留以防误推）
sqlite3 data/items.db "
UPDATE items SET kind='unclassified', classified_at=NULL WHERE source_id='xhs_events' AND kind='event';
DELETE FROM event_metadata WHERE item_id IN (SELECT id FROM items WHERE source_id='xhs_events');
"

# 4. 重新分类（dry-run，不推飞书）
.venv/bin/python scripts/run_radar.py --dry-run

# 5. 看 registration_deadline / location 抽全率
sqlite3 data/items.db "
SELECT
  count(*) total,
  sum(CASE WHEN event_date IS NOT NULL THEN 1 ELSE 0 END) has_date,
  sum(CASE WHEN registration_deadline IS NOT NULL THEN 1 ELSE 0 END) has_deadline,
  sum(CASE WHEN location IS NOT NULL THEN 1 ELSE 0 END) has_location,
  sum(CASE WHEN registration_url IS NOT NULL THEN 1 ELSE 0 END) has_url
FROM event_metadata em JOIN items i ON i.id=em.item_id
WHERE i.source_id='xhs_events';
"
```

如果 has_deadline / has_url 比之前的 1/0、0/0 高很多，说明 Bug 2 端到端 OK。

**风险点**：`_extract_detail_fields` 是我盲写的，因为没法登录探真实 shape。如果 `xhs_note_details.content` 是空的（拉到了但解析失败），需要查 MCP 实际响应：
```bash
~/.claude/skills/xiaohongshu/scripts/post-detail.sh <feed_id> <xsec_token>
```
然后调 `_extract_detail_fields` 里的候选路径。

## 等用户回来决定的事

### 决定 1：要不要把（修好的）14 条事件再推一次到飞书

数据库现在的状态：14 个 event_pushes 仍然在（首次推送的记录），但 event_metadata 被我清掉重抽了 13 行。**目前 13 个 event 全部 has been "已推送"（event_pushes 表里有）**，所以下一次正常 `run_radar.py` 不会再推。

如果要把修好年份的版本推一次：
```bash
sqlite3 data/items.db "DELETE FROM event_pushes WHERE item_id IN (SELECT id FROM items WHERE source_id='xhs_events')"
.venv/bin/python scripts/run_radar.py   # 注意：会真推到飞书
```
注意夜深时段会被 quiet_hours（默认 23-7）静默；想立即推改 `QUIET_HOURS_START=99 QUIET_HOURS_END=99` env。

### 决定 2：要不要 push 到 GitHub remote

仓库还没建 remote。要 push 的话：
```bash
gh repo create txie/ai-digest-workflow --private --source=. --remote=origin --push
```
我没自动建（"不自动 push" 规则）。

### 决定 3：要不要拆 baseline commit 成多个细粒度 commit

当前一个大 commit 包含 PR1+PR-A+PR-A.1+Bug1+Bug2。如果你介意 review 粒度，可以：
- 手工 reset HEAD~1 → 重新分次 stage + commit
- 或保留一大 commit，从这往后所有新工作都做小 commit

我建议保留现状 — 反正你回头看 commit history 都能从代码里看出来分层。

## 下次会话三个候选起点

### A. 完成 Bug 2 端到端验证（推荐 — 闭环）
按上面 Bug 2 章节的 5 步走完。然后决定要不要重推飞书。预计 10-15 分钟（不含登录扫码）。

### B. 进 PR-B 资讯日报
事件雷达稳定了，剩下 23 条 other + 1 news + 1 tool 没去处。PR-B 实现 daily digest：
- dedup 三阶段（URL → trigram → LLM）
- 主题聚类（DeepSeek，复用 PR-A 的 client）
- digest builder（≤ 500 字）
- 每日 push 到飞书 + Notion 归档
预计 ~500 行，分 3-4 个内部 commit。

### C. 推到 GitHub + 接 CI
建仓 + 接一个最小 CI（pytest + ruff + mypy）。约 30 分钟。

## 重启 XHS MCP（如果停了）

```bash
mkdir -p ~/.xiaohongshu
nohup ~/.local/bin/xiaohongshu-mcp -port ":18060" > ~/.xiaohongshu/mcp.log 2>&1 &
echo $! > ~/.xiaohongshu/mcp.pid
sleep 3
~/.claude/skills/xiaohongshu/scripts/status.sh
# 没登录就：
~/.claude/skills/xiaohongshu/scripts/login.sh
```

## 关键路径速查

```
~/projects/ai-digest-workflow/
├── ai-digest-workflow.md            # spec v2 双轨架构
├── README.md                        # 项目入口
├── pyproject.toml                   # 依赖（anthropic + openai + httpx + feedparser）
├── .env                             # DEEPSEEK_API_KEY + FEISHU_WEBHOOK_URL（不入 git）
├── .gitignore                       # 含 .env, data/items.db, .omc/state, .omc/sessions
├── .omc/
│   ├── HANDOFF.md                   # 本文件
│   └── plans/
│       ├── plan-v1.md               # ralplan 打回的草稿
│       └── plan-v2.md               # 当前 plan
├── config/sources.yaml              # linux_do (rss) + xhs_events (xhs)
├── src/digest/
│   ├── classifier.py                # AnthropicClient / OpenAICompatibleClient + today injection
│   ├── event_radar.py               # 编排 + is_quiet_hours
│   ├── push_feishu.py               # webhook 卡片 + 文本
│   ├── store.py                     # 6 表 + xhs_note_details + SqliteDetailCache
│   ├── url_canonical.py             # 剥 utm/spm/from
│   ├── fetchers/                    # base.py / rss.py
│   └── sources/xhs_skill_bridge.py  # XHS subprocess 桥 + 详情 cache 集成
├── scripts/
│   ├── run_fetch.py                 # 抓取 CLI（注入 SqliteDetailCache）
│   └── run_radar.py                 # 事件雷达 CLI（含 --dry-run）
├── deploy/wewe-rss/                 # 公众号 RSS 部署文档（用户没跑）
├── tests/                           # 89 tests（新增 17 个 xhs_bridge）
└── data/
    ├── items.db                     # SQLite live data
    └── items.db.bak-20260505-104253 # 重分类前备份（确认 OK 可删）
```

## 用户偏好（来自 ~/CLAUDE.md 协作准则，强相关条目）

- **不自动 push** — commit 后停下，等用户确认
- **每个改动展示 diff** — 不止口头说"改好了"
- **小 PR 优先** — <400 行
- **提交前本地验证** — pytest + ruff + mypy 必须全绿才 commit
- **关键假设必须显式确认** — 架构方向不可逆决策必须先问用户
- **结构化提问** — 模糊意图给 2-4 个文字选项，语音输入友好

## 下次开场建议

```
读 ~/projects/ai-digest-workflow/.omc/HANDOFF.md，告诉我：
(1) 你扫码登录 XHS 了没（Bug 2 端到端要登录）
(2) 14 条事件要不要重推飞书
(3) 仓库要不要 push 到 GitHub
然后从路线 A/B/C 选一个开始。
```
