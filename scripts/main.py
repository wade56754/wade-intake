#!/usr/bin/env python3
"""
学习助手 — 主入口
用法: python3 main.py "<URL>"

流程: 路由 → 抓取 → 分析 → 入库判断 → 输出
"""

import sys
import os
import time

# 把 scripts 目录加入 path，这样 fetchers 包可以被找到
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from router import route
from fetchers.twitter import fetch_tweet, fetch_profile
from fetchers.youtube import fetch_video as youtube_fetch_video
from fetchers.douyin import fetch_video as douyin_fetch_video
from fetchers.xiaohongshu import fetch_note as xhs_fetch_note
from fetchers.github import fetch_repo as github_fetch_repo
from fetchers.feishu import fetch_document as feishu_fetch_document
from fetchers.zhihu import (
    fetch_answer as zhihu_fetch_answer,
    fetch_question as zhihu_fetch_question,
    fetch_article as zhihu_fetch_article,
)
from analyzer import analyze_single, _get_title, _get_content, _get_author
from storage import store_article, _should_store
from wiki_writeback import append_log_entry
import failure_logger

# ── web-scraper 统一抓取层（可选外部依赖，本仓库不含实现，需自备并设置路径） ──
WEB_SCRAPER_DIR = os.environ.get('LEARNING_ASSISTANT_WEB_SCRAPER_DIR', '')
_web_scraper_route = None
if WEB_SCRAPER_DIR and os.path.isdir(WEB_SCRAPER_DIR):
    sys.path.insert(0, WEB_SCRAPER_DIR)
    try:
        from fetch import route as _ws_route
        _web_scraper_route = _ws_route
    except ImportError:
        pass

# 知识库根目录
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
KNOWLEDGE_DIR = os.path.join(SKILL_DIR, 'knowledge', 'articles')


# ─── 超时重试 ───

def fetch_with_retry(fetch_fn, *args, max_retries=3, url='', platform='', **kwargs):
    """
    带重试的 fetcher 包装器
    重试延迟: [5, 10, 0]（第1次等5s，第2次等10s，第3次直接抛异常）
    """
    delays = [5, 10, 0]
    last_exc = None

    for attempt in range(max_retries):
        try:
            return fetch_fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            delay = delays[attempt] if attempt < len(delays) else 0
            print(f'[retry] 第 {attempt + 1}/{max_retries} 次失败: {e}')
            failure_logger.log(
                url=url,
                platform=platform,
                step=f'fetch_retry_{attempt + 1}',
                error=str(e),
                exc=e,
            )
            if attempt < max_retries - 1 and delay > 0:
                print(f'[retry] 等待 {delay}s 后重试...')
                time.sleep(delay)

    raise last_exc


# ─── 抓取分发 ───

def dispatch_fetch(route_info):
    """根据路由信息分发到对应 fetcher"""
    platform = route_info['platform']
    mode = route_info.get('mode', '')
    url = route_info['url']

    try:
        # Twitter, YouTube, Douyin, 小红书保留 TikHub API fetcher
        if platform == 'twitter':
            if mode == 'tweet':
                return fetch_with_retry(fetch_tweet, route_info['tweet_id'],
                                        url=url, platform=platform)
            elif mode == 'profile':
                return fetch_with_retry(fetch_profile, route_info['username'],
                                        url=url, platform=platform)

        elif platform == 'youtube':
            return fetch_with_retry(youtube_fetch_video, url, route_info.get('video_id'),
                                    url=url, platform=platform)

        elif platform in ('douyin', 'tiktok'):
            return fetch_with_retry(douyin_fetch_video, url, route_info.get('video_id'), platform,
                                    url=url, platform=platform)

        elif platform == 'xiaohongshu':
            return fetch_with_retry(xhs_fetch_note, url, route_info.get('note_id'),
                                    url=url, platform=platform)

        elif platform == 'zhihu':
            if mode == 'answer':
                return fetch_with_retry(
                    zhihu_fetch_answer,
                    url,
                    route_info.get('question_id'),
                    route_info.get('answer_id'),
                    url=url,
                    platform=platform,
                )
            elif mode == 'question':
                return fetch_with_retry(
                    zhihu_fetch_question,
                    url,
                    route_info.get('question_id'),
                    url=url,
                    platform=platform,
                )
            elif mode == 'article':
                return fetch_with_retry(
                    zhihu_fetch_article,
                    url,
                    route_info.get('article_id'),
                    url=url,
                    platform=platform,
                )
            raise Exception(f'不支持的知乎模式: {mode}')

        elif platform == 'github':
            return fetch_with_retry(
                github_fetch_repo,
                route_info.get('owner'),
                route_info.get('repo'),
                url,
                url=url,
                platform=platform,
            )

        elif platform == 'feishu':
            # lark-cli 路径（bot 身份），OC web-scraper feishu 适配器退役后的替代
            return fetch_with_retry(feishu_fetch_document, url, route_info.get('doc_id'),
                                    url=url, platform=platform)

        # 其他平台（wechat_mp, flowus, web）统一走 web-scraper
        else:
            if _web_scraper_route:
                result = _web_scraper_route(url, platform=platform if platform in ('wechat', 'feishu') else None)
                if result and result.content:
                    return result.to_dict()

            # web-scraper 不可用时报错
            raise Exception(f'web-scraper 不可用，且无对应 {platform} 的本地 fetcher')

    except Exception as e:
        failure_logger.log(
            url=url,
            platform=platform,
            step='dispatch_fetch',
            error=str(e),
            exc=e,
            input_params={'mode': mode, 'tweet_id': route_info.get('tweet_id'),
                          'video_id': route_info.get('video_id'),
                          'question_id': route_info.get('question_id'),
                          'answer_id': route_info.get('answer_id'),
                          'article_id': route_info.get('article_id')},
        )
        raise


# ─── Profile 批量处理 ───

def process_profile(profile_data):
    """
    处理 Twitter profile 模式
    - 逐条分析帖子
    - 高价值逐一入库
    - 输出汇总
    """
    username = profile_data.get('username', '')
    name = profile_data.get('name', username)
    bio = profile_data.get('bio', '')
    followers = profile_data.get('followers', 0)
    tweets = profile_data.get('tweets', [])

    outputs = []
    stored_count = 0
    total = len(tweets)

    # 汇总头部
    header = f'👤 {name} (@{username})\n'
    header += f'粉丝: {followers:,} | 帖子数: {total}\n'
    if bio:
        header += f'简介: {bio[:100]}\n'
    header += '\n' + '═' * 40 + '\n'
    outputs.append(header)

    for i, tweet in enumerate(tweets):
        analysis = analyze_single(tweet)
        should_store, reason = _should_store(tweet)

        if should_store:
            filepath = store_article(tweet, analysis)
            stored_count += 1
            outputs.append(f'\n[{i+1}/{total}] ✅ 已入库\n{analysis}\n📁 {filepath}\n')
        else:
            outputs.append(f'\n[{i+1}/{total}] ⚪ 跳过 — {reason}\n'
                          f'📌 {_get_title(tweet)[:60]}\n')

    # 汇总尾部
    footer = '\n' + '═' * 40 + '\n'
    footer += f'📊 汇总: {total} 条帖子，{stored_count} 条入库\n'
    outputs.append(footer)

    return '\n'.join(outputs)


# ─── 主流程 ───

def main(url):
    """主入口"""
    # 1. 路由
    route_info = route(url)
    platform = route_info['platform']
    mode = route_info.get('mode', '')

    print(f'🔍 路由结果: {platform}/{mode}')
    print(f'   URL: {url}\n')

    # 2. 抓取
    try:
        data = dispatch_fetch(route_info)
    except Exception as e:
        # dispatch_fetch 内部已记录 failure_logger，这里只打印
        print(f'❌ 抓取失败: {e}')
        return

    if data.get('error'):
        failure_logger.log(
            url=url,
            platform=platform,
            step='dispatch_fetch',
            error=data['error'],
        )
        print(f'❌ 抓取错误: {data["error"]}')
        # 如果有部分数据，继续处理
        if not _get_content(data) and not _get_title(data):
            return

    # 3. 分析 + 入库 + 输出
    if platform == 'twitter' and mode == 'profile':
        # Profile 模式：批量处理
        output = process_profile(data)
        print(output)
    else:
        # 单条模式
        analysis = analyze_single(data)
        print(analysis)

        # 4. 入库判断
        should_store, reason = _should_store(data)
        if should_store:
            try:
                filepath = store_article(data, analysis)
                print(f'\n📁 已入库: {filepath}')

                # 5. 回填 workspace-wiki log.md（静默失败）
                try:
                    written = append_log_entry(
                        platform=platform,
                        author=_get_author(data),
                        title=_get_title(data),
                        url=url,
                        score=data.get('_score'),
                        filepath=filepath,
                    )
                    if written:
                        print('📝 workspace-wiki/log.md 已追加 ingest 条目')
                except Exception as e:
                    # wiki 回填不影响主流程
                    print(f'[wiki] 回填 log.md 失败（不影响入库）: {e}')
            except Exception as e:
                failure_logger.log(
                    url=url,
                    platform=platform,
                    step='store_article',
                    error=str(e),
                    exc=e,
                )
                print(f'❌ 入库失败: {e}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python3 main.py "<URL>" [--transcribe-x-video]')
        print('示例:')
        print('  python3 main.py "https://x.com/NFTCPS/status/2029847646819733547"')
        print('  python3 main.py "https://x.com/NFTCPS"')
        print('  python3 main.py "https://x.com/user/status/123" --transcribe-x-video')
        sys.exit(1)

    url = sys.argv[1]
    if '--transcribe-x-video' in sys.argv[2:]:
        os.environ['WADE_LEARNING_X_VIDEO_TRANSCRIBE'] = '1'
    main(url)
