#!/usr/bin/env python3
"""
fetch.py — web-scraper 统一主入口
URL → 平台路由 → 适配器调度 → 标准化 JSON 输出

用法:
  python3 fetch.py <URL> [--json] [--max-chars N] [--cookie-file PATH]
  python3 fetch.py https://mp.weixin.qq.com/s/xxx --json
  python3 fetch.py https://feishu.cn/docs/xxx --max-chars 20000
  python3 fetch.py https://example.com --cookie-file ~/cookies.txt
"""

import argparse
import json
import os
import sys

# 将 scripts/ 加入 path，支持直接运行
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

# 可选：一个提供 `log(url=, platform=, step=, error=)` 接口的外部失败日志模块。
# 设置 WEB_SCRAPER_FAILURE_LOGGER_DIR 指向其所在目录即可接入；未设置时跳过，不影响主流程。
try:
    _evo_log_dir = os.environ.get('WEB_SCRAPER_FAILURE_LOGGER_DIR', '')
    if _evo_log_dir:
        sys.path.insert(0, _evo_log_dir)
    from failure_logger import log as _evo_log
except ImportError:
    _evo_log = None

from utils import detect_platform
from adapters.base import FetchResult
from adapters.jina import JinaFetcher
from adapters.scrapling_adapter import ScraplingFetcher
from adapters.urllib_adapter import UrllibFetcher
from adapters.cdp import CdpFetcher
from adapters.wechat import WechatFetcher
from adapters.feishu import FeishuFetcher
from adapters.github import GithubFetcher
from adapters.youtube import YoutubeFetcher
from adapters.dedao import DedaoFetcher
from utils import is_github_repo_root


def route(url: str, **kwargs) -> FetchResult:
    """
    URL 路由 + 适配器调度

    路由策略：
    | URL 模式                          | 主适配器      | 降级链                        |
    |----------------------------------|--------------|------------------------------|
    | mp.weixin.qq.com                 | WechatFetcher | curl→Scrapling→urllib         |
    | feishu.cn / larksuite.com        | FeishuFetcher | CDP→Jina→urllib              |
    | github.com                       | JinaFetcher   | Jina→urllib                  |
    | --cookie-file 指定（付费内容）      | ScraplingFetcher（Playwright模式）→ urllib |
    | 其他通用网页                        | JinaFetcher   | Jina→Scrapling→urllib        |
    """
    auto_platform = detect_platform(url)
    force_platform = kwargs.get("platform", "auto")
    if force_platform == "wechat":
        platform = "wechat_mp"
    elif force_platform == "feishu":
        platform = "feishu"
    elif force_platform == "general":
        platform = "web"
    else:
        platform = auto_platform
    cookie_file = kwargs.get("cookie_file")
    max_chars = kwargs.get("max_chars", 30000)

    # ── 平台路由 ────────────────────────────────────────────────────────────
    if platform == "wechat_mp":
        try:
            return WechatFetcher().fetch(url, **kwargs)
        except Exception:
            pass  # 降级到通用三级链

    if platform == "feishu":
        try:
            return FeishuFetcher().fetch(url, **kwargs)
        except Exception:
            pass  # 降级到通用三级链

    # ── 付费内容（Cookie 注入）──────────────────────────────────────────────
    if cookie_file:
        fetcher = ScraplingFetcher()
        try:
            return fetcher.fetch(url, **kwargs)
        except Exception:
            pass
        # fallback: urllib
        try:
            return UrllibFetcher().fetch(url, **kwargs)
        except Exception as e:
            return _error_result(url, platform, str(e))

    # ── GitHub 仓库主页 ──────────────────────────────────────────────────
    if platform == "github" and is_github_repo_root(url):
        try:
            return GithubFetcher().fetch(url, **kwargs)
        except Exception:
            pass  # 降级到通用链

    # ── YouTube 视频 ─────────────────────────────────────────────────────
    if platform == "youtube":
        try:
            return YoutubeFetcher().fetch(url, **kwargs)
        except Exception:
            pass  # 降级到通用链

    # ── 得到课程/文章 ──────────────────────────────────────────────────────
    if platform == "dedao":
        try:
            return DedaoFetcher().fetch(url, **kwargs)
        except Exception:
            pass  # 降级到通用链

    # ── 通用三级降级：Jina → Scrapling → urllib ────────────────────────────
    errors = []

    # 1. Jina Reader
    try:
        result = JinaFetcher().fetch(url, **kwargs)
        if result.content and len(result.content) >= 100:
            return result
    except Exception as e:
        errors.append(f"jina: {e}")

    # 2. Scrapling
    try:
        result = ScraplingFetcher().fetch(url, **kwargs)
        if result.content and len(result.content) >= 100:
            return result
    except Exception as e:
        errors.append(f"scrapling: {e}")

    # 3. urllib（最终兜底）
    try:
        result = UrllibFetcher().fetch(url, **kwargs)
        if result.content and len(result.content) >= 50:
            return result
    except Exception as e:
        errors.append(f"urllib: {e}")

    # 全部失败
    return _error_result(url, platform, " | ".join(errors))


def _error_result(url: str, platform: str, error: str) -> FetchResult:
    # skill-self-evolution: 记录失败
    if _evo_log:
        try:
            _evo_log(url=url, platform=platform, step='route_fetch', error=error)
        except Exception:
            pass
    return FetchResult(
        platform=platform,
        title="",
        author="",
        content="",
        url=url,
        source="failed",
        error=error,
    )


def main():
    parser = argparse.ArgumentParser(
        description="web-scraper — 统一网页内容抓取",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 fetch.py "https://mp.weixin.qq.com/s/xxx" --json
  python3 fetch.py "https://feishu.cn/docs/xxx" --max-chars 20000
  python3 fetch.py "https://example.com" --cookie-file ~/cookies.txt
  python3 fetch.py "https://example.com"          # 纯文本输出
        """,
    )
    parser.add_argument("url", help="目标 URL")
    parser.add_argument("--json", action="store_true", help="输出标准化 JSON（默认输出纯文本）")
    parser.add_argument("--max-chars", type=int, default=30000, metavar="N", help="最大字符数（默认 30000）")
    parser.add_argument("--cookie-file", metavar="PATH", help="Netscape 格式 Cookie 文件路径（付费内容）")
    parser.add_argument("--wait-ms", type=int, default=5000, metavar="MS", help="CDP 等待页面加载时间（ms，默认 5000）")
    parser.add_argument(
        "--platform",
        default="auto",
        choices=["auto", "wechat", "feishu", "general"],
        help="强制指定平台（默认 auto，自动识别）",
    )
    args = parser.parse_args()

    result = route(
        args.url,
        max_chars=args.max_chars,
        cookie_file=args.cookie_file,
        wait_ms=args.wait_ms,
        platform=args.platform,
    )

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        if result.error:
            print(f"[错误] {result.error}", file=sys.stderr)
            sys.exit(1)
        if result.title:
            print(f"# {result.title}\n")
        if result.author:
            print(f"> 作者: {result.author}\n")
        print(result.content)


if __name__ == "__main__":
    main()
