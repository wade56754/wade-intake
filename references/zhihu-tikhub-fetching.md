# 知乎内容抓取：TikHub 主路径

## 背景

知乎网页端容易出现风控页：`当前请求存在异常，暂时限制本次访问`。单个回答 URL 直接走浏览器、Jina Reader 或通用 web-scraper 经常只能拿到拦截页，不能据此总结。

本地已有 TikHub 知乎能力，适合作为学习助手抓取知乎内容的主路径。

## 主路径

### 单个知乎回答 URL

URL 形态：

```text
https://www.zhihu.com/question/{question_id}/answer/{answer_id}
```

处理步骤：

1. 正则提取 `question_id` 和 `answer_id`。
2. 调 TikHub `zhihu_web.fetch_question_answers(question_id=..., limit=..., order=...)`。
3. 在返回的 `data.data[*].target.id` 中按 `answer_id` 精确匹配。
4. 抽取字段：
   - `question_id` / `answer_id`
   - question title（若返回中存在）
   - `target.author.name` / `url_token` / `headline`
   - `target.content` HTML 正文
   - `target.voteup_count` / `comment_count` / `created_time` / `updated_time` 等统计字段
   - figure/img 中的图片 URL
5. HTML 转 Markdown 或纯文本，再交给学习助手评分、摘要、入库。

现场验证过的例子：

```python
z.fetch_question_answers(question_id="648199294", limit=3, order="default")
```

返回中包含回答：

```text
answer_id: {answer_id}
author: {某作者昵称}
content: 完整 HTML 正文
comment_count: 663
```

### 问题页批量回答

URL 形态：

```text
https://www.zhihu.com/question/{question_id}
```

使用同一 API：

```python
fetch_question_answers(question_id=question_id, limit=20, order="default")
```

适合抓取 Top N 回答、作者、互动数据与正文片段。

### 专栏文章

URL 形态：

```text
https://zhuanlan.zhihu.com/p/{article_id}
```

底层为：

```python
zhihu_web.fetch_column_article_detail(article_id=article_id)
```

## 本仓库落地位置

```text
scripts/fetchers/zhihu.py
scripts/router.py
scripts/main.py
```

验证命令（从仓库根目录运行）：

```bash
python3 scripts/main.py 'https://www.zhihu.com/question/{question_id}/answer/{answer_id}'
```

学习助手的知乎 URL 必须留在 TikHub 知乎 fetcher，不要回退到通用 web-scraper 作为主路径。

统一输出结构：

```json
{
  "platform": "zhihu",
  "type": "answer",
  "url": "...",
  "question_id": "...",
  "answer_id": "...",
  "title": "...",
  "author": "...",
  "content_html": "...",
  "content": "...markdown/plain text...",
  "images": [],
  "stats": {
    "voteup_count": 0,
    "comment_count": 0
  }
}
```

## 注意事项

- 不要在网页抓取失败后直接要求用户粘贴正文；先尝试 TikHub `fetch_question_answers`。
- 不要把知乎风控页当正文总结。
- 如果 TikHub CLI 因继承 SOCKS 代理报 `Using SOCKS proxy, but the 'socksio' package is not installed`，修复方式是给对应 venv 安装 `httpx[socks]`，或在 runner 中清理代理环境变量；不要把它记录成“TikHub 不可用”。
- 如果学习助手可运行但报缺 `TIKHUB_API_KEY`，先检查 `scripts/fetchers/base.py` 的凭据加载顺序是否覆盖：环境变量 → 当前工具 `.env` / `credentials/tikhub.key`。不要因此改回网页抓取，也不要在日志/回复里输出 key。
