#!/usr/bin/env python3
"""
知乎抓取器
- 回答/问题：TikHub zhihu_web.fetch_question_answers 主路径
- 专栏：TikHub zhihu_web.fetch_column_article_detail
- 输出学习助手统一结构，保留 HTML、可读正文、图片 URL、统计字段
"""

import html
import json
import re
from html.parser import HTMLParser

from fetchers.base import fetch as tikhub_fetch


QUESTION_ANSWERS_PATH = '/api/v1/zhihu/web/fetch_question_answers'
ARTICLE_DETAIL_PATH = '/api/v1/zhihu/web/fetch_column_article_detail'


class _ReadableHTMLParser(HTMLParser):
    BLOCK_TAGS = set(['p', 'div', 'section', 'article', 'blockquote', 'br'])
    LIST_TAGS = set(['li'])
    HEADING_TAGS = set(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK_TAGS or tag in self.HEADING_TAGS:
            self.parts.append('\n')
        elif tag in self.LIST_TAGS:
            self.parts.append('\n- ')

    def handle_endtag(self, tag):
        if tag in self.BLOCK_TAGS or tag in self.LIST_TAGS or tag in self.HEADING_TAGS:
            self.parts.append('\n')

    def handle_data(self, data):
        if data:
            self.parts.append(data)

    def text(self):
        text = html.unescape(''.join(self.parts))
        lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in text.splitlines()]
        text = '\n'.join(line for line in lines if line)
        return re.sub(r'\n{3,}', '\n\n', text).strip()


def _html_to_text(content_html):
    if not content_html:
        return ''
    parser = _ReadableHTMLParser()
    parser.feed(str(content_html))
    parser.close()
    return parser.text()


def _extract_images(content_html, extra=None):
    images = []

    def add(value):
        if isinstance(value, str) and value.startswith(('http://', 'https://')) and value not in images:
            images.append(value)

    if content_html:
        for img in re.finditer(r'<img\b[^>]*>', str(content_html), flags=re.I):
            tag = img.group(0)
            for attr in ('src', 'data-src', 'data-original', 'data-actualsrc'):
                m = re.search(r'%s=["\']([^"\']+)["\']' % attr, tag, flags=re.I)
                if m:
                    add(html.unescape(m.group(1)))

    if isinstance(extra, dict):
        for key in ('image_url', 'thumbnail', 'title_image', 'cover', 'cover_url'):
            add(extra.get(key))

    return images


def _to_int(value):
    if value is None or value == '':
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _author_name(author):
    if isinstance(author, dict):
        return str(author.get('name') or author.get('nickname') or author.get('url_token') or '')
    return str(author or '')


def _author_info(author):
    if not isinstance(author, dict):
        return {'name': _author_name(author)} if author else {}
    return {
        'name': author.get('name') or author.get('nickname') or '',
        'url_token': author.get('url_token') or author.get('id') or '',
        'headline': author.get('headline') or '',
        'avatar_url': author.get('avatar_url') or author.get('avatar_url_template') or '',
    }


def _extract_answer_items(raw):
    containers = []
    if isinstance(raw, dict):
        containers.append(raw)
        if isinstance(raw.get('data'), (dict, list)):
            containers.append(raw['data'])

    for container in containers:
        if isinstance(container, list):
            return container
        if isinstance(container, dict):
            for key in ('data', 'answers', 'items', 'list', 'results'):
                value = container.get(key)
                if isinstance(value, list):
                    return value
    return []


def _question_title(item, target):
    question = None
    if isinstance(target, dict):
        question = target.get('question')
    if question is None and isinstance(item, dict):
        question = item.get('question')

    if isinstance(question, dict):
        return str(question.get('title') or question.get('name') or '')
    if isinstance(question, str):
        return question
    if isinstance(target, dict):
        return str(target.get('question_title') or target.get('title') or '')
    return ''


def _normalize_answer(item, question_id, source_url=None):
    if not isinstance(item, dict):
        return None

    target = item.get('target') if isinstance(item.get('target'), dict) else item
    if not isinstance(target, dict):
        return None

    answer_id = str(target.get('id') or target.get('answer_id') or item.get('id') or item.get('answer_id') or '')
    if not answer_id:
        return None

    content_html = str(target.get('content') or target.get('content_html') or target.get('body') or '')
    content = _html_to_text(content_html) or str(
        target.get('excerpt') or target.get('excerpt_new') or target.get('text') or ''
    ).strip()

    stats = {}
    for key in (
        'voteup_count',
        'comment_count',
        'thanks_count',
        'created_time',
        'updated_time',
        'created_at',
        'updated_at',
    ):
        if key in target:
            stats[key] = _to_int(target.get(key))

    author = target.get('author') or item.get('author') or {}
    canonical = 'https://www.zhihu.com/question/%s/answer/%s' % (question_id, answer_id)

    return {
        'platform': 'zhihu',
        'type': 'answer',
        'url': source_url or canonical,
        'canonical_url': canonical,
        'question_id': str(question_id),
        'answer_id': answer_id,
        'title': _question_title(item, target),
        'author': _author_name(author),
        'author_info': _author_info(author),
        'content_html': content_html,
        'content': content,
        'images': _extract_images(content_html, target),
        'stats': stats,
        'source': 'tikhub',
    }


def _fetch_question_answers(question_id, limit=20, order='default'):
    params = {
        'question_id': str(question_id),
        'limit': int(limit or 20),
        'order': order or 'default',
    }
    raw = tikhub_fetch(QUESTION_ANSWERS_PATH, params=params)
    if isinstance(raw, dict) and raw.get('code') not in (None, 200):
        raise RuntimeError(raw.get('message') or raw.get('error') or 'TikHub 问题回答接口返回非 200')
    answers = []
    for item in _extract_answer_items(raw):
        normalized = _normalize_answer(item, str(question_id))
        if normalized:
            answers.append(normalized)
    return raw, answers


def fetch_answer(url, question_id, answer_id, limit=20):
    """抓取单个知乎回答；必须在 answers 中精确匹配 answer_id。"""
    _, answers = _fetch_question_answers(question_id, limit=limit, order='default')
    target_id = str(answer_id)
    for answer in answers:
        if answer.get('answer_id') == target_id:
            answer['url'] = url
            return answer

    return {
        'platform': 'zhihu',
        'type': 'answer',
        'url': url,
        'question_id': str(question_id),
        'answer_id': target_id,
        'title': '',
        'author': '',
        'content_html': '',
        'content': '',
        'images': [],
        'stats': {},
        'error': '未在 question_id=%s 的 answers 中精确匹配 answer_id=%s' % (question_id, answer_id),
    }


def fetch_question(url, question_id, limit=5, order='default'):
    """抓取问题页 Top N 回答，并拼接成单条可分析内容。"""
    _, answers = _fetch_question_answers(question_id, limit=limit, order=order)
    title = next((a.get('title') for a in answers if a.get('title')), '知乎问题 %s' % question_id)

    sections = []
    images = []
    for idx, answer in enumerate(answers, start=1):
        author = answer.get('author') or '匿名用户'
        content = answer.get('content') or ''
        if content:
            sections.append('## 回答 %d：%s\n\n%s' % (idx, author, content))
        for image in answer.get('images') or []:
            if image not in images:
                images.append(image)

    content = '# %s\n\n%s' % (title, '\n\n'.join(sections)) if sections else title
    return {
        'platform': 'zhihu',
        'type': 'question',
        'url': url,
        'question_id': str(question_id),
        'answer_id': '',
        'title': title,
        'author': '知乎',
        'content_html': '',
        'content': content,
        'images': images,
        'stats': {'answer_count': len(answers)},
        'answers': answers,
        'source': 'tikhub',
    }


def _unwrap_article_payload(raw):
    candidates = []
    if isinstance(raw, dict):
        candidates.append(raw)
        data = raw.get('data')
        if isinstance(data, dict):
            candidates.append(data)
            nested = data.get('data')
            if isinstance(nested, dict):
                candidates.append(nested)
            elif isinstance(nested, list):
                candidates.extend([x for x in nested if isinstance(x, dict)])
        elif isinstance(data, list):
            candidates.extend([x for x in data if isinstance(x, dict)])

    for candidate in candidates:
        if candidate.get('title') or candidate.get('content') or candidate.get('content_html'):
            return candidate
    return candidates[-1] if candidates else {}


def fetch_article(url, article_id):
    """抓取知乎专栏文章。"""
    raw = tikhub_fetch(ARTICLE_DETAIL_PATH, params={'article_id': str(article_id)})
    if isinstance(raw, dict) and raw.get('code') not in (None, 200):
        return {
            'platform': 'zhihu',
            'type': 'article',
            'url': url,
            'article_id': str(article_id),
            'question_id': '',
            'answer_id': '',
            'title': '',
            'author': '',
            'content_html': '',
            'content': '',
            'images': [],
            'stats': {},
            'error': raw.get('message') or raw.get('error') or 'TikHub 专栏接口返回非 200',
        }

    article = _unwrap_article_payload(raw)
    content_html = str(article.get('content') or article.get('content_html') or article.get('body') or '')
    content = _html_to_text(content_html) or str(article.get('text') or article.get('excerpt') or '').strip()
    author = article.get('author') or article.get('author_info') or article.get('user') or article.get('creator') or {}

    stats = {}
    for key in (
        'voteup_count',
        'comment_count',
        'thanks_count',
        'like_count',
        'created',
        'updated',
        'created_time',
        'updated_time',
    ):
        if key in article:
            stats[key] = _to_int(article.get(key))

    return {
        'platform': 'zhihu',
        'type': 'article',
        'url': url,
        'canonical_url': 'https://zhuanlan.zhihu.com/p/%s' % article_id,
        'article_id': str(article_id),
        'question_id': '',
        'answer_id': '',
        'title': str(article.get('title') or article.get('name') or '知乎专栏 %s' % article_id),
        'author': _author_name(author),
        'author_info': _author_info(author),
        'content_html': content_html,
        'content': content,
        'images': _extract_images(content_html, article),
        'stats': stats,
        'source': 'tikhub',
    }


if __name__ == '__main__':
    import sys
    test_url = sys.argv[1] if len(sys.argv) > 1 else ''
    if '/answer/' in test_url:
        m = re.search(r'zhihu\.com/question/(\d+)/answer/(\d+)', test_url)
        result = fetch_answer(test_url, m.group(1), m.group(2)) if m else {'error': 'bad answer url'}
    elif 'zhuanlan.zhihu.com/p/' in test_url:
        m = re.search(r'zhuanlan\.zhihu\.com/p/(\d+)', test_url)
        result = fetch_article(test_url, m.group(1)) if m else {'error': 'bad article url'}
    else:
        m = re.search(r'zhihu\.com/question/(\d+)', test_url)
        result = fetch_question(test_url, m.group(1)) if m else {'error': 'bad question url'}
    print(json.dumps(result, ensure_ascii=False, indent=2)[:4000])
