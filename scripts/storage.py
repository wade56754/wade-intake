"""
Wade 学习助手 — 存储模块
负责文章入库判断和文件写入
"""

import os
import re
import shutil
from datetime import datetime


# 知识库根目录（相对于 scripts/ 的上一级）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
KNOWLEDGE_DIR = os.path.join(SKILL_DIR, 'knowledge', 'articles')


def _should_store(data):
    """
    判断是否入库
    优先读 analyzer.py 写入的 _action（评分驱动）
    回退到旧规则（兼容直接调用 storage 的场景）
    返回 (bool, reason)
    """
    # 优先使用评分驱动的决策
    action = data.get('_action')
    score = data.get('_score')
    if action is not None:
        if action == 'store':
            return True, f'评分 {score}/20 — 高价值入库'
        elif action == 'store_low':
            return True, f'评分 {score}/20 — 存摘要'
        else:
            return False, f'评分 {score}/20 — 低价值不入库'

    # 回退：旧规则（无评分时）
    platform = data.get('platform', '')
    mode = data.get('mode', '')

    if data.get('error'):
        return False, '抓取失败，不入库'

    if platform == 'twitter':
        if mode == 'profile':
            return True, 'profile 模式，全量入库'
        favorites = data.get('favorites', 0)
        bookmarks = data.get('bookmarks', 0)
        if favorites + bookmarks > 200:
            return True, f'互动数据优秀（❤️ {favorites} 🔖 {bookmarks}）'
        # 内容长度判断
        text = data.get('full_text', '') or data.get('text', '')
        if len(text) > 200:
            return True, '长推文，内容丰富'
        return False, f'互动数据不足（❤️ {favorites} 🔖 {bookmarks}）'

    elif platform == 'xiaohongshu':
        likes = data.get('likes', 0)
        if likes > 500:
            return True, f'高赞笔记（❤️ {likes}）'
        return False, f'点赞不足 500（❤️ {likes}）'

    elif platform in ('youtube', 'douyin', 'tiktok'):
        transcript = data.get('transcript', '') or data.get('subtitle', '')
        if transcript:
            return True, '有字幕内容，入库保存'
        title = data.get('title', '')
        if len(title) > 20:
            return True, '有标题描述，入库保存'
        return False, '无字幕和有效内容'

    elif platform == 'wechat_mp':
        content = data.get('content', '')
        if len(content) > 500:
            return True, f'公众号文章（{len(content)} 字）'
        return False, '内容过短'

    else:
        # 通用网页
        content = data.get('content', '')
        if len(content) > 1000:
            return True, f'有效内容 {len(content)} 字'
        return False, f'内容不足 1000 字（{len(content)} 字）'


def _find_existing_by_url(platform_dir, url):
    """
    扫 platform 目录里所有 .md 文件的 frontmatter，返回第一个 `url:` 匹配的文件路径。
    找不到返回 None。

    - 只读文件前 2KB（frontmatter 够了），避免 IO 浪费
    - 忽略 `.duplicates/` 子目录（归档区）
    - 跳过无法解码的文件
    """
    if not url or not os.path.isdir(platform_dir):
        return None
    target = url.strip()
    for fn in os.listdir(platform_dir):
        if not fn.endswith('.md') or fn.startswith('.'):
            continue
        path = os.path.join(platform_dir, fn)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                head = f.read(2048)
        except (OSError, UnicodeDecodeError):
            continue
        # 只在 frontmatter 块内找 url:，避免正文里的 URL 误匹配
        if not head.lstrip().startswith('---'):
            continue
        # 截取到第二个 `---` 之前
        fm_end = head.find('\n---', 3)
        fm_block = head[:fm_end] if fm_end != -1 else head
        for line in fm_block.splitlines():
            line = line.strip()
            if line.startswith('url:'):
                existing = line.split(':', 1)[1].strip()
                if existing == target:
                    return path
                break  # frontmatter 里 url 只会有一行
    return None


def _archive_duplicate(existing_path):
    """
    把重复文件移到同目录下的 `.duplicates/{filename}.{timestamp}.bak` 归档。
    返回归档后路径；失败时返回 None。
    """
    platform_dir = os.path.dirname(existing_path)
    dup_dir = os.path.join(platform_dir, '.duplicates')
    try:
        os.makedirs(dup_dir, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d-%H%M%S')
        archived = os.path.join(dup_dir, f'{os.path.basename(existing_path)}.{ts}.bak')
        shutil.move(existing_path, archived)
        return archived
    except OSError:
        return None


def _sanitize_filename(text):
    """清理文件名"""
    # 去除特殊字符
    text = re.sub(r'[\\/:*?"<>|\n\r\t]', '', text)
    # 去除 emoji
    text = re.sub(r'[^\w\s\-\u4e00-\u9fff]', '', text)
    text = text.strip()
    # 截断
    if len(text) > 40:
        text = text[:40]
    return text or 'untitled'


def store_article(data, analysis_text):
    """
    入库 — 写 Raw .md 文件到 knowledge/articles/{platform}/，并写 analyzer sidecar
    返回 Raw md 文件路径

    去重规则（2026-04-07 修）：
    - 先按 frontmatter `url:` 查现有文件
    - 命中 → 旧文件归档到 `.duplicates/`，再写新版本（覆盖语义）
    - 未命中 → 正常写入。若目标文件名恰好已存在（不同 URL 撞同 filename），加时间戳避免

    三层架构（2026-04-07 Bug 2 修复）：
    - Raw md 正文只含 frontmatter + 原文，绝不写入 analyzer 的打分/要点/素材
    - analyzer 产出写到 sibling `.analysis.md` sidecar（workspace-wiki schema.md §1）
    """
    from analyzer import _get_author, _get_title, _get_content, write_analysis_sidecar

    platform = data.get('platform', 'web')
    author = _get_author(data) or 'unknown'
    title = _get_title(data)
    ingest_date = datetime.now().strftime('%Y-%m-%d')
    # 发布日期优先取 analyzer 解析结果（fetcher_date 或 LLM 推断），否则回退到 ingest 日期
    source_date = data.get('_source_date') or ingest_date
    url = data.get('url', '')

    # 构建目录
    platform_dir = os.path.join(KNOWLEDGE_DIR, platform)
    os.makedirs(platform_dir, exist_ok=True)

    # 去重：按 URL 查旧文件，命中则归档
    existing_path = _find_existing_by_url(platform_dir, url)
    if existing_path:
        archived = _archive_duplicate(existing_path)
        if archived:
            print(f'[dedup] 同 URL 已存在，旧版归档到: {os.path.relpath(archived, KNOWLEDGE_DIR)}')
            # 同时归档旧的 sidecar（若存在）
            old_sidecar = os.path.splitext(existing_path)[0] + '.analysis.md'
            if os.path.exists(old_sidecar):
                try:
                    dup_dir = os.path.join(platform_dir, '.duplicates')
                    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
                    shutil.move(
                        old_sidecar,
                        os.path.join(dup_dir, f'{os.path.basename(old_sidecar)}.{ts}.bak'),
                    )
                except OSError:
                    pass
        else:
            print(f'[dedup] 同 URL 已存在但归档失败，直接覆盖: {existing_path}')
            try:
                os.remove(existing_path)
            except OSError:
                pass

    # 文件名
    safe_author = _sanitize_filename(author)
    safe_title = _sanitize_filename(title)
    filename = f'{ingest_date}-{safe_author}-{safe_title}.md'
    filepath = os.path.join(platform_dir, filename)

    # 兜底：不同 URL 撞同一 filename 的罕见情况，加时间戳
    if os.path.exists(filepath):
        filepath = filepath.replace('.md', f'-{datetime.now().strftime("%H%M%S")}.md')

    # 写入 Raw md — 只有 frontmatter + 原文，绝不含 analyzer 输出
    content = _get_content(data)
    score = data.get('_score')

    md_content = f'---\n'
    md_content += f'platform: {platform}\n'
    md_content += f'author: {author}\n'
    md_content += f'url: {url}\n'
    md_content += f'date: {source_date}\n'
    md_content += f'ingested_at: {ingest_date}\n'
    if score is not None:
        md_content += f'score: {score}\n'
    md_content += f'---\n\n'
    md_content += f'# {title}\n\n'
    md_content += f'## 原文\n\n{content}\n'

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(md_content)

    # 写 sidecar（analyzer 输出），Raw md 写入后立即关闭，不再打开
    try:
        sidecar_path = write_analysis_sidecar(filepath, analysis_text, data)
        if sidecar_path:
            print(f'[analysis] sidecar 已写: {os.path.relpath(sidecar_path, KNOWLEDGE_DIR)}')
    except Exception as e:
        print(f'[analysis] sidecar 写入失败（不影响入库）: {e}')

    return filepath
