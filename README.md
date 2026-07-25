# learning-assistant

一个个人学习助手：喂给它任意平台的链接（微信公众号 / 小红书 / 抖音 / TikTok /
YouTube / X(Twitter) / 飞书 / 知乎 / 普通网页 / GitHub），它会自动识别平台、
调用合适的抓取工具、用 LLM（或关键词启发式 fallback）分析内容、按 5 维度打分，
并按分数自动决定是否入库、存全文还是只存摘要。

最初是作为 [Claude Code](https://claude.com/claude-code) 的一个 Skill 开发的
（见 `SKILL.md`），但 `scripts/main.py` 本身是独立可运行的 Python CLI，脱离
Claude Code 也能直接用。

这是从个人知识库工具脱敏后的公开版本：移除了硬编码的个人机器路径、私有反代
域名、以及一个已退役内部系统（OpenClaw）的死引用，改成了下面这份环境变量表。
核心的抓取路由 / 评分体系 / 入库与去重逻辑与原版一致。

## 快速开始

```bash
git clone <this-repo>
cd learning-assistant
export TIKHUB_API_KEY=your_key_here   # 大多数平台的主抓取路径都依赖它
python3 scripts/main.py "https://x.com/someuser/status/123456789"
```

首次运行会在仓库根目录自动创建 `knowledge/`、`arsenal/`、`logs/` 等运行时目录
（均已加入 `.gitignore`，不会被提交）。

## 依赖

必需：
- Python 3.9+（标准库为主，无第三方包依赖）
- [TikHub](https://tikhub.io/) API Key — 大多数平台（小红书/抖音/TikTok/X/YouTube/知乎）的主抓取路径

可选（缺失时相关平台会走 fallback 或跳过，不影响其他平台）：
- `gh` CLI — GitHub 仓库抓取
- `lark-cli` — 飞书文档抓取
- `mcporter` + douyin MCP server — 抖音字幕转写优先路径
- Nitter 本地实例 — X/Twitter 免费抓取路径（未部署时自动走 FxTwitter → TikHub）
- 一个 OpenAI-compatible LLM 后端 — 结构化内容分析（未配置时自动回退到
  `scripts/scorer.py` 的关键词启发式评分）
- ffmpeg + Whisper（`mlx_whisper` 或 `whisper`）— X 视频本地转写
- 一个独立的 YouTube 字幕抓取工具、X 评论区/时间线抓取工具 —
  见下方 `BAOYU_YOUTUBE_TRANSCRIPT_DIR` / `XTF_DIR`

通用网页/微信公众号/飞书/GitHub/YouTube/得到课程的兜底抓取引擎（`web-scraper/`）
**已随本仓库一起提供**，`scripts/main.py` 默认会自动找到它，无需额外配置；它自己
的可选依赖（`FEISHU_APP_ID`、Scrapling、Chrome CDP 等）见 `web-scraper/SKILL.md`。

完整环境变量表见 `SKILL.md` § 环境变量。

## 目录结构

```
scripts/           学习助手主体：抓取器 + 路由 + 分析 + 评分 + 存储
web-scraper/        通用网页抓取引擎（微信/飞书/GitHub/YouTube/得到/通用网页），
                    独立可用，也是 scripts/main.py 的默认兜底抓取层
references/         排障笔记：TikHub 知乎路径、得到课程页 DOM 兜底、LLM JSON 解析踩坑
SKILL.md            Claude Code Skill 定义（平台矩阵、评分体系、输出格式）
```

运行后会额外生成（均不提交）：

```
knowledge/articles/{platform}/     入库全文
arsenal/{insights,data_points,stories}.md   可复用素材（金句/数据/故事/案例）
logs/failures.jsonl                失败事件日志
credentials/tikhub.key             可选：把 API Key 放这里而不是环境变量
```

## 自定义

`analyzer.py` 里的业务打分维度默认是原作者的示例（跨境电商 + AI Agent 架构），
用 `LEARNING_ASSISTANT_BUSINESS_CONTEXT` / `LEARNING_ASSISTANT_BIZ_LENS_1_LABEL` /
`LEARNING_ASSISTANT_BIZ_LENS_2_LABEL` 换成你自己的业务领域即可，不用改代码。

## License

MIT，见 `LICENSE`。
