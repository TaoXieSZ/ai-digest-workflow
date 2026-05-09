# launchd 自动化（macOS）

让 fetch / radar / digest 自动跑，不用每天手动。

## 安装

```bash
cd deploy/launchd
./install.sh
```

会装 5 个 LaunchAgent 到 `~/Library/LaunchAgents/`：

| Job | 频率 | 做什么 |
|-----|------|--------|
| `com.txie.ai-digest.fetch` | 每 30 分钟 | 抓所有 source（linux.do / xhs / 公众号 / newsnow） |
| `com.txie.ai-digest.radar` | 每小时 | 分类 + 整点推事件雷达（23-7 静默窗内只分类不推） |
| `com.txie.ai-digest.digest` | 每天 12:00 | 推资讯日报（聚类 + 飞书 + Notion 归档） |
| `com.txie.ai-digest.calendar` | 每小时 | 把新分类的 event 推到飞书日历（idempotent，重跑不重复） |
| `com.txie.ai-digest.wiki_ingest` | 每天 12:30 | 把当天 `daily-{date}.md` 通过 `claude -p` 调 OMC `wiki_ingest` 工具写入 `.omc/wiki/`（idempotent） |

`install.sh` 是幂等的，重跑等于"重新加载配置"。

> **calendar job 前置条件**：`.env` 需要 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_CALENDAR_ID`，应用需勾选 `calendar:calendar` 和 `calendar:calendar.event:create` 权限。第一次配置参考 `scripts/feishu_list_calendars.py --ensure-primary` 拿到 `calendar_id`。

> **wiki_ingest job 前置条件**：
> - `claude` CLI 在 PATH（默认搜 `~/.local/bin` / `/opt/homebrew/bin` / `/usr/local/bin` / `~/.npm-global/bin`）
> - oh-my-claudecode 插件已全局安装（`wiki_ingest` MCP tool 通过它注入）
> - 已通过 `claude` 完成首次登录 / API key 配置
> - 当天 `data/digests/daily-{today}.md` 已生成（digest job 跑过）；找不到就静默 skip
> - 单次成本 ~$0.005-$0.02 (Sonnet)，年成本 ~$5
> - `~/.claude/settings.json` 加这条让 cron 不卡审批：
>   ```json
>   "permissions": {
>     "allow": ["mcp__plugin_oh-my-claudecode_t__wiki_ingest"]
>   }
>   ```
>   runner 还会传 `--allowedTools 'Read,mcp__plugin_oh-my-claudecode_t__wiki_ingest'` 把 sub-session 工具集限到这两个

## 查看状态

```bash
launchctl list | grep ai-digest          # 是否已加载
tail -F data/logs/{fetch,radar,digest,calendar,wiki_ingest}.{out,err}.log   # 实时日志
```

每次任务的 stdout/stderr 都写到 `data/logs/<name>.{out,err}.log`（已 gitignore）。

## 手动触发一次（不等到下个调度点）

```bash
launchctl kickstart -k gui/$(id -u)/com.txie.ai-digest.fetch
launchctl kickstart -k gui/$(id -u)/com.txie.ai-digest.radar
launchctl kickstart -k gui/$(id -u)/com.txie.ai-digest.digest
launchctl kickstart -k gui/$(id -u)/com.txie.ai-digest.calendar
launchctl kickstart -k gui/$(id -u)/com.txie.ai-digest.wiki_ingest
```

也可以直接跑 runner 脚本（绕过 launchd）测试 wiki_ingest：

```bash
./deploy/launchd/wiki_ingest.sh                # 用今天日期
./deploy/launchd/wiki_ingest.sh 2026-05-08    # 指定日期
```

## 卸载

```bash
./uninstall.sh
```

## 调试

| 现象 | 排查 |
|------|------|
| `launchctl bootstrap` 报 "service already loaded" | install.sh 已自动 bootout，正常重跑即可 |
| 任务跑但什么也没做 | 看 `*.err.log`：通常是 `.env` 没找到（DEEPSEEK_API_KEY 缺）或 venv 路径错 |
| `Operation not permitted` | 系统设置 → 隐私与安全性 → 完全磁盘访问 → 加 `/usr/bin/launchctl` |
| 时间错位 | macOS 用本地时区跑 `StartCalendarInterval`；CST 下 12:00 没问题 |

## 设计决策

- **launchd, not cron**: macOS 14+ cron 已废弃；launchd 是官方推荐
- **`run.sh` 包装层**: launchd 不继承 shell env，统一在脚本里 source `.env` + `deploy/wewe-rss/.env`，secrets 不进 plist
- **不开机自启 (`RunAtLoad=false`)**: 避免笔记本一开机就跑（笔记本可能没网），等下个调度点
- **`ProcessType=Background`**: 让 macOS 知道这些是后台任务，可以让出 CPU/IO 给前台 app
