"""
scrapling_adapter.py — Scrapling 适配器
高质量动态渲染，支持 Cookie 注入抓取付费内容
"""

import re
import sys

from .base import BaseFetcher, FetchResult
from utils import fix_lazy_images, clean_markdown, detect_platform, truncate_content


class ScraplingFetcher(BaseFetcher):
    """
    Scrapling 适配器
    - 普通页面用 Fetcher（快）
    - 反爬/SPA 用 StealthyFetcher（中）
    - 付费内容用 Playwright Cookie 注入
    """

    def fetch(self, url: str, **kwargs) -> FetchResult:
        max_chars = kwargs.get("max_chars", 30000)
        cookie_file = kwargs.get("cookie_file")  # Netscape cookie 文件路径
        cookies = kwargs.get("cookies")           # dict or list of dicts
        platform = detect_platform(url)

        try:
            import html2text
            from scrapling.fetchers import Fetcher
        except ImportError as e:
            raise RuntimeError(f"Scrapling/html2text 未安装: {e}")

        # Cookie 注入模式（付费内容）
        if cookie_file or cookies:
            return self._fetch_with_cookies(url, platform, max_chars, cookie_file, cookies)

        # 普通 Scrapling 抓取
        try:
            page = Fetcher(auto_match=False).get(
                url,
                headers={"Referer": "https://www.google.com/search?q=site"},
            )
        except Exception as e:
            raise RuntimeError(f"Scrapling Fetcher 失败: {e}")

        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = False
        h.body_width = 0

        # 平台专用 + 通用选择器
        if "mp.weixin.qq.com" in url:
            selectors = ["div#js_content", "div.rich_media_content"]
        else:
            selectors = [
                "article", "main",
                ".post-content", ".entry-content", ".article-body",
                '[class*="body"]', '[class*="content"]', '[class*="article"]',
            ]

        title = ""
        author = ""
        content = ""

        # 尝试提取 title
        title_els = page.css("title")
        if title_els:
            title = title_els[0].text.strip()

        for selector in selectors:
            els = page.css(selector)
            if els:
                html_raw = fix_lazy_images(els[0].html_content)
                md = h.handle(html_raw)
                md = clean_markdown(md, max_chars)
                if len(md) > 300:
                    content = md
                    break

        if not content:
            # fallback: full page
            html_raw = fix_lazy_images(page.html_content)
            md = h.handle(html_raw)
            content = clean_markdown(md, max_chars)

        if not content or len(content) < 50:
            raise RuntimeError("Scrapling 提取内容过短")

        return FetchResult(
            platform=platform,
            title=title,
            author=author,
            content=content,
            url=url,
            source="scrapling",
        )

    def _fetch_with_cookies(
        self, url: str, platform: str, max_chars: int,
        cookie_file=None, cookies=None
    ) -> FetchResult:
        """Playwright Cookie 注入模式"""
        import asyncio

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError("playwright 未安装，无法使用 Cookie 注入")

        # 解析 cookie 文件（Netscape 格式）
        if cookie_file and not cookies:
            cookies = _parse_netscape_cookies(cookie_file)

        async def _run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                ctx = await browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
                if cookies:
                    await ctx.add_cookies(cookies if isinstance(cookies, list) else [cookies])
                page = await ctx.new_page()
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(3000)
                # 滚动加载懒加载内容
                for _ in range(10):
                    await page.keyboard.press("End")
                    await page.wait_for_timeout(300)
                title = await page.title()
                content = await page.evaluate(
                    "document.querySelector('article, main, .content, [role=main]')"
                    "?.innerText || document.body.innerText"
                )
                await browser.close()
                return title, content

        title, content = asyncio.run(_run())
        content = truncate_content(content or "", max_chars)

        if not content or len(content) < 50:
            raise RuntimeError("Cookie 注入模式内容过短")

        return FetchResult(
            platform=platform,
            title=title or "",
            author="",
            content=content,
            url=url,
            source="scrapling-playwright",
        )


def _parse_netscape_cookies(cookie_file: str) -> list:
    """解析 Netscape 格式 cookie 文件为 Playwright 格式"""
    cookies = []
    try:
        with open(cookie_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, _, path, secure, expires, name, value = parts[:7]
                cookies.append({
                    "name": name,
                    "value": value,
                    "domain": domain.lstrip("."),
                    "path": path,
                    "secure": secure.upper() == "TRUE",
                    "sameSite": "Lax",
                })
    except Exception:
        pass
    return cookies
