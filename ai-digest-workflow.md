# AI Digest Workflow — Spec

> 中文 AI 圈每日资讯工作流：抓取 → LLM 主题聚合 → 紧凑日报 + 持久库

## Metadata

- **Interview ID**: deep-interview-ai-digest-workflow
- **Rounds**: 7
- **Final Ambiguity**: 14.5%（阈值 20%，PASSED）
- **Type**: greenfield
- **Generated**: 2026-05-04
- **Owner**: txie（深圳，中文互联网环境）

## Clarity Breakdown

| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.90 | 40% | 0.360 |
| Constraint Clarity | 0.85 | 30% | 0.255 |
| Success Criteria | 0.80 | 30% | 0.240 |
| **Total Clarity** | | | **0.855** |
| **Ambiguity** | | | **0.145** |

## Goal

> **2026-05-04 修订（pivot v2）**：用户真实痛点是"错过线下 AI 活动报名时间"（一次错过黑客松展会触发），不是日常信息聚合。架构改为**双轨**：
> - **事件雷达**（高优先级、时效敏感）— 抓到 event 类条目立即抽取结构化字段（活动日期/报名截止/地点/报名链接）→ 整点批量飞书推送
> - **资讯日报**（低优先级、聚合容忍）— 非 event 类条目走原 daily digest + Notion 归档

每天定时抓取中文 AI 圈的活动、新闻、工具资讯，用 LLM 分类后**双轨处理**：
- **事件类**（黑客松、meetup、conference、招募）→ 整点批量飞书推送，含报名截止 + 活动日期 + 地点 + 报名链接
- **新闻/工具类** → 主题聚合成紧凑日报，飞书推送 + Notion 归档

一句话定义：**"事件雷达（不漏报名）+ 资讯日报（不淹没）"**。

## Constraints

### 数据源（按事件信息密度重排）

> **2026-05-04 重排**：事件类内容密度 公众号 > XHS > 微博 > 即刻 > 论坛。优先级跟着内容密度走。

**Tier 1（事件信息密度最高）**：
- **微信公众号**（机器之心、量子位、AICon、深圳本地活动公众号）— 自部署 [we-mp-rss](https://github.com/rachelos/we-mp-rss)（Docker），产出 RSS 由 PR1 已有的 RSSFetcher 消费
- **小红书**（深圳本地账号、个人 vlog 招募）— 直接调用已装的 `xiaohongshu` skill（xpzouying/xiaohongshu-mcp）

**Tier 2（中等信息密度）**：
- **微博** — 用 [qinyuanpei/mcp-server-weibo](https://github.com/qinyuanpei/mcp-server-weibo) MCP
- **即刻** — RSS

**Tier 3（噪音多但偶有信号）**：
- **linux.do**（PR1 已实现）
- **V2EX / 掘金 / 少数派** — RSS

**非目标**：Twitter/X、Reddit、HN、ProductHunt 等英文站不在此次范围（深圳网络环境，不依赖 VPN）。

### 频率与时序（双轨）

| 轨道 | 抓取频率 | 推送频率 | 空窗口处理 |
|------|----------|----------|------------|
| **事件雷达** | 每 4 小时 | **整点批量推送**（用户偏好：避免立即打扰） | 无新事件 → 不推送 |
| **资讯日报** | 每 4 小时 | 每日一次 digest | 当日 0 条 → 不推送 |

- **去重窗口**：7 天滚动
- **静默时段**：23:00-07:00 不推送任何东西（待实现，PR-A 后期）

### 投递通道

- **事件推送**：飞书 webhook（结构化卡片：活动日期 / 报名截止 / 地点 / 报名链接）
- **资讯 digest**：飞书 webhook（紧凑型 ≤ 500 字）
- **持久库**：Notion DB，`kind` 字段区分 event/news/tool

### 内容范围

AI 相关的混合资讯：
- **活动类**：线下/线上 meetup、会议、hackathon、workshop（重深圳/北京/上海）
- **新闻类**：模型发布、公司动态、产品更新
- **工具类**：新出 AI 应用、Prompt 技巧、玩法

## Non-Goals

- ❌ ~~雷达预警模式~~ → **2026-05-04 撤销，雷达就是核心需求**
- ❌ 二次创作素材库（独立 layer）—— Notion DB 兼任即可
- ❌ 英文源覆盖
- ❌ 即时推送（事件雷达走整点批量，用户偏好：避免凌晨打扰）
- ❌ 多人共享 / 团队订阅功能
- ❌ Telegram 推送 → 改飞书（GFW + 用户深圳无 VPN）

## Acceptance Criteria

### 事件雷达（核心，必须达成）

- [ ] LLM 分类器把 item 分到 `event` / `news` / `tool` 三类，event 类抽取出 `活动日期 / 报名截止 / 地点 / 报名链接` 至少 3 个字段
- [ ] event 类 item 在下一个整点（且非 23:00-07:00 静默窗）推送到飞书，格式为结构化卡片
- [ ] 同一活动在 ≥2 个源出现时，飞书只推送一次（item_id 级 idempotency + 7 天去重）
- [ ] 已推送的 event 不重复推送（`digests.pushed_at` 检查）

### 资讯日报（次要）

- [ ] 非 event 类条目按 plan-v2 原 digest 流程（3-5 主题 / 2-4 条/主题 / ≤500 字）
- [ ] 每条目附带原始链接 + 来源 + Notion 链接
- [ ] Notion DB 区分 `kind` 字段，event 单独 view、news/tool 合并 view

### 共同

- [ ] Tier 1 源（公众号 / XHS）抓取成功率 ≥ 90%（Tier 2/3 best-effort）
- [ ] 单源失败不阻塞 pipeline
- [ ] 23:00-07:00 静默时段不推送任何东西
- [ ] 整个 pipeline 在深圳本地无 VPN 运行

## Assumptions Exposed & Resolved

| 假设 | 挑战时机 | 解决 |
|------|----------|------|
| "AI 资讯"是单一类别 | Round 1 | 实为活动+新闻+工具混合（Goal D） |
| 必须从 XHS/微博抓 | Round 4（Contrarian） | 改为中文站灵活方案，linux.do 入列，XHS/微博 best-effort |
| 周报够用 | Round 6（Simplifier） | 用户拒绝，坚持日报 + 双通道 |
| 量大不限 | Round 7 | 紧凑型（≤500 字、3-5 主题）锁死 |

## Pipeline Architecture（高层，留给下阶段细化）

```
┌────────────┐  4h cron  ┌──────────────┐    ┌──────────────┐
│  数据源    │──────────>│  抓取 Worker │───>│  原始条目仓  │
│ linux.do   │           │ (并发, fail- │    │  (SQLite or  │
│ 即刻/V2EX  │           │  isolated)   │    │   Notion)    │
│ 掘金/少数派│           └──────────────┘    └──────┬───────┘
│ XHS/微博 ?│                                       │
└────────────┘                                      │ daily
                                                    ▼
                              ┌─────────────────────────────────┐
                              │  LLM 主题聚合 + 7 天去重         │
                              │  (Claude Haiku/Sonnet)           │
                              └─────────┬───────────────────────┘
                                        │
                          ┌─────────────┴────────────┐
                          ▼                          ▼
                   ┌──────────────┐          ┌──────────────┐
                   │ TG Bot 推送  │          │ Notion DB    │
                   │ (紧凑 digest)│          │ (持久库)     │
                   └──────────────┘          └──────────────┘
```

## Ontology

| Entity | Type | 关键字段 | 关系 |
|--------|------|----------|------|
| 资讯条目 (Item) | core | id, url, source, title, content, fetched_at, topic | belongs_to 数据源；assigned_to 主题 |
| 数据源 (Source) | core | name, type(论坛/社交/资讯)、抓取策略、健康度 | has_many 资讯条目 |
| 主题 (Topic) | core | name, summary, item_ids, week_id | aggregates 资讯条目（7 天滚动窗口） |
| Digest | core | date, topics, content_md | references 主题 |
| Notion DB | storage | page schema | persists 资讯条目 + 主题 |
| TG Channel | output | bot_token, chat_id | delivers Digest |

## Ontology Convergence

| Round | 实体数 | 新增 | 改名 | Stable | 稳定率 |
|-------|--------|------|------|--------|--------|
| 1 | 1 (AI 资讯) | 1 | - | - | N/A |
| 2 | 3 | 2 (Digest, 消费形态) | - | 1 | 33% |
| 3 | 4 | 1 (归档库) | - | 3 | 75% |
| 4 | 4 | 0 | 1 (数据源具体化) | 3 | 75% |
| 5 | 5 | 1 (主题) | - | 4 | 80% |
| 6 | 6 | 1 (推送通道=TG) | - | 5 | 83% |
| 7 | 6 | 0 | 0 | 6 | **100%** ✅ |

连续 2 轮稳定，本体已收敛。

## 待下阶段（plan）决定的实现选择

1. **运行环境**：本地 launchd/cron / 服务器 / Cloudflare Workers / Claude Code agent + 调度？
2. **抓取技术**：requests + parser / Playwright / 现成 MCP（如 xiaohongshu、weibo MCP）/ MediaCrawler
3. **LLM 调用**：Claude API 直连 / 通过 Anthropic SDK / 其他（DeepSeek/Qwen for cost）
4. **存储**：原始条目落 SQLite 中转 → Notion，还是直接 Notion？
5. **失败恢复**：源失效后的重试/降级策略
6. **配置形式**：源列表+关键词放 yaml / 数据库 / 代码硬编码？

## Sources / 参考

- 用户访谈：本 repo 7 轮 deep-interview（见下方 transcript）
- 反爬难度参考：[MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)（XHS/微博）
- 用户行为偏好：~/CLAUDE.md（最简方案优先、用户必须 review、提交前本地验证）

## Interview Transcript

<details>
<summary>7 rounds Q&A</summary>

**Round 1** | Goal — "AI 活动资讯"是哪类？ → **D（混合：活动+新闻+工具）** | Ambiguity 78%

**Round 2** | Goal — 消费形态？ → **E 混合（待细化）** | 72%

**Round 3** | Goal — 混合的具体组合？ → **B（推送+沉淀，无雷达无独立素材）** | 64%

**Round 4** | Constraints + Contrarian — 数据源底线？ → **B（中文站灵活，XHS/微博 best-effort）+ 加 linux.do + 中文优先（深圳）** | 52%

**Round 5** | Success Criteria — 理想 digest 长啥样？ → **D（主题聚合 + 跨源去重）** | 39%

**Round 6** | Constraints + Simplifier — 频率与投递？ → **C（日报 + Notion + TG 双通道）** | 33%

**Round 7** | Success Criteria — 量化标准 + 3 个细则 → **A（紧凑型）+ 7 天去重 + 每 4h 抓取 + 空日不推** | **14.5%** ✅

</details>
