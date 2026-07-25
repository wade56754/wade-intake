# 知乎重抓与 raw cache 回退模式

## 背景

知乎回答页/问题页在同一会话中可能出现：第一次通过 API 成功，随后再次请求同一 URL 返回 403/风控。不要把这类情况直接判为“抓取失败”，也不要退回浏览器硬抓。

## 推荐流程

1. 解析 URL，得到 `question_id` / `answer_id` / `article_id`。
2. 先尝试专用路径：TikHub Zhihu API；再尝试知乎公开 API。
3. 一旦任一 API 成功，必须先保存 `raw.json`，再生成 `content.md` / `meta.json`。
4. 如果用户要求“再次抓取/重抓同一链接”，实时 API 返回 403/401 时：
   - 先查找本轮或最近一次的 `raw.json`；
   - 用已有 raw 重新导出 `content.md` / `meta.json`；
   - 在 `meta.json` 标注 `status=ok_from_previous_raw_cache` 或同义状态；
   - 明确写出 live retry 的错误和 cache 来源。
5. 不要在输出里暴露 cookie、token、API key；如 raw 中出现敏感字段，写报告前统一 `[REDACTED]`。

## 输出契约

建议目录命名：

```text
/tmp/zhihu-refetch-{question_id}-{answer_id}-{timestamp}/
├── raw.json
├── content.md
└── meta.json
```

若来自缓存，可命名：

```text
/tmp/zhihu-refetch-{question_id}-{answer_id}-from-cache-{timestamp}/
```

`meta.json` 至少包含：

```json
{
  "status": "ok_from_previous_raw_cache",
  "reason": "live API retry returned 403; reused existing raw captured earlier in this session",
  "source_raw": "/tmp/zhihu_raw.json",
  "question_id": "...",
  "answer_id": "...",
  "url": "...",
  "content_lines": 127
}
```

## 坑

- 浏览器直开知乎页常触发“当前请求存在异常，暂时限制本次访问”，不应作为主抓取路径。
- 403/401 可能是时点性风控；核心经验不是“知乎 API 不可用”，而是“raw-first + cache rehydrate”。
- 若 live retry 失败但已有 raw，最终状态应是“从已核验 raw 重建成功”，不是单纯失败。
