#!/usr/bin/env python3
"""
Wiki writeback — 把学习助手的 ingest 事件回填到一个可选的外部知识库日志文件
（例如 Obsidian vault 里的 log.md），本仓库不含该知识库实现。

设计原则：
- 静默失败 — 未配置 LEARNING_ASSISTANT_WIKI_LOG_PATH，或目标文件不存在/写失败时不报错，
  不影响学习助手主流程
- 格式对齐 grep-friendly 约定：`## [YYYY-MM-DD HH:MM] {type} | {平台/作者} | {标题}`
- 相对路径展示（避免 log 里全是绝对长路径）
"""

import os
from datetime import datetime

WIKI_LOG_PATH = os.environ.get('LEARNING_ASSISTANT_WIKI_LOG_PATH', '')


def _trim_url(url, max_len=100):
    """URL 太长时截断显示。"""
    if len(url) <= max_len:
        return url
    return url[:max_len - 3] + '...'


def _humanize_filepath(filepath):
    """绝对路径转 `~/...` 形式。"""
    if not filepath:
        return '-'
    home = os.path.expanduser('~')
    if filepath.startswith(home):
        return '~' + filepath[len(home):]
    return filepath


def _sanitize_title(title, max_len=60):
    """标题去换行，限长。"""
    if not title:
        return '无标题'
    return str(title).strip().replace('\n', ' ').replace('\r', ' ')[:max_len]


def append_log_entry(platform, author, title, url, score, filepath, action='ingest'):
    """
    向 workspace-wiki/knowledge/log.md 追加一条 ingest 记录。

    Args:
        platform: 'twitter' / 'github' / 'youtube' / ...
        author: 作者 (URL 回退后的最终值)
        title: 帖子/文章标题
        url: 原始 URL
        score: 0-20 的总分（None 表示未评分）
        filepath: 入库后的绝对路径
        action: 'ingest' | 'reingest' | 'lint' | 'meta'

    Returns:
        bool — True 写成功，False 静默失败
    """
    if not os.path.isfile(WIKI_LOG_PATH):
        return False

    ts = datetime.now().strftime('%Y-%m-%d %H:%M')
    title_clean = _sanitize_title(title)
    author_clean = author or 'unknown'
    score_str = f'{score}/20' if score is not None else '?/20'
    filepath_display = _humanize_filepath(filepath)

    entry = (
        f'\n## [{ts}] {action} | {platform}/{author_clean} | {title_clean}\n\n'
        f'- 源：{_trim_url(url)}\n'
        f'- 评分：{score_str}\n'
        f'- 文件：`{filepath_display}`\n'
        f'- touched: wade-learning-assistant only（workspace-wiki Raw 层未同步）\n'
        f'- 写入者：`wade-learning-assistant/main.py` → `wiki_writeback.append_log_entry`\n'
    )

    try:
        with open(WIKI_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(entry)
        return True
    except OSError:
        return False


if __name__ == '__main__':
    # 手动测试
    import sys
    ok = append_log_entry(
        platform='test',
        author='testuser',
        title='手动测试条目，应当出现在 log.md 末尾',
        url='https://example.com/test',
        score=15,
        filepath='/tmp/test.md',
        action='meta',
    )
    print('OK' if ok else 'FAILED')
    sys.exit(0 if ok else 1)
