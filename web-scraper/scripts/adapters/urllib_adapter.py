"""
urllib_adapter.py — urllib 直接抓取（最终兜底方案）
无外部依赖，纯标准库
"""

import re
import urllib.request
import urllib.error

from .base import BaseFetcher, FetchResult
from utils import detect_platform, truncate_content


class UrllibFetcher(BaseFetcher):
    """urllib 直接抓取适配器 — 兜底，无需任何外部依赖"""

    def fetch(self, url: str, **kwargs) -> FetchResult:
        max_chars = kwargs.get("max_chars", 30000)
        platform = detect_platform(url)

        req = urllib.request.Request(url)
        req.add_header(
            "User-Agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        req.add_header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        req.add_header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")

        try:
            resp = urllib.request.urlopen(req, timeout=15)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"urllib HTTP {e.code}: {e.reason}")
        except Exception as e:
            raise RuntimeError(f"urllib 请求失败: {e}")

        # 检测编码
        charset = "utf-8"
        content_type = resp.headers.get("Content-Type", "")
        m = re.search(r"charset=([^\s;]+)", content_type, re.I)
        if m:
            charset = m.group(1)

        raw_bytes = resp.read()
        try:
            html = raw_bytes.decode(charset, errors="ignore")
        except (UnicodeDecodeError, LookupError):
            html = raw_bytes.decode("utf-8", errors="ignore")

        title = _extract_title(html)
        author = _extract_author(html)
        content = _extract_content(html)

        if not content or len(content) < 50:
            raise RuntimeError(f"urllib 提取内容过短 ({len(content)} chars)")

        content = truncate_content(content, max_chars)

        return FetchResult(
            platform=platform,
            title=title,
            author=author,
            content=content,
            url=url,
            source="urllib",
        )


def _clean_html(html: str) -> str:
    """清洗 HTML，提取纯文本"""
    if not html:
        return ""
    for tag in ["script", "style", "nav", "footer", "header", "aside"]:
        html = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", html, flags=re.S | re.I)
    html = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</tr>|</h[1-6]>", "\n", html, flags=re.I)
    html = re.sub(r"<[^>]+>", "", html)
    html = html.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def _extract_title(html: str) -> str:
    m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if m:
        return _clean_html(m.group(1)).strip()
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    if m:
        return _clean_html(m.group(1)).strip()
    return ""


def _extract_author(html: str) -> str:
    m = re.search(r'<meta[^>]+property=["\']og:article:author["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r'<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if m:
        return m.group(1).strip()
    return ""


def _extract_content(html: str) -> str:
    m = re.search(r"<article[^>]*>(.*?)</article>", html, re.S | re.I)
    if m:
        return _clean_html(m.group(1))
    m = re.search(r"<main[^>]*>(.*?)</main>", html, re.S | re.I)
    if m:
        return _clean_html(m.group(1))
    m = re.search(
        r'<div[^>]*(?:id|class)=["\'][^"\']*content[^"\']*["\'][^>]*>(.*?)</div>',
        html, re.S | re.I
    )
    if m:
        text = _clean_html(m.group(1))
        if len(text) > 200:
            return text
    m = re.search(r"<body[^>]*>(.*?)</body>", html, re.S | re.I)
    if m:
        return _clean_html(m.group(1))
    return _clean_html(html)
