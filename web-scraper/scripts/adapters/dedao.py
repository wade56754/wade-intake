"""
dedao.py — 得到课程/文章抓取适配器
支持两种模式：
  1. 单篇文章 URL：https://www.dedao.cn/course/article?id=xxx
  2. 课程批量抓取：通过 course_enid 拉全量文章列表

依赖 Chrome CDP（登录态），通过 playwright 复用已登录的 Chrome。
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Optional

from .base import BaseFetcher, FetchResult
from utils import truncate_content

CDP_URL = "http://localhost:9222"


def _safe_filename(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', title)[:80]


def _parse_blocks(blocks: list) -> str:
    """解析得到富文本 blocks → Markdown"""
    lines = []
    for b in blocks:
        bt = b.get('type', '')
        # 顶层 text 字段
        top = b.get('text', '')
        if isinstance(top, str) and top.strip():
            if bt in ('heading', 'header'):
                lv = b.get('level', 2)
                lines.append(f"{'#' * min(lv + 1, 4)} {top.strip()}")
            else:
                lines.append(top.strip())
            lines.append('')
            continue
        # contents 嵌套
        parts = []
        for c in b.get('contents', []):
            t = c.get('text', {})
            if isinstance(t, dict):
                parts.append(t.get('content', ''))
            elif isinstance(t, str):
                parts.append(t)
        text = ''.join(parts).strip()
        if text:
            if bt in ('heading', 'header'):
                lv = b.get('level', 2)
                lines.append(f"{'#' * min(lv + 1, 4)} {text}")
            else:
                lines.append(text)
            lines.append('')
    return '\n'.join(lines).strip()


async def _get_dedao_page():
    """获取已连接 Chrome 中的得到页面（或新开一个）"""
    from playwright.async_api import async_playwright
    pw = async_playwright()
    p = await pw.__aenter__()
    browser = await p.chromium.connect_over_cdp(CDP_URL)
    ctx = browser.contexts[0]
    page = None
    for pg in ctx.pages:
        if 'dedao.cn' in pg.url:
            page = pg
            break
    if page is None:
        page = await ctx.new_page()
    if 'dedao.cn' not in page.url:
        await page.goto('https://www.dedao.cn', wait_until='domcontentloaded')
        await page.wait_for_timeout(2000)
    return pw, browser, page


async def _fetch_article_detail(page, article_enid: str, class_enid: str) -> dict:
    """通过 API 获取单篇文章详情"""
    result = await page.evaluate("""
        async ([article_enid, class_enid]) => {
            const r = await fetch('https://www.dedao.cn/pc/bauhinia/pc/class/free_article_detail', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                credentials: 'include',
                body: JSON.stringify({article_enid, class_enid})
            });
            return r.json();
        }
    """, [article_enid, class_enid])
    return result


async def _fetch_course_articles(page, course_enid: str) -> list:
    """获取课程全量文章列表"""
    result = await page.evaluate("""
        async ([detail_id]) => {
            const r = await fetch('https://www.dedao.cn/pc/bauhinia/pc/class/free_article_list', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                credentials: 'include',
                body: JSON.stringify({detail_id, count: 0, offset: 0})
            });
            return r.json();
        }
    """, [course_enid])
    return result.get('c', {}).get('article_list', [])


class DedaoFetcher(BaseFetcher):
    """得到平台适配器 — 单篇文章抓取"""

    def fetch(self, url: str, **kwargs) -> FetchResult:
        """
        支持得到文章 URL：
          https://www.dedao.cn/course/article?id=xxx&enid=yyy
        """
        max_chars = kwargs.get("max_chars", 50000)

        # 解析 URL 参数
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        article_enid = params.get('enid', [None])[0] or params.get('id', [None])[0]
        class_enid = params.get('class_enid', [None])[0] or params.get('course_id', [None])[0] or ''

        if not article_enid:
            raise ValueError(f"无法从 URL 解析 article_enid: {url}")

        result = asyncio.run(self._async_fetch(article_enid, class_enid, url, max_chars))
        return result

    async def _async_fetch(self, article_enid: str, class_enid: str, url: str, max_chars: int) -> FetchResult:
        pw, browser, page = await _get_dedao_page()
        try:
            det = await _fetch_article_detail(page, article_enid, class_enid)
            info = det.get('c', {}).get('article_info', {})
            title = info.get('title', '')
            blocks = info.get('content', {}).get('blocks', [])
            if blocks:
                content = _parse_blocks(blocks)
            else:
                content = info.get('content_without_fmt', '') or info.get('summary', '')
            content = truncate_content(content, max_chars)
            return FetchResult(
                platform='dedao',
                title=title,
                author=info.get('author', ''),
                content=content,
                url=url,
                source='dedao-cdp-api',
            )
        finally:
            await browser.close()


class DedaoCourseBatchFetcher:
    """
    得到课程批量抓取器（非标准 BaseFetcher，独立使用）

    用法：
        fetcher = DedaoCourseBatchFetcher(output_dir="/path/to/output")
        await fetcher.scrape_course("课程名", "课程的 course_enid")
    """

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)

    async def scrape_course(self, course_name: str, course_enid: str) -> dict:
        """抓取整个课程，断点续传，返回统计"""
        out = self.output_dir / course_name
        out.mkdir(parents=True, exist_ok=True)
        prog_path = out / '.progress.json'
        prog = json.loads(prog_path.read_text()) if prog_path.exists() else {'done': []}
        done_set = set(prog['done'])

        pw, browser, page = await _get_dedao_page()
        try:
            articles = await _fetch_course_articles(page, course_enid)
            total = len(articles)
            skip = sum(1 for a in articles if a.get('enid', '') in done_set or
                      (out / (_safe_filename(a.get('title', '')) + '.md')).exists())
            print(f"[{course_name}] 共 {total} 篇，已完成 {skip} 篇，待抓 {total - skip} 篇", flush=True)

            saved, failed = 0, 0
            for i, art in enumerate(articles):
                enid = art.get('enid', '')
                title = art.get('title', f'no_title_{i}')
                fname = _safe_filename(title) + '.md'
                fpath = out / fname

                if enid in done_set or fpath.exists():
                    continue

                try:
                    det = await _fetch_article_detail(page, enid, course_enid)
                    info = det.get('c', {}).get('article_info', {})
                    blocks = info.get('content', {}).get('blocks', [])
                    if blocks:
                        content = _parse_blocks(blocks)
                    else:
                        content = info.get('content_without_fmt', '') or art.get('summary', '')

                    fpath.write_text(f"# {title}\n\n{content}\n", encoding='utf-8')
                    done_set.add(enid)
                    prog['done'] = list(done_set)
                    prog_path.write_text(json.dumps(prog, ensure_ascii=False))
                    saved += 1
                    if saved % 20 == 0 or saved == 1:
                        print(f"  [{course_name}] {len(done_set)}/{total} ✓ {title[:30]}", flush=True)
                    await asyncio.sleep(0.4)

                except Exception as e:
                    err_msg = str(e)
                    if 'SyntaxError' in err_msg or 'JSON' in err_msg:
                        failed += 1
                    else:
                        print(f"  [{course_name}] ✗ {title[:30]}: {err_msg[:80]}", flush=True)
                        if 'closed' in err_msg.lower() or 'ECONNREFUSED' in err_msg:
                            print(f"  [警告] 连接断开，等待 3s 后继续...", flush=True)
                            await asyncio.sleep(3)
                    await asyncio.sleep(1)

            print(f"[{course_name}] ✅ 完成！新增 {saved}，失败/跳过 {failed}，总计 {len(done_set)}/{total}", flush=True)
            return {'saved': saved, 'failed': failed, 'total': total, 'done': len(done_set)}

        finally:
            await browser.close()

    async def scrape_courses(self, courses: list) -> None:
        """批量抓取多个课程（顺序执行）"""
        for name, enid in courses:
            try:
                await self.scrape_course(name, enid)
            except Exception as e:
                print(f"[错误] {name} 抓取失败: {e}", flush=True)
