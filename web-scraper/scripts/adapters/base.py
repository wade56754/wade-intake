"""
base.py — 抽象基类，定义统一接口
所有适配器必须继承 BaseFetcher 并实现 fetch() 方法
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class FetchResult:
    """标准化抓取结果"""
    platform: str           # wechat_mp | feishu | github | web
    title: str
    author: str
    content: str            # Markdown 格式正文
    url: str
    source: str             # jina-reader | scrapling | urllib | chrome-cdp | wechat-skill | feishu-mcp
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "platform": self.platform,
            "title": self.title,
            "author": self.author,
            "content": self.content,
            "url": self.url,
            "source": self.source,
            "fetched_at": self.fetched_at,
        }
        if self.error:
            d["error"] = self.error
        return d


class BaseFetcher(ABC):
    """所有抓取适配器的抽象基类"""

    @abstractmethod
    def fetch(self, url: str, **kwargs) -> FetchResult:
        """
        抓取 URL，返回标准化 FetchResult

        Args:
            url: 目标 URL
            **kwargs: 适配器特有参数（如 cookie_file, max_chars）
        Returns:
            FetchResult
        Raises:
            RuntimeError: 当抓取失败时抛出，由上层路由捕获降级
        """
        ...

    def can_handle(self, url: str) -> bool:
        """判断此适配器是否适合处理该 URL（默认全部能处理）"""
        return True
