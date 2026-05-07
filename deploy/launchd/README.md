# launchd 自动化（macOS）

让 fetch / radar / digest 自动跑，不用每天手动。

## 安装

```bash
cd deploy/launchd
./install.sh
```

会装 4 个 LaunchAgent 到 `~/Library/LaunchAgents/`：

| Job | 频率 | 做什么 |
|-----|------|--------|
| `com.txie.ai-digest.fetch` | 每 30 分钟 | 抓所有 source（linux.do / xhs / 公众号 / newsnow） |
| `com.txie.ai-digest.radar` | 每小时 | 分类 + 整点推事件雷达（23-7 静默窗内只分类不推） |
| `com.txie.ai-digest.digest` | 每天 12:00 | 推资讯日报（聚类 + 飞书 + Notion 归档） |
| `com.txie.ai-digest.calendar` | 每小时 | 把新分类的 event 推到飞书日历（idempotent，重跑不重复） |

`install.sh` 是幂等的，重跑等于"重新加载配置"。

> **calendar job 前置条件**：`.env` 需要 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_CALENDAR_ID`，应用需勾选 `calendar:calendar` 和 `calendar:calendar.event:create` 权限。第一次配置参考 `scripts/feishu_list_calendars.py --ensure-primary` 拿到 `calendar_id`。

## 查看状态

```bash
launchctl list | grep ai-digest          # 是否已加载
tail -F data/logs/{fetch,radar,digest,calendar}.{out,err}.log   # 实时日志
```

每次任务的 stdout/stderr 都写到 `data/logs/<name>.{out,err}.log`（已 gitignore）。

## 手动触发一次（不等到下个调度点）

```bash
launchctl kickstart -k gui/$(id -u)/com.txie.ai-digest.fetch
launchctl kickstart -k gui/$(id -u)/com.txie.ai-digest.radar
launchctl kickstart -k gui/$(id -u)/com.txie.ai-digest.digest
launchctl kickstart -k gui/$(id -u)/com.txie.ai-digest.calendar
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
