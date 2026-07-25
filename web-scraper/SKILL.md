---
name: web-scraper
description: 统一网页内容抓取。URL进来→平台路由→最优策略提取→标准化JSON输出。
  支持微信公众号/飞书/GitHub/YouTube/得到/通用网页，三级降级（Jina→Scrapling→urllib），
  登录态页面走Chrome CDP，Cookie注入支持付费内容。
  触发：需要抓取网页内容、提取文章正文、抓取微信公众号/飞书文档时使用。
argument-hint: <url> [--json] [--max-chars N] [--cookie-file PATH] [--platform auto|wechat|feishu|general]
tags: [web, scraper, fetcher, wechat, feishu, cdp, jina, scrapling]
---

# Web Scraper — 统一网页抓取 Skill

> 这是 [learning-assistant](../SKILL.md) 依赖的通用抓取引擎，从个人 KB 工具脱敏后
> 独立发布在同一仓库的 `web-scraper/` 子目录下。硬编码的个人机器路径和已退役内部
> 系统（OpenClaw）引用已移除。

## 快速使用

```bash
cd web-scraper

# 通用网页（自动路由）
python3 scripts/fetch.py "<URL>"

# 输出标准化 JSON
python3 scripts/fetch.py "<URL>" --json

# 限制内容长度
python3 scripts/fetch.py "<URL>" --max-chars 10000

# 付费内容（Cookie 注入）
python3 scripts/fetch.py "<URL>" --cookie-file ~/cookies.txt

# 强制指定平台
python3 scripts/fetch.py "<URL>" --platform feishu
```

在 `learning-assistant` 里接入：设置 `LEARNING_ASSISTANT_WEB_SCRAPER_DIR=/path/to/web-scraper/scripts`。

## 路由策略

| URL 模式 | 适配器 | 降级链 |
|----------|--------|--------|
| `mp.weixin.qq.com` | WechatFetcher | curl → Scrapling → urllib |
| `feishu.cn` / `larksuite.com` | FeishuFetcher | Open API（需 FEISHU_APP_ID/SECRET）→ Chrome CDP → Jina → urllib |
| `github.com/{user}/{repo}` | GithubFetcher | REST API（可选 GITHUB_TOKEN）→ Jina → urllib |
| `youtube.com/watch`, `youtu.be` | YoutubeFetcher | yt-dlp → TikHub（需 TIKHUB_API_KEY）→ Jina |
| `dedao.cn/course/article` | DedaoFetcher | Chrome CDP（需登录态） |
| `--cookie-file` 指定（付费内容） | ScraplingFetcher（Playwright） | → urllib |
| 其他通用网页 | JinaFetcher | Jina → Scrapling → urllib |

## 输出格式

标准化 JSON（`--json` 模式）：

```json
{
  "platform": "wechat_mp",
  "title": "文章标题",
  "author": "作者/公众号名",
  "content": "Markdown格式正文",
  "url": "原始URL",
  "source": "jina-reader|scrapling|urllib|chrome-cdp|wechat-curl|wechat-scrapling",
  "fetched_at": "2026-03-18T00:00:00+00:00"
}
```

纯文本模式（默认）：直接输出 `# 标题\n\n> 作者\n\n正文`

## 命令行参数

```
python3 scripts/fetch.py URL [选项]

位置参数:
  URL                 目标 URL

选项:
  --json              输出标准化 JSON（默认输出纯文本）
  --max-chars N       最大字符数（默认 30000）
  --cookie-file PATH  Netscape 格式 Cookie 文件（付费内容）
  --wait-ms MS        CDP 等待页面加载时间，毫秒（默认 5000）
  --platform PLATFORM 强制指定平台：auto|wechat|feishu|general（默认 auto）
```

## 适配器说明

### 1. Jina Reader（`adapters/jina.py`）
- 调用 `r.jina.ai` API，直接返回 Markdown 格式
- 质量最高，支持大多数公开网页
- 无需额外依赖

### 2. Scrapling（`adapters/scrapling_adapter.py`）
- 基于 [Scrapling](https://github.com/D4Vinci/Scrapling) + html2text，智能 CSS 选择器提取正文
- 支持 Playwright Cookie 注入（付费内容）
- 需要单独安装：`pip install scrapling html2text` 并 `scrapling install`（浏览器依赖）

### 3. urllib（`adapters/urllib_adapter.py`）
- 纯标准库，无外部依赖
- 最终兜底，成功率最低但最稳定

### 4. Chrome CDP（`adapters/cdp.py`）
- 复用用户已登录的 Chrome，无需 Cookie 导出
- **前提**：Chrome 以 CDP 模式启动在端口 9222（`chrome --remote-debugging-port=9222 --user-data-dir=<独立目录>`）
- 支持飞书专用选择器（`.wiki-content`、`.doc-content`、`[data-page-id]`）
- 底层用 `scripts/cdp.mjs`（Node 22+，raw CDP over WebSocket，无 Puppeteer 依赖）

### 5. Wechat（`adapters/wechat.py`）
- 专用微信公众号解析（curl + 正则提取元数据）
- 提取：标题、作者、发布时间、正文

### 6. Feishu（`adapters/feishu.py`）
- 飞书/Lark 文档专用
- 优先 Open API（`FEISHU_APP_ID` / `FEISHU_APP_SECRET`），降级 CDP（登录态），再降级 Jina，最终 urllib

### 7. GitHub（`adapters/github.py`）
- GitHub REST API（可选 `GITHUB_TOKEN` 提高速率限制），降级 Jina → urllib

### 8. YouTube（`adapters/youtube.py`）
- `yt-dlp`（字幕+元数据，需本机安装），降级 TikHub API（需 `TIKHUB_API_KEY`）→ Jina

### 9. 得到课程（`adapters/dedao.py`）
- 单篇文章走 Chrome CDP + 得到内部 API（需登录态）
- 同文件里的 `DedaoCourseBatchFetcher` 类可用于批量抓整个课程（断点续传），自行传入课程名/`course_enid` 列表即可，本仓库不预置任何课程数据

## Cookie 注入（付费内容）

1. Chrome → F12 → Application → Cookies → 导出目标域名 cookie
2. 保存为 Netscape 格式文件（`cookies.txt`）
3. 运行：`python3 scripts/fetch.py "<URL>" --cookie-file ~/cookies.txt`

## Public corpus landing pattern

批量采集公开语料（GitHub 仓库/公开归档/论坛合集）到本地时的落地规范，见
`references/public-corpus-local-landing.md`：按日期建目录、生成带 hash 的
manifest、按需抽取 PDF/DOCX 文本、最后建索引笔记而不是把长文本贴进对话。

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 否 | 飞书文档主抓取路径（Open API）；未设置则降级走 CDP/Jina |
| `GITHUB_TOKEN` | 否 | 提高 GitHub API 速率限制 |
| `TIKHUB_API_KEY` | 否 | YouTube fallback |
| `WEB_SCRAPER_FAILURE_LOGGER_DIR` | 否 | 可选外部失败日志模块目录，提供 `log(url=, platform=, step=, error=)` 接口 |

## 文件结构

```
web-scraper/
├── SKILL.md
├── requirements.txt
├── scripts/
│   ├── fetch.py                  # 主入口：URL→路由→抓取→输出
│   ├── utils.py                  # 公共工具（HTML清洗、平台识别）
│   ├── cdp.mjs                   # CDP 底层 Node.js 脚本
│   └── adapters/
│       ├── __init__.py
│       ├── base.py               # 抽象基类 BaseFetcher + FetchResult
│       ├── jina.py
│       ├── scrapling_adapter.py
│       ├── urllib_adapter.py
│       ├── cdp.py
│       ├── wechat.py
│       ├── feishu.py
│       ├── github.py
│       ├── youtube.py
│       └── dedao.py
└── references/
    └── public-corpus-local-landing.md
```

## 错误退出码

- `0`：成功
- `1`：所有降级策略均失败（stderr 有错误信息）

## 已知限制

- Scrapling 适配器需要单独安装（含浏览器依赖），建议放专用 venv
- CDP 适配器需要 Chrome 以调试模式运行在 9222（或 18800）端口
- 微信公众号文章有时会触发反爬，此时 curl 失败会自动降级 Scrapling
- 飞书文档优先 Open API/CDP（保留登录态/权限），无这两者时只能走 Jina（仅限公开文档）
- 得到课程内容需要登录态（Chrome CDP），本仓库不含批量抓取的课程清单
