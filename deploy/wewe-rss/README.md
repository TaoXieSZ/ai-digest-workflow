# 部署 we-mp-rss / wewe-rss（微信公众号 → RSS）

> AI 资讯密度最高的源是公众号（机器之心 / 量子位 / 各 meetup 主办方）。
> 这一步把它们桥接成 RSS，由 ai-digest 主 pipeline 消费。

## 选哪个？

| 项目 | 状态 | 推荐场景 |
|------|------|----------|
| [cooderl/wewe-rss](https://github.com/cooderl/wewe-rss) | **2026-01-19 archived**（仍可跑，不再维护） | 想要最简部署、不在乎更新 |
| [rachelos/we-mp-rss](https://github.com/rachelos/we-mp-rss) | 活跃 | 推荐 |
| [wang-h/werss](https://github.com/wang-h/werss) | 活跃 + LLM 摘要 | 想要 AI 加工层 |

**默认推荐 we-mp-rss**——活跃 + 功能更全。这个目录的 docker-compose.yml 是 wewe-rss 模板（相同接口），切到 we-mp-rss 改 image 即可。

## 5 分钟部署（wewe-rss / SQLite 模式）

### 1. 准备 .env

```bash
cd deploy/wewe-rss
cat > .env <<EOF
WEWE_PORT=4000
WEWE_AUTH_CODE=$(openssl rand -hex 32)
EOF
```

`AUTH_CODE` 用于保护 RSS feed URL，避免别人随便订阅。

### 2. 启动

```bash
docker compose up -d
```

容器跑起来后访问 `http://localhost:4000`，按 UI 提示用**微信读书账号**扫码登录（第一次需要）。这是 wewe-rss 的工作原理：通过微信读书的订阅 API 拉取公众号文章。

### 3. 添加订阅的公众号

UI 里搜索公众号名称添加。建议起步订阅：

**AI 行业新闻类**
- 机器之心
- 量子位
- 智东西
- 新智元

**深圳/华南本地活动**
- 深圳人工智能行业协会
- AICon
- 各个具体 meetup / hackathon 主办方公众号

**工具/玩法类**
- AI 路由器
- AIGC 研究室
- 你信任的个人公众号

### 4. 拿到 RSS URL

每个订阅会有一个个性化的 RSS URL，格式类似：
```
http://localhost:4000/feeds/<feed_id>.atom?auth_code=<your_auth_code>
```

UI 上每个 feed 旁有"复制 RSS"按钮。

### 5. 把 URL 加到 ai-digest 的 sources.yaml

回到主项目根目录，编辑 `config/sources.yaml`，每个公众号一个条目：

```yaml
sources:
  # ... 原有的 linux_do ...

  - id: jiqizhixin
    display_name: 机器之心
    fetcher_type: rss
    config:
      url: http://localhost:4000/feeds/<feed_id>.atom?auth_code=<token>
      max_items: 30
    enabled: true

  - id: qbitai
    display_name: 量子位
    fetcher_type: rss
    config:
      url: http://localhost:4000/feeds/<feed_id>.atom?auth_code=<token>
      max_items: 30
    enabled: true
```

PR1 已经实现的通用 `RSSFetcher` 直接消费——无需写新代码。

### 6. 验证

```bash
# 主项目根
source .venv/bin/activate
python scripts/run_fetch.py
sqlite3 data/items.db "SELECT source_id, count(*) FROM items GROUP BY source_id"
```

应该能看到每个公众号的条目数。

## 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| Docker 启动失败 `AUTH_CODE` undefined | 没建 .env | 重做步骤 1 |
| 微信读书登录二维码刷不出 | 容器内出口被墙 | 配置容器代理或宿主走透明代理 |
| RSS 拉到的文章数 = 0 | 微信读书账号没有该公众号订阅 | 在微信读书 app 里先订阅一遍 |
| 订阅添加成功但 feed 一直空 | wewe-rss 后台拉取有延迟 | 等 5-10 分钟，或用 `docker compose logs` 看 |

## 资源消耗

SQLite 模式：~100 MB 内存，磁盘按订阅数线性增长（10 个公众号 1 个月约 50 MB）。

## 替代方案

如果 docker 部署太重，[Wechat2RSS](https://github.com/ttttmr/Wechat2RSS) 是更轻量的命令行版（不需要数据库），但订阅管理体验差一些。
