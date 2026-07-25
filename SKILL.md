---
name: learning-assistant
description: >
  个人学习助手 & 知识大脑。当用户发送任意平台链接时自动激活：
  微信公众号/小红书/抖音/TikTok/YouTube/X(Twitter)/飞书/普通网页/GitHub/知乎。
  自动识别平台，调用最优抓取工具，提炼核心知识，评分决策入库。
  **入库决策由助手自行评分判断，无需逐条询问用户。≥16分入库，10-15分存摘要，<10分弃用。**
---

# 学习助手

> 从个人 KB 工具脱敏而来的公开版本。核心抓取/评分/入库逻辑与原版一致；
> 涉及个人机器路径、私有反代域名、已退役内部系统（OpenClaw）的部分已移除，
> 改为可选的环境变量配置，见下方「环境变量」。

## 用法

当用户发送一个 URL 时：

```bash
cd <本仓库根目录>
python3 scripts/main.py "{URL}" 2>&1
```

**注意：**
- X/Twitter 视频默认只抓推文文本和媒体元数据；需要转写视频正文时显式运行：
  `python3 scripts/main.py "{URL}" --transcribe-x-video`
- 分析层优先读取 `LEARNING_ASSISTANT_LLM_*` 环境变量；未设置时会尝试连接
  `http://127.0.0.1:8100/v1/chat/completions`（本机 OpenAI-compatible 反代的常见默认端口，
  你需要自行提供一个这样的后端，或直接设置 `LEARNING_ASSISTANT_LLM_API_URL`）。
  连不上时自动回退到 `scripts/scorer.py` 的关键词启发式评分，不会报错中断。
  不输出、不保存、不提交任何 key。

---

## 支持平台 & 抓取工具

| 平台 | 链接特征 | 抓取方式 | 状态 |
|------|---------|---------|------|
| 微信公众号 | `mp.weixin.qq.com/s/` | TikHub / urllib，兜底走内置 web-scraper | ✅ |
| 小红书 | `xiaohongshu.com` / `xhslink.com` | TikHub xiaohongshu API | ✅ |
| 抖音 | `douyin.com` / `v.douyin.com` | **douyin-mcp（优先，需 mcporter）**，回退可选抖音 CLI / TikHub | ✅ |
| TikTok | `tiktok.com` | TikHub tiktok API | ✅ |
| YouTube | `youtube.com` / `youtu.be` | 可选 baoyu-youtube-transcript（InnerTube，无需 Key）→ YouMind → TikHub | ✅ |
| X/Twitter 帖子 | `x.com/{user}/status/{id}` | Nitter/FxTwitter → **TikHub**，视频可显式转写 | ✅ |
| X/Twitter 主页 | `x.com/{user}` | **TikHub**（fetch_user_post_tweet） | ✅ |
| GitHub 仓库 | `github.com/{owner}/{repo}` | **gh CLI** | ✅ |
| 知乎回答/问题/专栏 | `zhihu.com/question/...` / `zhuanlan.zhihu.com/p/...` | **TikHub Zhihu API 主路径**：回答/问题走 `fetch_question_answers`，专栏走 `fetch_column_article_detail`；不要优先走通用 web-scraper | ✅ |
| 飞书文档 | `feishu.cn` / `lark.com` | **lark-cli**（wiki 节点 `get_node` 解析 + `docs +fetch` 正文，均 bot 身份） | ✅ |
| FlowUs 付费页 | `flowus.cn` | 内置 web-scraper（Cookie 注入） | ✅ |
| 通用网页 | 其他 | 内置 web-scraper（三级降级：Jina→Scrapling→urllib） | ✅ |
| 得到课程 | `dedao.cn/course/detail` | **浏览器可见 DOM 优先**：打开课程页 → 点击「课程内容」→ 抽取课程元数据与目录；web-scraper 仅作初筛 | ⚠️ |

---

## 工具调用方式

### 1. 抖音视频 — douyin-mcp（可选，需 mcporter + 对应 MCP server）
```bash
mcporter call 'douyin.parse_douyin_video_info(share_link: "https://v.douyin.com/xxx/")'
mcporter call 'douyin.extract_douyin_text(share_link: "https://v.douyin.com/xxx/")'  # 需 DASHSCOPE_API_KEY
```

### 2. Twitter/X — TikHub API（无需 Cookie）
```bash
# 通过 fetchers/twitter.py 调用，内部用 TikHub：
# fetch_tweet_detail → 单条帖文
# fetch_user_post_tweet → 用户主页帖子列表
# fetch_user_profile → 用户信息
```

### 3. 飞书文档 — lark-cli（fetchers/feishu.py 封装）
```bash
# main.py 自动路由，内部两步（均 bot 身份；user 身份会报 need_user_authorization）：
# 1) wiki 链接先解析节点拿 obj_token / title
lark-cli wiki spaces get_node --params '{"token":"{node_token}"}' --as bot
# 2) 正文抓取（docx 直链跳过第 1 步直接用 URL 里的 token）
lark-cli docs +fetch --doc {obj_token} --as bot
```

### 4. YouTube 字幕（可选外部工具，本仓库不含实现）
```bash
# fetcher 内部顺序：
# 1. baoyu-youtube-transcript (InnerTube，无 Key，支持章节/多语言/缓存) —
#    设置 BAOYU_YOUTUBE_TRANSCRIPT_DIR 指向其安装目录
# 2. YouMind API（YOUMIND_API_KEY 存在时）
# 3. TikHub API（兜底）
```

### 5. GitHub — gh CLI
```bash
gh repo view owner/repo
gh search repos "query" --sort stars --limit 10
```

### 6. 通用网页 — 内置 web-scraper
```bash
# 本仓库自带 web-scraper/（见 ../web-scraper/SKILL.md），main.py 默认自动加载它，
# 用于通用网页/微信公众号/飞书/GitHub/YouTube/得到课程的兜底抓取（Jina→Scrapling→urllib
# 三级降级 + Chrome CDP 登录态支持）。要换成自己的实现，设置 LEARNING_ASSISTANT_WEB_SCRAPER_DIR
# 指向一个实现了 `fetch.route(url, platform=None) -> Result(content, ...)` 接口的目录。
```

### 6.1 知乎 — TikHub 专用路径

详细参考：`references/zhihu-tikhub-fetching.md`。重抓/cache 回退参考：`references/zhihu-refetch-cache-pattern.md`。

知乎回答页不要优先走浏览器/Jina/通用 web-scraper；网页端常触发风控页「当前请求存在异常，暂时限制本次访问」。处理 `zhihu.com/question/{question_id}/answer/{answer_id}` 时，先提取 `question_id` / `answer_id`，调用 TikHub `zhihu_web.fetch_question_answers(question_id=...)`，再在返回列表中按 `answer_id` 精确匹配目标回答，抽取 HTML 正文后转 Markdown/纯文本。抓取成功后必须 raw-first：先落 `raw.json`，再重建 `content.md` / `meta.json`；如果用户要求再次抓取而 live API 返回 403/401，优先用已保存 raw 重新导出，并在 `meta.json` 标注 `ok_from_previous_raw_cache`、live retry 错误和 cache 来源。

若报缺 `TIKHUB_API_KEY`，不要退回网页抓取，先对齐凭据读取路径：环境变量 → 当前工具 `.env` / `credentials/tikhub.key`。

### 6.2 得到课程页 — 浏览器 DOM 兜底

详细参考：`references/dedao-course-dom-fallback.md`。需要登录/已购正文时，另见：`references/dedao-login-and-purchase-capture.md`（含短信验证码/扫码/无密码入口、CDP 抓取、`detail_id` 参数、分页与逐讲正文落库规则）。

得到课程详情页（`dedao.cn/course/detail?id=...`）直接用 web-scraper / Jina 抓取时，容易只抓到登录页、导航栏、学习统计占位（如 `NaN 今日学习`、`账户充值`），导致摘要污染。处理课程页时：

1. 先运行学习助手主脚本生成初稿，但不要直接信任自动摘要。
2. 用浏览器打开课程详情页，点击「课程内容」。
3. 以浏览器可见 DOM / `document.body.innerText` 抽取：课程名、副标题、价格、试读数、已更新/总讲数、学习人数、购买须知、已展开目录。
4. 若页面懒加载，只抓到前 30 讲，先滚动到底部再复抓；课程页通常会继续追加后续讲次。
5. 同步探测接口：`/pc/bauhinia/pc/class/info` 可返回 `chapter_list` 与基础文章元数据；`/pc/bauhinia/pc/class/purchase/info` / `purchase/article_list` 只在登录且已购授权成立时才可能返回购买态正文/列表。
6. 必须记录登录/购买态证据：页面是否仍显示"登录/注册""购买"，以及 `class_info.is_subscribe`。若 `is_subscribe=0` 或页面显示购买按钮，只能交付"公开目录+待补正文"，不能声称已抓到已购正文。
7. 如果页面只展开部分目录，明确标记"公开可见/当前展开到 N 讲"，不要虚构完整目录。
8. 最终笔记应重写为干净课程总览；把自动草稿中的登录噪音列为"抓取失败信号"，不要作为正文入库。

---

## 评分体系（入库决策）

每条内容抓取后，按以下 5 个维度打分，自动决策入库：

| 维度 | 权重 | 1分 | 3分 | 5分 |
|------|------|-----|-----|-----|
| **信息密度** | 30% | 全是观点废话 | 有数据但步骤模糊 | 有具体数据+可执行步骤 |
| **业务相关度** | 30% | 与业务完全无关 | 有一定参考价值 | 直接命中你的业务领域（见环境变量自定义） |
| **稀缺性** | 20% | 烂大街内容 | 有一定独特视角 | 独家一手案例/数据 |
| **可复用性** | 15% | 一次性资讯 | 可偶尔参考 | 框架/模板可反复引用 |
| **时效性** | 5% | 超1年 | 3-12个月 | 3个月内 |

```
总分 = 信息密度×0.30 + 业务相关×0.30 + 稀缺性×0.20 + 可复用×0.15 + 时效×0.05
满分 = 5×(0.30+0.30+0.20+0.15+0.05) = 5分

换算为百分制：总分 × 4 = 最终分（满分20分）
```

**决策规则：**
- **≥ 16分** → 🔥 高价值入库（全文 + 分析）
- **10-15分** → 📖 存摘要（核心观点 + 来源链接，不存全文）
- **< 10分** → 🗑️ 弃用（仅记录标题+原因，不入库）

---

## 输出格式

```
📌 [标题] — [平台] [@作者]

━━ 内容概要 ━━
{2-3句，直接说价值}

━━ 核心知识点 ━━
• 要点1（带数据/案例）
• 要点2
• 要点3

━━ 业务借鉴分析 ━━
【业务透镜1】默认跨境电商/TikTok Shop，可用 LEARNING_ASSISTANT_BIZ_LENS_1_LABEL 自定义（无关时省略）
【业务透镜2】默认 AI Agent 架构，可用 LEARNING_ASSISTANT_BIZ_LENS_2_LABEL 自定义（无关时省略）

━━ 评分 ━━
信息密度 X/5 · 业务相关 X/5 · 稀缺性 X/5 · 可复用 X/5 · 时效 X/5
→ 总分 XX/20

━━ 入库决策 ━━
🔥 高价值入库 / 📖 存摘要 / 🗑️ 弃用 — {一句理由}
去向：📚知识库 / 🔫素材库 / 📚+🔫两者都

━━ 素材提取（仅当去向含🔫时）━━
• [洞察] "具体内容" — 追加到 arsenal/insights.md
• [数据] "具体数据" — 追加到 arsenal/data_points.md
• [故事] 故事标题/梗概 — 追加到 arsenal/stories.md
• [金句] "原文金句" — 追加到 arsenal/insights.md（金句类）
• [案例] 某人/某公司做了什么，结果如何 — 追加到 arsenal/data_points.md（案例类）
• [反常识] "违反直觉的发现" — 追加到 arsenal/insights.md（反常识类）
```

---

## 入库路径

- 全文：`knowledge/articles/{platform}/{YYYY-MM-DD}-{作者}-{主题}.md`
- 摘要：`knowledge/articles/{platform}/summaries/{YYYY-MM-DD}-{作者}-{主题}.md`

这两个目录以及下面的 `arsenal/`、`credentials/`、`logs/`、`memory/` 都是**运行时产物**，不在本仓库中，
首次运行时脚本会自行创建。

---

## 素材库分流（入库决策后必须执行）

### 分流判断标准

评分 ≥10 的内容，**必须判断是否提取素材**：

| 内容特征 | 去向 |
|----------|------|
| 完整方法论/框架/教程，碎片引用价值低 | 📚 仅知识库 |
| 有可直接引用的金句/数据/故事/案例 | 🔫 仅素材库（≥16 同时入知识库） |
| 既有完整体系又有碎片金句 | 📚+🔫 两者都 |
| 用户个人经历/感悟 | 🔫 素材库（个人素材） |

**简化规则：≥16分默认"两者都"，10-15分默认"仅素材库"（如有可提取弹药）。**

### 六种弹药类型

| 类型 | 说明 | 追加到 |
|------|------|--------|
| **洞察** | 独特认知/观点/判断 | `arsenal/insights.md` |
| **数据** | 具体数字/比例/实验结果 | `arsenal/data_points.md` |
| **故事** | 有人物+冲突+结局的叙事 | `arsenal/stories.md` |
| **金句** | 原文可直接引用的精炼表达 | `arsenal/insights.md`（金句分区） |
| **案例** | 某人/某公司做了什么+结果 | `arsenal/data_points.md`（案例分区） |
| **反常识** | 违反直觉但有依据的发现 | `arsenal/insights.md`（反常识分区） |

### 素材格式规范

每条素材必须包含：
```
- {内容}（出处：{作者}《{标题}》{日期}）
```

示例：
```
- TikTok Shop 2025年美区GMV突破200亿美元，同比增长240%（出处：@电商报《TikTok美区年报》2025-12）
```

### 素材写入规则

1. **追加不覆盖** — 在对应文件末尾追加，不修改已有内容
2. **去重检查** — 追加前搜索文件，相同数据/观点不重复录入
3. **出处必标** — 每条素材标注来源（作者+标题+日期），无出处的不入
4. **质量门槛** — 只提取"拿出来就能用"的弹药，模糊/泛泛而谈的不提取
5. **数量克制** — 每篇内容最多提取 8 条弹药，宁缺毋滥

### 素材库路径

```
<本仓库根目录>/arsenal/
├── insights.md      ← 洞察 + 金句 + 反常识
├── data_points.md   ← 数据 + 案例
└── stories.md       ← 故事
```

---

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `TIKHUB_API_KEY` | 是（大多数平台的主抓取路径） | 优先环境变量；也可放 `.env` / `credentials/tikhub.key`（两者均已加入 `.gitignore`） |
| `LEARNING_ASSISTANT_LLM_API_URL` | 否 | 自定义 OpenAI-compatible LLM 后端；不设置时默认 `http://127.0.0.1:8100/v1/chat/completions` |
| `LEARNING_ASSISTANT_LLM_API_KEY` | 否 | 对应后端的 key；未设置且默认地址连不上时自动回退到关键词启发式评分 |
| `LEARNING_ASSISTANT_LLM_MODEL` | 否 | 默认 `gpt-5.4-mini`（按你的后端支持的模型名调整） |
| `LEARNING_ASSISTANT_LLM_API_MODE` | 否 | `openai_chat`（默认）或 `anthropic_messages` |
| `LEARNING_ASSISTANT_LLM_USER_AGENT` | 否 | 需要给 LLM 请求附加自定义 User-Agent 时设置 |
| `LEARNING_ASSISTANT_BUSINESS_CONTEXT` | 否 | 系统提示里的"你的业务背景"，替换成自己的领域描述 |
| `LEARNING_ASSISTANT_BIZ_LENS_1_LABEL` / `_2_LABEL` | 否 | 「业务借鉴分析」两个透镜的标签，默认跨境电商/AI Agent 架构（原作者的示例） |
| `YOUMIND_API_KEY` | 否 | YouTube 字幕 fallback |
| `DASHSCOPE_API_KEY` | 否 | 抖音语音转文字（经 douyin-mcp） |
| `XTF_DIR` | 否 | 可选 x-tweet-fetcher companion 工具目录（评论区/时间线降级用） |
| `BAOYU_YOUTUBE_TRANSCRIPT_DIR` | 否 | 可选 baoyu-youtube-transcript companion 工具目录 |
| `DOUYIN_CLI_PATH` | 否 | 可选独立抖音 CLI 抓取器路径 |
| `LEARNING_ASSISTANT_WEB_SCRAPER_DIR` | 否 | 通用网页抓取器目录；默认自动指向本仓库内的 `web-scraper/scripts`，设置后可换成自己的实现（需实现 `fetch.route(url, platform=None)` 接口） |
| `LEARNING_ASSISTANT_WIKI_LOG_PATH` | 否 | 可选：把 ingest 事件回填到一个外部知识库的 log.md |
| `NITTER_URL` | 否 | 本地 Nitter 实例地址，默认 `http://127.0.0.1:8788` |
| `WADE_LEARNING_X_VIDEO_TRANSCRIBE` | 否 | 设为 `1` 时对 X 视频做本地 Whisper 转写（需要 ffmpeg + mlx_whisper/whisper） |

mcporter（可选）：如果要用 douyin-mcp，需自行配置好对应 MCP server。

## 分析链路排障

当抓取成功但分析阶段出现「回退 heuristic」或 `Expecting value: line 1 column 1 (char 0)` 时，不要先怀疑正文抓取；先把 fetch、LLM transport、provider response shape、JSON extraction 四层拆开验证。尤其是知乎链接：主抓取路径应走 TikHub，分析失败通常是 LLM 后端/响应格式问题。

参考：`references/zhihu-learning-assistant-llm-json.md`
