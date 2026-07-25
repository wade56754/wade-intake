#!/usr/bin/env python3
"""
Twitter/X 抓取器
- mode=tweet: 单条帖子详情
  主路径 1: Nitter（本地 http://127.0.0.1:8788，免费，需 Cookie）
  主路径 2: FxTwitter API（免费，零 API Key）
  fallback: TikHub API（付费，仅在前两路均失败时用）
- mode=profile: 用户主页帖子（TikHub，因为 FxTwitter 不支持翻页）
- mode=replies: Nitter 优先（本地），再降级 x-tweet-fetcher + Camofox
- mode=timeline_xtf: 用户时间线（Nitter 优先，再降级 x-tweet-fetcher + Camofox）

Nitter 地址: NITTER_URL 环境变量，默认 http://127.0.0.1:8788
x-tweet-fetcher（评论区/时间线降级用，可选外部依赖，本仓库不含实现）：
  设置 XTF_DIR 环境变量指向其安装目录（需提供 scripts/fetch_tweet.py 与
  scripts/nitter_client.py，接口约定见下方函数实现）；未设置时相关降级路径自动跳过。
Camofox 端口: 9377（默认，可用 --port 覆盖）
"""

import json
import os
import sys
import shutil
import subprocess
import tempfile
from fetchers.base import fetch as tikhub_fetch

# x-tweet-fetcher 脚本路径（可选外部依赖，未设置时相关降级路径自动跳过）
XTF_DIR = os.environ.get('XTF_DIR', '')
XTF_SCRIPT = os.path.join(XTF_DIR, 'scripts', 'fetch_tweet.py')
XTF_NITTER = os.path.join(XTF_DIR, 'scripts', 'nitter_client.py')

# Nitter 地址（本地 Docker）
NITTER_URL = os.environ.get('NITTER_URL', 'http://127.0.0.1:8788')


def _env_truthy(name):
    return os.environ.get(name, '').strip().lower() in ('1', 'true', 'yes', 'y', 'on')


def _pick_best_video_url(video):
    """Choose the best direct mp4 video URL when variants are available."""
    direct_url = video.get('url') or video.get('video_url') or video.get('media_url_https') or ''
    variants = video.get('variants') or video.get('videoVariants') or []
    best = None
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        url = variant.get('url') or ''
        content_type = variant.get('content_type') or variant.get('contentType') or ''
        if not url:
            continue
        bitrate = int(variant.get('bitrate') or 0)
        is_mp4 = 'mp4' in content_type or '.mp4' in url
        if is_mp4 and (best is None or bitrate > best.get('bitrate', 0)):
            best = {'url': url, 'bitrate': bitrate, 'content_type': content_type}
    return (best or {}).get('url') or direct_url


def _normalise_duration_seconds(value):
    if value in (None, ''):
        return None
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    if duration > 100000:
        duration = duration / 1000
    return round(duration, 3)


def _extract_media(tweet_obj):
    """Extract images and videos from common X/FxTwitter/TikHub shapes."""
    media = {'videos': [], 'images': []}
    seen = set()

    def add_video(item):
        if not isinstance(item, dict):
            return
        url = _pick_best_video_url(item)
        if not url or url in seen:
            return
        seen.add(url)
        duration = _normalise_duration_seconds(
            item.get('duration') or item.get('duration_millis') or item.get('durationMillis')
        )
        video = {
            'url': url,
            'thumbnail': item.get('thumbnail') or item.get('thumb') or item.get('media_url_https') or '',
            'duration': duration,
            'variants': item.get('variants') or item.get('videoVariants') or [],
        }
        media['videos'].append(video)

    def add_image(item):
        if not isinstance(item, dict):
            return
        url = (
            item.get('url')
            or item.get('media_url_https')
            or item.get('media_url')
            or item.get('src')
            or ''
        )
        if not url or url in seen:
            return
        seen.add(url)
        media['images'].append({'url': url})

    def consume(value):
        if isinstance(value, dict):
            for item in value.get('videos') or []:
                add_video(item)
            for item in value.get('photos') or value.get('images') or []:
                add_image(item)
            for item in value.get('media') or []:
                consume(item)
            media_type = value.get('type') or value.get('media_type') or ''
            if media_type in ('video', 'animated_gif'):
                add_video(value)
            elif media_type == 'photo':
                add_image(value)
        elif isinstance(value, list):
            for item in value:
                consume(item)

    legacy = tweet_obj.get('legacy') if isinstance(tweet_obj.get('legacy'), dict) else {}
    candidates = [
        tweet_obj.get('media'),
        tweet_obj.get('entities'),
        tweet_obj.get('extended_entities'),
        legacy.get('entities'),
        legacy.get('extended_entities'),
    ]
    for candidate in candidates:
        consume(candidate)

    return media


def _transcribe_first_video(media, tweet_id):
    """Optionally transcribe the first X video via ffmpeg + local Whisper."""
    videos = media.get('videos') or []
    if not videos:
        return '', ''
    if not _env_truthy('WADE_LEARNING_X_VIDEO_TRANSCRIBE'):
        return '', 'disabled'

    video_url = videos[0].get('url') or ''
    if not video_url:
        return '', 'missing_video_url'
    if not shutil.which('ffmpeg'):
        return '', 'ffmpeg_missing'

    model = os.environ.get('WADE_LEARNING_X_VIDEO_WHISPER_MODEL', 'mlx-community/whisper-tiny')
    language = os.environ.get('WADE_LEARNING_X_VIDEO_LANGUAGE', 'zh')
    ffmpeg_timeout = int(os.environ.get('WADE_LEARNING_X_VIDEO_FFMPEG_TIMEOUT', '1800'))
    whisper_timeout = int(os.environ.get('WADE_LEARNING_X_VIDEO_WHISPER_TIMEOUT', '3600'))

    with tempfile.TemporaryDirectory(prefix=f'x-video-{tweet_id or "tweet"}-') as tmpdir:
        audio_path = os.path.join(tmpdir, 'audio.wav')
        ffmpeg_cmd = [
            'ffmpeg',
            '-y',
            '-i',
            video_url,
            '-vn',
            '-acodec',
            'pcm_s16le',
            '-ar',
            '16000',
            '-ac',
            '1',
            audio_path,
        ]
        proc = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=ffmpeg_timeout)
        if proc.returncode != 0:
            return '', f'ffmpeg_failed: {(proc.stderr or proc.stdout).strip()[:300]}'

        if shutil.which('mlx_whisper'):
            cmd = [
                'mlx_whisper',
                audio_path,
                '--model',
                model,
                '--language',
                language,
                '--output-dir',
                tmpdir,
                '--output-format',
                'txt',
                '--verbose',
                'False',
            ]
            transcript_path = os.path.join(tmpdir, 'audio.txt')
        elif shutil.which('whisper'):
            fallback_model = os.environ.get('WADE_LEARNING_X_VIDEO_OPENAI_WHISPER_MODEL', 'turbo')
            cmd = [
                'whisper',
                audio_path,
                '--model',
                fallback_model,
                '--language',
                language,
                '--output_dir',
                tmpdir,
                '--output_format',
                'txt',
                '--verbose',
                'False',
            ]
            transcript_path = os.path.join(tmpdir, 'audio.txt')
        else:
            return '', 'whisper_missing'

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=whisper_timeout)
        if proc.returncode != 0:
            return '', f'whisper_failed: {(proc.stderr or proc.stdout).strip()[:300]}'

        if os.path.exists(transcript_path):
            with open(transcript_path, 'r', encoding='utf-8', errors='replace') as f:
                transcript = f.read().strip()
            if transcript:
                return transcript, 'ok'
        return '', 'empty_transcript'


def _attach_media_and_transcript(result, media, tweet_id):
    if not media.get('videos') and not media.get('images'):
        return result

    result['media'] = media
    result['video_count'] = len(media.get('videos') or [])
    result['image_count'] = len(media.get('images') or [])

    if result['video_count']:
        transcript, status = _transcribe_first_video(media, tweet_id)
        result['transcript_status'] = status
        if transcript:
            result['transcript'] = transcript
            result['full_text'] = (result.get('full_text') or result.get('text') or '') + '\n\n' + transcript
            result['text'] = result['full_text']
            result['transcript_source'] = 'local_whisper'
        elif status == 'disabled':
            print('[twitter/video] 检测到视频；如需转写，运行 main.py 时加 --transcribe-x-video 或设置 WADE_LEARNING_X_VIDEO_TRANSCRIBE=1')
        else:
            result['transcript_error'] = status

    return result


# ---------------------------------------------------------------------------
# Nitter 主路径（本地实例，免费，零延迟）
# ---------------------------------------------------------------------------

def _check_nitter() -> bool:
    """检查本地 Nitter 是否可用"""
    import urllib.request
    try:
        req = urllib.request.Request(NITTER_URL + '/', headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as resp:
            resp.read(64)
        return True
    except Exception:
        return False


def _fetch_tweet_nitter(tweet_id, username=None):
    """
    通过本地 Nitter 实例抓取单条推文。
    需要 Nitter 运行在 NITTER_URL（默认 http://127.0.0.1:8788）
    返回标准化结构，失败返回 None。
    """
    if not os.path.exists(XTF_NITTER):
        return None

    if not username:
        return None  # Nitter 需要 username/status/id 路径

    try:
        proc = subprocess.run(
            [sys.executable, XTF_NITTER,
             '--tweet', f'{username}/{tweet_id}',
             '--nitter-url', NITTER_URL],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, 'NITTER_URL': NITTER_URL},
        )
        if proc.returncode != 0:
            return None

        data = json.loads(proc.stdout)
        if data.get('error'):
            return None

        text = data.get('text', '')
        if not text or len(text) < 5:
            return None

        # Article 帖子：text 是短链，Nitter 拿不到全文，让 FxTwitter/TikHub 处理
        is_article_link = ('x.com/i/article' in text or ('t.co/' in text and len(text) < 30))
        if is_article_link:
            print(f'[twitter/nitter] Article 帖子，降级到 FxTwitter')
            return None

        result = {
            'platform': 'twitter',
            'mode': 'tweet',
            'tweet_id': tweet_id,
            'author': data.get('display_name', username),
            'username': data.get('username', username),
            'text': text,
            'full_text': text,
            'created_at': data.get('time', ''),
            'favorites': int(data.get('likes', 0) or 0),
            'bookmarks': 0,
            'views': int(data.get('views', 0) or 0),
            'retweets': int(data.get('retweets', 0) or 0),
            'replies': int(data.get('replies', 0) or 0),
            'url': data.get('url', f'https://x.com/{username}/status/{tweet_id}'),
            '_source': 'nitter',
        }
        return _attach_media_and_transcript(result, _extract_media(data), tweet_id)
    except Exception as e:
        print(f'[twitter/nitter] 失败: {e}')
        return None


# ---------------------------------------------------------------------------
# FxTwitter 主路径（单条推文，零成本）
# ---------------------------------------------------------------------------

def _fetch_tweet_fxtwitter(tweet_id, username=None):
    """
    通过 FxTwitter 公开 API 抓取单条推文。
    无需 API Key，完全免费。
    返回标准化结构，失败返回 None。
    """
    import urllib.request
    import urllib.error
    import re

    # 如果没有 username，用通配符（FxTwitter 支持只传 ID）
    if not username:
        # 先尝试 i/status 路径
        api_url = f'https://api.fxtwitter.com/i/status/{tweet_id}'
    else:
        api_url = f'https://api.fxtwitter.com/{username}/status/{tweet_id}'

    try:
        req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        if data.get('code') != 200:
            print(f'[twitter/fxtwitter] code={data.get("code")}: {data.get("message")}')
            return None

        tw = data.get('tweet', {})
        author = tw.get('author', {})
        screen_name = author.get('screen_name', '')

        text = tw.get('text', '')
        full_text = text

        # Article 全文
        article = tw.get('article')
        if article:
            blocks = article.get('content', {}).get('blocks', [])
            if blocks:
                article_full = '\n\n'.join(b.get('text', '') for b in blocks if b.get('text'))
                if article_full:
                    full_text = article_full
                    text = article_full
                    print(f'[twitter/fxtwitter] Article 全文: {len(article_full)} 字')

        media = _extract_media(tw)
        result = {
            'platform': 'twitter',
            'mode': 'tweet',
            'tweet_id': tweet_id,
            'author': author.get('name', ''),
            'username': screen_name,
            'text': text,
            'full_text': full_text,
            'created_at': tw.get('created_at', ''),
            'favorites': int(tw.get('likes', 0) or 0),
            'bookmarks': int(tw.get('bookmarks', 0) or 0),
            'views': int(tw.get('views', 0) or 0),
            'retweets': int(tw.get('retweets', 0) or 0),
            'replies': int(tw.get('replies', 0) or 0),
            'url': f'https://x.com/{screen_name}/status/{tweet_id}' if screen_name else f'https://x.com/i/status/{tweet_id}',
            '_source': 'fxtwitter',
        }
        return _attach_media_and_transcript(result, media, tweet_id)

    except Exception as e:
        print(f'[twitter/fxtwitter] 失败: {e}')
        return None


# ---------------------------------------------------------------------------
# TikHub fallback（单条推文）
# ---------------------------------------------------------------------------

def _parse_tweet_flat(tweet_data):
    """解析 TikHub 扁平格式的推文（fetch_tweet_detail / timeline item）

    TikHub user timeline 的 entries 是包装过的 GraphQL 结构，真正的字段藏在：
        entries[].content.itemContent.tweet_results.result.legacy
    或者在 pinned / tweet_detail 响应里扁平化到：
        { id / id_str / rest_id, legacy: {id_str, full_text, ...} }
    这里逐层兜底取 tweet_id，避免任一路径漏掉导致 url 空掉。
    """
    if not tweet_data:
        return None

    t = tweet_data

    # 如果是 timeline entry 包装，剥到真正的 tweet result
    if isinstance(t, dict):
        content = t.get('content') or {}
        if isinstance(content, dict):
            item_content = content.get('itemContent') or {}
            if isinstance(item_content, dict):
                tweet_results = item_content.get('tweet_results') or {}
                if isinstance(tweet_results, dict):
                    inner = tweet_results.get('result')
                    if isinstance(inner, dict):
                        t = inner
        # 有些响应是 {'tweet': {...}} 或 {'result': {...}}
        if 'legacy' not in t and isinstance(t.get('tweet'), dict):
            t = t['tweet']
        if 'legacy' not in t and isinstance(t.get('result'), dict):
            t = t['result']

    legacy = t.get('legacy') if isinstance(t.get('legacy'), dict) else {}

    tweet_id = str(
        t.get('tweet_id')
        or t.get('id')
        or t.get('id_str')
        or t.get('rest_id')
        or legacy.get('id_str')
        or legacy.get('id')
        or ''
    )
    text = (
        t.get('text')
        or t.get('full_text')
        or t.get('display_text')
        or legacy.get('full_text')
        or legacy.get('text')
        or ''
    )
    full_text = (
        t.get('display_text')
        or t.get('text')
        or t.get('full_text')
        or legacy.get('full_text')
        or legacy.get('text')
        or ''
    )

    # Article 全文提取：当 text 只有短链时，从 article 字段取正文
    is_article_link = 'x.com/i/article' in text or 't.co/' in text
    if (is_article_link or len(text) < 30) and isinstance(t.get('article'), dict):
        article_full = t['article'].get('full_text') or t['article'].get('text') or ''
        if article_full and len(article_full) > len(text):
            full_text = article_full
            text = article_full
            print(f'[twitter/tikhub] Article 全文: {len(article_full)} 字')

    # 作者：先看顶层 author，再 fallback 到 core.user_results.result.legacy（timeline 包装里的路径）
    author_info = t.get('author') or {}
    author = ''
    username = ''
    if isinstance(author_info, dict):
        author = author_info.get('name', '') or ''
        username = author_info.get('screen_name', '') or ''
    elif author_info:
        author = str(author_info)

    if not username:
        core = t.get('core') or {}
        if isinstance(core, dict):
            user_results = core.get('user_results') or {}
            if isinstance(user_results, dict):
                user_result = user_results.get('result') or {}
                if isinstance(user_result, dict):
                    user_legacy = user_result.get('legacy') or user_result
                    if isinstance(user_legacy, dict):
                        username = username or user_legacy.get('screen_name', '') or ''
                        author = author or user_legacy.get('name', '') or ''

    favorites = int(
        t.get('likes', 0)
        or t.get('favorite_count', 0)
        or legacy.get('favorite_count', 0)
        or 0
    )
    bookmarks = int(
        t.get('bookmarks', 0)
        or t.get('bookmark_count', 0)
        or legacy.get('bookmark_count', 0)
        or 0
    )
    retweets = int(
        t.get('retweets', 0)
        or t.get('retweet_count', 0)
        or legacy.get('retweet_count', 0)
        or 0
    )
    replies = int(
        t.get('replies', 0)
        or t.get('reply_count', 0)
        or legacy.get('reply_count', 0)
        or 0
    )
    views = int(t.get('views', 0) or 0)
    if not views:
        views_obj = t.get('views') if isinstance(t.get('views'), dict) else None
        if views_obj:
            views = int(views_obj.get('count', 0) or 0)
    created_at = t.get('created_at') or legacy.get('created_at') or ''

    media = _extract_media(t)
    result = {
        'platform': 'twitter',
        'mode': 'tweet',
        'tweet_id': tweet_id,
        'author': author,
        'username': username,
        'text': text,
        'full_text': full_text,
        'created_at': created_at,
        'favorites': favorites,
        'bookmarks': bookmarks,
        'views': views,
        'retweets': retweets,
        'replies': replies,
        'url': f'https://x.com/{username}/status/{tweet_id}' if username and tweet_id else (f'https://x.com/i/status/{tweet_id}' if tweet_id else ''),
        '_source': 'tikhub',
    }
    return _attach_media_and_transcript(result, media, tweet_id)


def _fetch_tweet_tikhub(tweet_id):
    """TikHub fallback: 抓取单条推文"""
    try:
        resp = tikhub_fetch(
            '/api/v1/twitter/web/fetch_tweet_detail',
            params={'tweet_id': tweet_id}
        )
    except Exception as e:
        print(f'[twitter/tikhub] API 调用失败: {e}')
        return None

    tweet_data = resp.get('data', resp)
    result = _parse_tweet_flat(tweet_data)
    if result:
        if not result['tweet_id']:
            result['tweet_id'] = tweet_id
        if not result['url']:
            result['url'] = f'https://x.com/i/status/{tweet_id}'

        # Article fallback
        txt = result.get('text', '')
        is_short = len(txt) < 30 or 'x.com/i/article' in txt or 't.co/' in txt
        if is_short:
            article = None
            if isinstance(resp.get('data'), dict):
                article = resp['data'].get('article')
            if not article and isinstance(tweet_data, dict):
                article = tweet_data.get('article')
            if isinstance(article, dict):
                article_full = article.get('full_text') or article.get('text') or ''
                if article_full and len(article_full) > len(txt):
                    result['text'] = article_full
                    result['full_text'] = article_full
                    print(f'[twitter/tikhub] Article fallback 全文: {len(article_full)} 字')

    return result


# ---------------------------------------------------------------------------
# 公开接口：fetch_tweet（主入口，FxTwitter → TikHub fallback）
# ---------------------------------------------------------------------------

def fetch_tweet(tweet_id, username=None):
    """
    抓取单条推文。
    主路径 1: Nitter（本地，免费）
    主路径 2: FxTwitter（免费）
    fallback: TikHub（付费）
    """
    # 主路径 1: Nitter（需要 username）
    if username and os.path.exists(XTF_NITTER):
        nitter_result = _fetch_tweet_nitter(tweet_id, username=username)
        if nitter_result and nitter_result.get('text') and len(nitter_result['text']) > 5:
            print(f'[twitter] Nitter 命中 ✓')
            return nitter_result

    # 主路径 2: FxTwitter
    result = _fetch_tweet_fxtwitter(tweet_id, username=username)

    if result and result.get('text') and len(result['text']) > 5:
        return result

    # Fallback: TikHub
    print(f'[twitter] FxTwitter 失败或内容为空，切换到 TikHub fallback...')
    tikhub_result = _fetch_tweet_tikhub(tweet_id)

    if tikhub_result:
        return tikhub_result

    # 全部失败
    return {
        'platform': 'twitter',
        'mode': 'tweet',
        'tweet_id': tweet_id,
        'error': 'Nitter / FxTwitter / TikHub 均抓取失败',
        '_source': 'none',
    }


# ---------------------------------------------------------------------------
# 评论区：x-tweet-fetcher + Camofox
# ---------------------------------------------------------------------------

def fetch_replies(tweet_url, camofox_port=9377):
    """
    抓取评论区。
    主路径: Nitter（本地，免费）
    fallback: x-tweet-fetcher + Camofox
    """
    import re
    m = re.search(r'(?:x\.com|twitter\.com)/([^/]+)/status/(\d+)', tweet_url)
    if not m:
        return {'platform': 'twitter', 'mode': 'replies', 'url': tweet_url, 'error': '无法解析 URL'}

    username = m.group(1)
    tweet_id = m.group(2)

    # 主路径: Nitter
    if os.path.exists(XTF_NITTER):
        try:
            proc = subprocess.run(
                [sys.executable, XTF_NITTER,
                 '--tweet', f'{username}/{tweet_id}',
                 '--nitter-url', NITTER_URL],
                capture_output=True, text=True, timeout=20,
                env={**os.environ, 'NITTER_URL': NITTER_URL},
            )
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                replies_list = data.get('replies_list', [])
                if replies_list:
                    print(f'[twitter/nitter] 评论区: {len(replies_list)} 条')
                    return {
                        'platform': 'twitter',
                        'mode': 'replies',
                        'url': tweet_url,
                        'tweet_id': tweet_id,
                        'username': username,
                        'replies': [
                            {
                                'username': r.get('username', ''),
                                'text': r.get('text', ''),
                                'likes': r.get('likes', 0),
                                'time': r.get('time', ''),
                            }
                            for r in replies_list
                        ],
                        'reply_count': len(replies_list),
                        '_source': 'nitter',
                    }
        except Exception as e:
            print(f'[twitter/nitter] 评论区失败: {e}')

    # Fallback: x-tweet-fetcher + Camofox（原逻辑）
    if not os.path.exists(XTF_SCRIPT):
        return {
            'platform': 'twitter',
            'mode': 'replies',
            'url': tweet_url,
            'error': f'x-tweet-fetcher 未找到: {XTF_SCRIPT}，请先 git clone',
        }

    try:
        proc = subprocess.run(
            [sys.executable, XTF_SCRIPT, '--url', tweet_url, '--replies',
             '--port', str(camofox_port)],
            capture_output=True, text=True, timeout=60
        )
        if proc.returncode == 2:
            # exit code 2 = error
            return {
                'platform': 'twitter',
                'mode': 'replies',
                'url': tweet_url,
                'error': proc.stderr.strip() or '抓取失败',
            }

        data = json.loads(proc.stdout)

        # 标准化输出
        return {
            'platform': 'twitter',
            'mode': 'replies',
            'url': tweet_url,
            'tweet_id': data.get('tweet_id', ''),
            'username': data.get('username', ''),
            'replies': data.get('replies', []),
            'reply_count': data.get('reply_count', 0),
            'warning': data.get('warning', ''),
            '_source': 'x-tweet-fetcher+camofox',
        }

    except subprocess.TimeoutExpired:
        return {'platform': 'twitter', 'mode': 'replies', 'url': tweet_url, 'error': '抓取超时（60s）'}
    except json.JSONDecodeError as e:
        return {'platform': 'twitter', 'mode': 'replies', 'url': tweet_url, 'error': f'JSON 解析失败: {e}'}
    except Exception as e:
        return {'platform': 'twitter', 'mode': 'replies', 'url': tweet_url, 'error': str(e)}


# ---------------------------------------------------------------------------
# 用户时间线：x-tweet-fetcher + Camofox（替代 TikHub 付费接口）
# ---------------------------------------------------------------------------

def fetch_timeline_xtf(username, limit=50, camofox_port=9377):
    """
    用 Nitter 或 x-tweet-fetcher 抓取用户时间线（免费，替代 TikHub 付费接口）。
    主路径: Nitter
    fallback: x-tweet-fetcher + Camofox
    """
    # 主路径: Nitter
    if os.path.exists(XTF_NITTER):
        try:
            proc = subprocess.run(
                [sys.executable, XTF_NITTER,
                 '--timeline', username,
                 '--count', str(limit),
                 '--nitter-url', NITTER_URL],
                capture_output=True, text=True, timeout=30,
                env={**os.environ, 'NITTER_URL': NITTER_URL},
            )
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                tweets_raw = data.get('tweets', [])
                if tweets_raw:
                    print(f'[twitter/nitter] timeline: {len(tweets_raw)} 条')
                    tweets = []
                    for tw in tweets_raw:
                        uname = tw.get('username', username).lstrip('@')
                        tweets.append({
                            'platform': 'twitter',
                            'mode': 'tweet',
                            'tweet_id': tw.get('tweet_id', ''),
                            'author': tw.get('display_name', uname),
                            'username': uname,
                            'text': tw.get('text', ''),
                            'full_text': tw.get('text', ''),
                            'created_at': tw.get('time', ''),
                            'favorites': int(tw.get('likes', 0) or 0),
                            'bookmarks': 0,
                            'views': int(tw.get('views', 0) or 0),
                            'retweets': int(tw.get('retweets', 0) or 0),
                            'replies': int(tw.get('replies', 0) or 0),
                            'url': f'https://x.com/{uname}/status/{tw.get("tweet_id", "")}',
                            '_source': 'nitter',
                        })
                    return {
                        'platform': 'twitter',
                        'mode': 'timeline_xtf',
                        'username': username,
                        'tweets': tweets,
                        'count': len(tweets),
                        '_source': 'nitter',
                    }
        except Exception as e:
            print(f'[twitter/nitter] timeline 失败: {e}')

    # Fallback: x-tweet-fetcher + Camofox（原逻辑）
    if not os.path.exists(XTF_SCRIPT):
        return {
            'platform': 'twitter',
            'mode': 'timeline_xtf',
            'username': username,
            'error': f'x-tweet-fetcher 未找到: {XTF_SCRIPT}',
        }

    try:
        proc = subprocess.run(
            [sys.executable, XTF_SCRIPT, '--user', username,
             '--limit', str(limit), '--port', str(camofox_port)],
            capture_output=True, text=True, timeout=120
        )
        if proc.returncode == 2:
            return {
                'platform': 'twitter',
                'mode': 'timeline_xtf',
                'username': username,
                'error': proc.stderr.strip() or '抓取失败',
            }

        data = json.loads(proc.stdout)

        # 转换为与 fetch_profile 兼容的格式
        tweets_raw = data.get('tweets', [])
        tweets = []
        for tw in tweets_raw:
            author_str = tw.get('author', '')
            tweets.append({
                'platform': 'twitter',
                'mode': 'tweet',
                'tweet_id': tw.get('tweet_id', ''),
                'author': tw.get('author_name', ''),
                'username': author_str.lstrip('@'),
                'text': tw.get('text', ''),
                'full_text': tw.get('text', ''),
                'created_at': tw.get('time_ago', ''),
                'favorites': tw.get('likes', 0),
                'bookmarks': 0,
                'views': tw.get('views', 0),
                'retweets': tw.get('retweets', 0),
                'replies': tw.get('replies', 0),
                'url': f'https://x.com/{author_str.lstrip("@")}/status/{tw.get("tweet_id", "")}',
                '_source': 'x-tweet-fetcher+camofox',
            })

        return {
            'platform': 'twitter',
            'mode': 'timeline_xtf',
            'username': username,
            'tweets': tweets,
            'count': len(tweets),
            '_source': 'x-tweet-fetcher+camofox',
        }

    except subprocess.TimeoutExpired:
        return {'platform': 'twitter', 'mode': 'timeline_xtf', 'username': username, 'error': '抓取超时（120s）'}
    except json.JSONDecodeError as e:
        return {'platform': 'twitter', 'mode': 'timeline_xtf', 'username': username, 'error': f'JSON 解析失败: {e}'}
    except Exception as e:
        return {'platform': 'twitter', 'mode': 'timeline_xtf', 'username': username, 'error': str(e)}


# ---------------------------------------------------------------------------
# 用户主页：TikHub（用于详细 profile 数据 + 不需要 Camofox 场景）
# ---------------------------------------------------------------------------

def fetch_profile(username, max_pages=15, count_per_page=20):
    """
    抓取用户主页帖子（TikHub，适合不需要 Camofox 的场景）
    如果 Camofox 可用，建议改用 fetch_timeline_xtf（免费）
    """
    # 先获取用户 profile
    profile_data = {'name': username, 'bio': '', 'followers': 0}
    try:
        resp = tikhub_fetch(
            '/api/v1/twitter/web/fetch_user_profile',
            params={'screen_name': username}
        )
        pd = resp.get('data', resp)
        if 'user' in pd:
            user_result = pd['user'].get('result', pd['user'])
            user_legacy = user_result.get('legacy', user_result)
        else:
            user_legacy = pd

        profile_data = {
            'name': user_legacy.get('name', '') or pd.get('name', username),
            'bio': user_legacy.get('description', '') or pd.get('description', ''),
            'followers': int(user_legacy.get('followers_count', 0) or pd.get('followers_count', 0) or 0),
        }
    except Exception as e:
        print(f'[twitter/tikhub] profile 获取失败: {e}')

    all_tweets = []
    cursor = None

    for page in range(max_pages):
        params = {'screen_name': username, 'count': count_per_page}
        if cursor:
            params['cursor'] = cursor

        try:
            resp = tikhub_fetch(
                '/api/v1/twitter/web/fetch_user_post_tweet',
                params=params
            )
        except Exception as e:
            print(f'[twitter/tikhub] 第 {page + 1} 页抓取失败: {e}')
            break

        page_data = resp.get('data', resp)
        timeline = page_data.get('timeline', [])
        if not timeline:
            timeline = page_data.get('tweets', []) or page_data.get('entries', [])

        pinned = page_data.get('pinned', [])
        if pinned and page == 0:
            if not isinstance(pinned, list):
                pinned = [pinned]
            for p in pinned:
                parsed = _parse_tweet_flat(p)
                if parsed:
                    parsed['pinned'] = True
                    all_tweets.append(parsed)

        if not timeline:
            break

        for item in timeline:
            parsed = _parse_tweet_flat(item)
            if not parsed:
                continue
            if parsed['text'].startswith('RT @'):
                continue
            if not parsed['username']:
                parsed['username'] = username
            all_tweets.append(parsed)

        next_cursor = page_data.get('next_cursor') or page_data.get('cursor')
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

    return {
        'platform': 'twitter',
        'mode': 'profile',
        'username': username,
        'name': profile_data.get('name', username),
        'bio': profile_data.get('bio', ''),
        'followers': profile_data.get('followers', 0),
        'tweets': all_tweets,
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python3 twitter.py <tweet_id|url|username> [mode]')
        print('  mode: tweet / profile / replies / timeline_xtf')
        sys.exit(1)

    arg = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else None

    if mode == 'replies':
        result = fetch_replies(arg)
    elif mode == 'timeline_xtf':
        result = fetch_timeline_xtf(arg)
    elif mode == 'profile' or (not arg.isdigit() and 'x.com' not in arg and 'twitter.com' not in arg):
        result = fetch_profile(arg)
    else:
        # 从 URL 或纯 ID 抓单条推文
        tweet_id = arg
        uname = None
        if 'x.com' in arg or 'twitter.com' in arg:
            import re
            m = re.search(r'(?:x\.com|twitter\.com)/([^/]+)/status/(\d+)', arg)
            if m:
                uname = m.group(1)
                tweet_id = m.group(2)
        result = fetch_tweet(tweet_id, username=uname)

    print(json.dumps(result, ensure_ascii=False, indent=2)[:5000])
