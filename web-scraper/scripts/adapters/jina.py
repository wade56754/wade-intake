"""
jina.py — Jina Reader 适配器
使用 r.jina.ai 将任意网页转为 Markdown，质量高，速度快
"""

import re
import urllib.request
import urllib.error

from .base import BaseFetcher, FetchResult
from utils import detect_platform, truncate_content


class JinaFetcher(BaseFetcher):
    """Jina Reader 适配器 — 主力方案，返回 Markdown 格式"""

    def fetch(self, url: str, **kwargs) -> FetchResult:
        max_chars = kwargs.get("max_chars", 30000)
        platform = detect_platform(url)

        jina_url = f"https://r.jina.ai/{url}"
        req = urllib.request.Request(jina_url)
        req.add_header("Accept", "text/markdown")
        req.add_header("User-Agent", "Mozilla/5.0")

        try:
            resp = urllib.request.urlopen(req, timeout=20)
            raw = resp.read().decode("utf-8", errors="ignore").strip()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Jina Reader HTTP {e.code}: {e.reason}")
        except Exception as e:
            raise RuntimeError(f"Jina Reader 请求失败: {e}")

        if not raw or len(raw) < 100:
            raise RuntimeError(f"Jina Reader 内容过短 ({len(raw)} chars)")

        # 提取 title
        title = ""
        m = re.search(r"^Title:\s*(.+)$", raw, re.M)
        if m:
            title = m.group(1).strip()

        # 提取 author（Jina 有时会在 meta 行里）
        author = ""
        m = re.search(r"^Author:\s*(.+)$", raw, re.M)
        if m:
            author = m.group(1).strip()

        # 去掉 Jina 头部元数据行
        content = re.sub(
            r"^(Title:.*|URL Source:.*|Published Time:.*|Author:.*|Markdown Content:)\s*\n",
            "",
            raw,
            flags=re.M,
        ).strip()

        content = truncate_content(content, max_chars)

        return FetchResult(
            platform=platform,
            title=title,
            author=author,
            content=content,
            url=url,
            source="jina-reader",
        )
