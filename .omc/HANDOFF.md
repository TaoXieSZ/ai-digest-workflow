# AI Digest Workflow — Session Handoff

> 写给下一个会话开始的 Claude。本文档自包含，不需要回看历史聊天。
> 项目根目录：`~/projects/ai-digest-workflow/`

## 一句话状态

PR-A 端到端跑通了：XHS → DeepSeek 分类 → 飞书事件雷达卡片。**14 个事件已成功推送到飞书**。还剩两个已知 bug 待修。

## 上下文回放（精简版）

1. **deep-interview 7 轮** → 落 spec `ai-digest-workflow.md`，14.5% ambiguity
2. **ralplan 1 轮** Architect+Critic 反馈 10 项 → plan-v2 修复
3. **PR1**：项目骨架 + URL canonical + SQLite + linux.do RSS
4. **架构 pivot**（用户实际痛点是"错过线下 AI 黑客松"，不是日报）→ spec/plan 改为**双轨**：事件雷达 + 资讯日报
5. **PR-A 事件雷达 MVP**：classifier + 飞书 push + event_radar 编排 + xhs_skill_bridge + scheduled-tasks 部署文档
6. **PR-A.1 provider 切换**：DeepSeek/Qwen/OpenAI 兼容（用户从 Anthropic 改用 DeepSeek，深圳网络更稳）
7. **XHS MCP 部署 + 登录**：从 GitHub releases 下 darwin-arm64 二进制到 `~/.local/bin/`
8. **首次端到端**：抓 36 条 XHS + 65 条 linux.do，分类 14 个 event（"深圳 AI 小聚"、"美团黑客松找队友"等真实活动），临时关静默推送 → 飞书 200 OK

## 真实状态（核对自最后一次跑）

| 项 | 值 | 命令 |
|----|----|------|
| 测试 | 71/71 pass / ruff clean / mypy strict clean | `.venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/mypy src` |
| linux_do items | 65 条 | `sqlite3 data/items.db "SELECT count(*) FROM items WHERE source_id='linux_do'"` |
| xhs_events items | 36 条 | 同上 source_id='xhs_events' |
| 已推送事件 | 14 条 | `sqlite3 data/items.db "SELECT count(*) FROM event_pushes"` |
| XHS MCP 进程 | **已停**（重启需见下） | `curl -s -X POST http://localhost:18060/mcp ...` 无响应 |
| Git | 未 init / 未 commit | `cd ~/projects/ai-digest-workflow && git status` |

## 已知 Bug（PR-A 收尾）

### Bug 1：事件日期年份错

DeepSeek 抽出的 `event_date` 都写成 2025（应为 2026）。例：
- "4.23 Attrax 黑客松" → 抽到 `2025-04-23`，应该是 `2026-04-23`

**根因**：prompt 里没注入"今天是哪一天"，模型默认用训练时的年份。

**修法**：`src/digest/classifier.py` 的 `_PROMPT` 模板里加一行：
```
今天日期：%(today)s（你必须用这个年份做日期推断）
```
然后 `Classifier.classify()` 在 `prompt = _PROMPT % {...}` 时传 `today=date.today().isoformat()`。
约 5 分钟。

### Bug 2：XHS 没拉正文

XHS bridge 只用了搜索结果里的 `noteCard.displayTitle` 作为 title + `noteCard.desc` 作为 content。但 `desc` 经常为空。所以分类器看到的"正文"几乎只有标题，导致：
- `registration_deadline` 14/14 全空
- `registration_url` 14/14 全空
- `location` 9/14 抽到（依赖标题里就有"深圳"字样）

**修法**：在 `xhs_skill_bridge.py` 的 `_feed_to_item` 之后，**对每个 feed 调用 `post-detail.sh <feed_id> <xsec_token>`** 拉完整正文。但这会让单源耗时从 ~60s 涨到 N×3-5s（视 N 个结果）。

**取舍**：是否值得加？我的看法是值得 —— 但要做带 cache：同一 `(feed_id, xsec)` 拉过一次就缓存到 SQLite 一个新表 `xhs_note_details`，下次跳过。这样只对新 feed 慢一次。

约 30-40 分钟（含缓存 + 测试）。

## 下次会话三个候选起点（按推荐度）

### A. 修这两个 bug 然后再推一次（最闭环）

1. 修 Bug 1（5 分钟）
2. 重置 14 个 event 的 classified_at + event_metadata（让它们重新分类）：
   ```bash
   sqlite3 data/items.db "UPDATE items SET classified_at=NULL, kind='unclassified' WHERE source_id='xhs_events'; DELETE FROM event_metadata; DELETE FROM event_pushes;"
   ```
3. 重启 XHS MCP（见下方）
4. 跑 `python scripts/run_radar.py`，验证 event_date 现在是 2026
5. 修 Bug 2（30-40 分钟，含 cache）
6. 同上重置 + 重跑，验证 registration_deadline / location 抽全
7. 把 14 条修好的事件再推一次到飞书

### B. git init + commit（锁定当前状态）

用户 CLAUDE.md 不允许自动 push，但 commit 是 OK 的（commit 后停下让用户 review）。建议 3 个独立 commit：
1. `feat: PR1 项目骨架 + linux.do RSS fetcher`
2. `feat: PR-A 事件雷达（classifier + 飞书 push + XHS bridge + 部署文档）`
3. `feat: PR-A.1 LLM provider 抽象（DeepSeek/Qwen/OpenAI 兼容）`

不要 push 到 remote（用户没建 remote）。

### C. 进 PR-B 资讯日报

事件雷达已工作；剩下的 36% 非 event 条目（other/news/tool）目前进库后无处可去。PR-B 实现 daily digest：
- dedup 三阶段（URL → trigram → LLM）
- 主题聚类（DeepSeek，复用 PR-A 的 client）
- digest builder（≤ 500 字）
- 每日 push 到飞书 + Notion 归档

预计 ~500 行，分 3-4 个内部 commit。

## 重启 XHS MCP（两个起点都需要）

```bash
mkdir -p ~/.xiaohongshu
nohup ~/.local/bin/xiaohongshu-mcp -port ":18060" > ~/.xiaohongshu/mcp.log 2>&1 &
echo $! > ~/.xiaohongshu/mcp.pid
sleep 2
~/.claude/skills/xiaohongshu/scripts/status.sh    # 应该看到 ✅ 已登录
```

cookies 已持久化（`/tmp/cookies.json` 或 `~/.xiaohongshu/cookies.json`），重启不需要重新扫码（只要 cookie 没过期）。

## 关键路径速查

```
~/projects/ai-digest-workflow/
├── ai-digest-workflow.md            # spec v2 双轨架构
├── README.md                        # 项目入口
├── pyproject.toml                   # 依赖（anthropic + openai + httpx + feedparser）
├── .env                             # 用户已填（DEEPSEEK_API_KEY + FEISHU_WEBHOOK_URL）
├── .omc/
│   ├── HANDOFF.md                   # 本文件
│   └── plans/
│       ├── plan-v1.md               # ralplan 打回的草稿
│       └── plan-v2.md               # 当前 plan（含 PR-A/B/C rollup）
├── config/sources.yaml              # linux_do (rss) + xhs_events (xhs)
├── src/digest/
│   ├── classifier.py                # AnthropicClient / OpenAICompatibleClient / make_client_from_env
│   ├── event_radar.py               # 编排 + is_quiet_hours
│   ├── push_feishu.py               # webhook 卡片 + 文本
│   ├── store.py                     # 5 表 + 迁移 + EWMA
│   ├── url_canonical.py             # 剥 utm/spm/from
│   ├── fetchers/                    # base.py / rss.py
│   └── sources/xhs_skill_bridge.py  # XHS subprocess 桥
├── scripts/
│   ├── run_fetch.py                 # 抓取 CLI（rss + xhs dispatcher）
│   └── run_radar.py                 # 事件雷达 CLI
├── deploy/wewe-rss/                 # 公众号 RSS 部署文档（用户没跑）
├── tests/                           # 71 tests
└── data/items.db                    # SQLite，体积约 100 KB
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
读 ~/projects/ai-digest-workflow/.omc/HANDOFF.md，然后告诉我你想从哪条路开始（A/B/C），或者先看一眼 git diff 锁定当前状态。
```
