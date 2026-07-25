from .base import BaseFetcher, FetchResult
from .jina import JinaFetcher
from .scrapling_adapter import ScraplingFetcher
from .urllib_adapter import UrllibFetcher
from .cdp import CdpFetcher
from .wechat import WechatFetcher
from .feishu import FeishuFetcher
from .github import GithubFetcher
from .youtube import YoutubeFetcher

__all__ = [
    "BaseFetcher",
    "FetchResult",
    "JinaFetcher",
    "ScraplingFetcher",
    "UrllibFetcher",
    "CdpFetcher",
    "WechatFetcher",
    "FeishuFetcher",
    "GithubFetcher",
    "YoutubeFetcher",
]
