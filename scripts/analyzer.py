"""
Wade 学习助手 — 分析模块
负责内容解析、要点提取、业务分析

v2.0: LLM 分析（deeprouter/haiku），heuristic fallback
v2.1 (2026-04-07):
  - Bug 1 修复：注入今天的日期作为锚点，防止 LLM 默认 2025 年
  - Bug 2 修复：analyzer 输出写入 sidecar `.analysis.md`，不再污染 Raw md 正文
"""

import json
import re
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import failure_logger

# ── LLM 配置 ──
# 默认指向一个本机 OpenAI-compatible 反代（如自建 codex-proxy / litellm）。
# 凭证只从环境变量或本地 .env 读取，不硬编码任何 key。
_LOCAL_ENV_CACHE = None


def _load_local_env():
    """Load known local env files into an in-memory dict without printing values."""
    global _LOCAL_ENV_CACHE
    if _LOCAL_ENV_CACHE is not None:
        return _LOCAL_ENV_CACHE

    paths = [
        os.path.join(os.path.dirname(SCRIPT_DIR), '.env'),
    ]
    values = {}
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and value and key not in values:
                        values[key] = value
        except OSError:
            continue

    _LOCAL_ENV_CACHE = values
    return values


def _env_value(*names):
    local_env = _load_local_env()
    for name in names:
        value = os.getenv(name) or local_env.get(name)
        if value:
            return value
    return ''


def _default_llm_api_url():
    explicit = os.getenv('LEARNING_ASSISTANT_LLM_API_URL')
    if explicit:
        return explicit
    base_url = _env_value('CLIPROXY_BASE_URL', 'OPENAI_BASE_URL')
    if base_url:
        return base_url.rstrip('/') + '/chat/completions'
    return 'http://127.0.0.1:8100/v1/chat/completions'


LLM_API_URL = _default_llm_api_url()
LLM_API_KEY = _env_value('LEARNING_ASSISTANT_LLM_API_KEY', 'CLIPROXY_API_KEY', 'OPENAI_API_KEY')
LLM_MODEL = os.getenv('LEARNING_ASSISTANT_LLM_MODEL', 'gpt-5.4-mini')
LLM_API_MODE = os.getenv('LEARNING_ASSISTANT_LLM_API_MODE', 'openai_chat')


def _today_str():
    """返回今天的绝对日期 YYYY-MM-DD（Asia/Shanghai 本地时间）。"""
    return datetime.now().strftime('%Y-%m-%d')


# 业务背景 & 两个「业务借鉴」透镜——按自己的领域改这三个环境变量即可，
# 不改的话会用下面这组示例默认值（原作者的业务背景，仅作示范）。
BUSINESS_CONTEXT = os.getenv(
    'LEARNING_ASSISTANT_BUSINESS_CONTEXT',
    '跨境电商创业者，运营多 Agent 自动化系统，关注 AI Agent 架构、内容创作、一人公司',
)
BIZ_LENS_1_LABEL = os.getenv('LEARNING_ASSISTANT_BIZ_LENS_1_LABEL', '跨境电商/TikTok Shop')
BIZ_LENS_2_LABEL = os.getenv('LEARNING_ASSISTANT_BIZ_LENS_2_LABEL', 'AI Agent 架构')


def _build_system_prompt(today):
    """构造带今日日期锚点的 system prompt。"""
    return f"""你是一个学习助手，负责分析内容并输出结构化笔记。

背景：{BUSINESS_CONTEXT}。

━━━ 时间锚点（务必遵守）━━━
今天的日期是 {today}。所有相对时间（"上周"/"昨天"/"March 8" 无年份 / "3月8日" 无年份）必须以此为锚点转为绝对日期。
- 若原文只给 "March 8" 且上下文未明示年份，默认取今年（{today[:4]}）。
- 若今年同月已过且距今 > 6 个月，取去年。
- 严禁默认 2024/2025 年——你的知识截止日期不是今天的日期。
- arsenal 里每条 source_label 的日期字段必须用 YYYY-MM-DD 或 YYYY-MM 格式，若原文日期无法确定，写 "{today[:7]}"（本月）而不是编造。

━━━ 输出格式 ━━━
你必须严格输出以下 JSON 格式（不要加 markdown 代码块）：
{{
  "date": "YYYY-MM-DD（原文发布日期的绝对形式；若无法确定，留空字符串 \\"\\"，禁止编造）",
  "summary": "2-3句概要，直接说价值，不要复述标题",
  "points": ["要点1（带具体数据/案例）", "要点2", "要点3", "要点4", "要点5"],
  "biz_ecom": "{BIZ_LENS_1_LABEL} 视角下可借鉴之处（无关写 null）",
  "biz_arch": "{BIZ_LENS_2_LABEL} 视角下可参考之处（无关写 null）",
  "scores": {{
    "info_density": 1-5,
    "relevance": 1-5,
    "scarcity": 1-5,
    "reusability": 1-5,
    "timeliness": 1-5
  }},
  "arsenal": [
    {{"type": "洞察|数据|故事|金句|案例|反常识", "content": "具体内容", "source_label": "作者《标题》YYYY-MM-DD"}}
  ]
}}

评分标准：
- 信息密度：1=废话 3=有数据但模糊 5=具体数据+可执行步骤
- 业务相关度：1=完全无关 3=有参考 5=直接命中跨境/Agent架构
- 稀缺性：1=烂大街 3=有独特视角 5=独家一手案例/数据
- 可复用性：1=一次性 3=偶尔参考 5=框架/模板可反复引用
- 时效性：1=超1年 3=3-12个月 5=3个月内（以 {today} 为参照点）

arsenal 只提取"拿出来就能用"的弹药，最多 5 条，宁缺毋滥。无可提取写空数组。

重要：保持简洁。summary 不超过 3 句，每个 point 不超过 30 字，每条 arsenal content 不超过 50 字。直接输出 JSON，不要任何前缀解释。"""


# 向后兼容：保留模块级别常量（以今天为默认锚点）
LLM_SYSTEM_PROMPT = _build_system_prompt(_today_str())


_ISO_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _coerce_iso_date(value):
    """把任意输入尝试转为 YYYY-MM-DD。返回 str 或 ''。"""
    if not value:
        return ''
    s = str(value).strip()
    if not s:
        return ''
    # 已经是 YYYY-MM-DD
    if _ISO_DATE_RE.match(s):
        return s
    # 尝试几种常见格式：Twitter/X API、ISO8601
    fmts = [
        '%a %b %d %H:%M:%S %z %Y',   # Twitter: "Mon Apr 07 10:25:14 +0000 2026"
        '%Y-%m-%dT%H:%M:%S.%fZ',
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%d %H:%M:%S',
        '%Y/%m/%d',
    ]
    for fmt in fmts:
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return ''


def _fetcher_date(data):
    """从 fetcher 的 data dict 中尝试提取已知的发布日期 (ISO)。找不到返回 ''。"""
    for key in ('date', 'created_at', 'published_at', 'publish_time', 'time'):
        iso = _coerce_iso_date(data.get(key))
        if iso:
            return iso
    return ''


def _llm_analyze(title, author, content, platform, url, fetcher_date=''):
    """调用 LLM 分析内容，返回结构化 dict 或 None（通过 curl 避免 Python SSL 问题）

    Args:
        fetcher_date: fetcher 已经获知的 ISO 发布日期（YYYY-MM-DD），可为空。
            若非空，LLM 不应覆盖它；若为空，LLM 需基于今日锚点推断。
    """
    import subprocess, tempfile

    today = _today_str()
    truncated = content[:8000] if len(content) > 8000 else content

    date_instruction = (
        f"已知发布日期：{fetcher_date}（由 fetcher 提供；date 字段必须原样输出此值，不得修改）"
        if fetcher_date
        else f"发布日期未明示；请根据今天（{today}）锚点推断，若无法确定 date 字段留空字符串"
    )

    user_msg = (
        f"今天是 {today}。\n"
        f"{date_instruction}\n\n"
        f"平台: {platform}\n作者: {author}\n标题: {title}\nURL: {url}\n\n"
        f"正文:\n{truncated}"
    )

    system_prompt = _build_system_prompt(today)
    if LLM_API_MODE == 'anthropic_messages':
        payload_obj = {
            "model": LLM_MODEL,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_msg}],
            "max_tokens": 2500,
        }
    else:
        payload_obj = {
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": 2500,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
    payload = json.dumps(payload_obj, ensure_ascii=False)

    # 写入临时文件避免命令行长度限制
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        f.write(payload)
        tmp_path = f.name

    def _extract_response_text(body):
        # Anthropic Messages: {"content": [{"type": "text", "text": "..."}]}
        content_items = body.get('content')
        if isinstance(content_items, list) and content_items:
            first = content_items[0]
            if isinstance(first, dict) and first.get('text'):
                return first.get('text', '')

        # OpenAI Chat Completions: {"choices": [{"message": {"content": "..."}}]}
        choices = body.get('choices')
        if isinstance(choices, list) and choices:
            message = choices[0].get('message') or {}
            content = message.get('content', '')
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        parts.append(item.get('text') or item.get('content') or '')
                    elif isinstance(item, str):
                        parts.append(item)
                return '\n'.join(p for p in parts if p)

        # Responses-style fallback.
        output = body.get('output')
        if isinstance(output, list):
            parts = []
            for item in output:
                for c in item.get('content', []) if isinstance(item, dict) else []:
                    if isinstance(c, dict):
                        parts.append(c.get('text') or '')
            if parts:
                return '\n'.join(parts)

        return ''

    def _loads_jsonish(text):
        text = re.sub(r'^```(?:json)?\s*', '', text.strip())
        text = re.sub(r'\s*```$', '', text.strip())
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            raise

    try:
        env = os.environ.copy()
        env['no_proxy'] = '*'
        headers = ['-H', 'Content-Type: application/json']
        if LLM_API_MODE == 'anthropic_messages':
            headers += ['-H', 'anthropic-version: 2023-06-01']
            if LLM_API_KEY:
                headers += ['-H', f'x-api-key: {LLM_API_KEY}']
        elif LLM_API_KEY:
            headers += ['-H', f'Authorization: Bearer {LLM_API_KEY}']
        extra_ua = os.getenv('LEARNING_ASSISTANT_LLM_USER_AGENT')
        if extra_ua:
            headers += ['-H', f'User-Agent: {extra_ua}']

        result = subprocess.run(
            ['curl', '-sS', '--noproxy', '*', '--max-time', '45', LLM_API_URL, *headers, '-d', f'@{tmp_path}'],
            capture_output=True, text=True, timeout=50, env=env,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or 'curl failed').strip()[:500])
        if not result.stdout.strip():
            raise RuntimeError('LLM 返回空响应')
        body = json.loads(result.stdout)
        if body.get('error'):
            raise RuntimeError(f'LLM error: {body["error"]}')
        text = _extract_response_text(body)
        if not text.strip():
            raise RuntimeError(f'LLM 响应缺少文本字段，keys={list(body.keys())}')
        return _loads_jsonish(text)
    except Exception as e:
        print(f'[llm] 分析失败，回退 heuristic: {e}')
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _get_title(data):
    """提取标题"""
    if data.get('title'):
        return data['title']
    if data.get('text'):
        # Twitter: 取前 50 字作标题
        text = data['text'].replace('\n', ' ')
        return text[:50] + ('...' if len(text) > 50 else '')
    if data.get('full_text'):
        text = data['full_text'].replace('\n', ' ')
        return text[:50] + ('...' if len(text) > 50 else '')
    return '无标题'


_URL_AUTHOR_PATTERNS = [
    # gist.github.com/{user}/{gist_id}
    (re.compile(r'gist\.github\.com/([^/]+)/'), None),
    # github.com/{owner}/{repo} — 排除 github 自身路径
    (re.compile(r'github\.com/([^/]+)/'),
     {'gist', 'orgs', 'settings', 'topics', 'search', 'features',
      'pricing', 'marketplace', 'login', 'signup', 'explore',
      'notifications', 'new', 'codespaces', 'sponsors', 'issues'}),
    # x.com/{username}/status/{id} 或 twitter.com/{username}
    (re.compile(r'(?:x\.com|twitter\.com)/([^/?#]+)'),
     {'home', 'explore', 'search', 'notifications', 'messages',
      'settings', 'i', 'compose'}),
]


def _author_from_url(url):
    """从 URL 路径里回退提取作者/用户名。找不到返回空串。"""
    if not url:
        return ''
    for pattern, blacklist in _URL_AUTHOR_PATTERNS:
        m = pattern.search(url)
        if m:
            candidate = m.group(1)
            if blacklist is None or candidate.lower() not in blacklist:
                return candidate
    return ''


def _get_author(data):
    """提取作者"""
    author = data.get('author') or data.get('username') or data.get('name') or ''
    # author 可能是 dict（Twitter API 返回嵌套对象）
    if isinstance(author, dict):
        author = author.get('name') or author.get('screen_name') or author.get('nickname') or str(author)
    author = str(author).strip()
    # URL 回退：web-scraper 走 gist/github/twitter 兜底时 data 里通常没 author 字段
    if not author or author.lower() in ('unknown', 'none'):
        fallback = _author_from_url(data.get('url', ''))
        if fallback:
            return fallback
    return author


def _get_content(data):
    """提取正文内容"""
    # 优先级：transcript > subtitle > full_text > content > text > desc
    content = ''
    for key in ['transcript', 'subtitle', 'full_text', 'content', 'text', 'desc']:
        val = data.get(key, '')
        if val and len(str(val)) > 10:
            content = str(val)
            break
    # 拼接所有可用文本
    if not content:
        parts = []
        for key in ['title', 'description', 'text', 'content']:
            val = data.get(key, '')
            if val:
                parts.append(str(val))
        content = '\n'.join(parts)

    media = data.get('media') or {}
    videos = media.get('videos') or []
    if videos and not data.get('transcript'):
        lines = ['\n\n[媒体抓取提示] 该 X/Twitter 帖包含视频，但当前未转写视频正文。']
        status = data.get('transcript_status')
        if status:
            lines.append(f'转写状态: {status}')
        for i, video in enumerate(videos[:3], 1):
            duration = video.get('duration')
            duration_text = f'，时长约 {duration} 秒' if duration else ''
            lines.append(f'视频 {i}: {video.get("url", "")}{duration_text}')
        content += '\n'.join(lines)

    return content


def _make_summary(content, title):
    """生成概要（2-3句）"""
    if not content:
        return title or '无内容'
    # 去除多余空白
    text = re.sub(r'\s+', ' ', content).strip()
    # 取前 300 字
    if len(text) > 300:
        # 尝试在句号处截断
        cut = text[:300]
        for end in ['。', '！', '？', '.', '!', '?']:
            idx = cut.rfind(end)
            if idx > 100:
                return cut[:idx + 1]
        return cut + '...'
    return text


def _extract_points(content, title):
    """提取核心知识点"""
    if not content:
        return '• 内容不足，无法提取要点'

    text = content + '\n' + (title or '')
    points = []

    # 策略1：找已有的列表项（• - 1. 等）
    list_items = re.findall(r'(?:^|\n)\s*[•\-\*\d+\.]\s*(.+)', text)
    if list_items:
        for item in list_items[:5]:
            item = item.strip()
            if len(item) > 5:
                points.append(item)

    # 策略2：按句子拆分，取信息密度高的
    if len(points) < 3:
        sentences = re.split(r'[。！？\.\!\?]\s*', text)
        for s in sentences:
            s = s.strip()
            if len(s) > 15 and len(s) < 200 and s not in points:
                # 简单过滤无意义句子
                if not re.match(r'^(大家好|今天|首先|最后|谢谢|关注|点赞)', s):
                    points.append(s)
                    if len(points) >= 5:
                        break

    if not points:
        # 兜底：截取前几段
        paras = [p.strip() for p in text.split('\n') if p.strip() and len(p.strip()) > 10]
        points = paras[:3]

    # 格式化
    return '\n'.join(f'• {p[:100]}' for p in points[:5]) if points else '• 内容较短，无明显知识点'


def _biz_analysis(content, title, platform):
    """业务借鉴分析"""
    text = (content or '') + ' ' + (title or '')
    text_lower = text.lower()

    parts = []

    # 业务透镜 1（默认：跨境电商/TikTok Shop，可用 LEARNING_ASSISTANT_BIZ_LENS_1_LABEL 覆盖）
    ecom_keywords = ['跨境', '电商', 'tiktok', 'shop', '选品', '运营', '流量', '变现',
                     '广告', 'cpc', 'cpm', 'roas', 'acos', 'listing', 'amazon',
                     'shopify', '独立站', '供应链', '物流', 'fba', '利润',
                     'ecommerce', 'dropship', '带货', '直播', 'gmv']
    ecom_hits = [k for k in ecom_keywords if k in text_lower]
    if ecom_hits:
        parts.append(f'【{BIZ_LENS_1_LABEL}】涉及关键词：{", ".join(ecom_hits[:5])}，可结合该业务参考')
    else:
        parts.append(f'【{BIZ_LENS_1_LABEL}】未发现直接相关内容，可作为行业视野拓展')

    # 业务透镜 2（默认：AI Agent 架构，可用 LEARNING_ASSISTANT_BIZ_LENS_2_LABEL 覆盖）
    tech_keywords = ['agent', 'ai', 'llm', 'prompt', '自动化', 'workflow', 'api',
                     'rag', '知识库', '多agent', 'multi-agent', 'tool', 'skill',
                     'claude', 'gpt', 'openai', 'anthropic', '大模型']
    tech_hits = [k for k in tech_keywords if k in text_lower]
    if tech_hits:
        parts.append(f'【{BIZ_LENS_2_LABEL}】涉及：{", ".join(tech_hits[:5])}，可参考用于该方向优化')

    return '\n'.join(parts)


def _platform_label(platform):
    """平台中文标签"""
    labels = {
        'twitter': 'X/Twitter',
        'youtube': 'YouTube',
        'douyin': '抖音',
        'tiktok': 'TikTok',
        'wechat_mp': '微信公众号',
        'xiaohongshu': '小红书',
        'zhihu': '知乎',
        'web': '网页',
        'feishu': '飞书',
    }
    return labels.get(platform, platform)


def _format_llm_result(llm_result, title, author, platform, url, data):
    """将 LLM JSON 结果格式化为输出字符串，并写回 data 的评分/动作"""
    scores = llm_result.get('scores', {})
    total = round(
        scores.get('info_density', 3) * 0.30 +
        scores.get('relevance', 3) * 0.30 +
        scores.get('scarcity', 3) * 0.20 +
        scores.get('reusability', 3) * 0.15 +
        scores.get('timeliness', 3) * 0.05
    , 1)
    score_20 = round(total * 4, 1)

    if score_20 >= 16:
        store_icon = '🔥 高价值入库'
        store_action = 'store'
    elif score_20 >= 10:
        store_icon = '📖 存摘要'
        store_action = 'store_low'
    else:
        store_icon = '⚪ 不入库'
        store_action = 'skip'

    data['_score'] = score_20
    data['_action'] = store_action

    platform_label = _platform_label(platform)
    author_label = f' @{author}' if author else ''

    out = f'📌 {title} — {platform_label}{author_label}\n\n'
    out += f'━━ 内容概要 ━━\n{llm_result.get("summary", "")}\n\n'

    points = llm_result.get('points', [])
    out += '━━ 核心知识点 ━━\n'
    out += '\n'.join(f'• {p}' for p in points[:6]) + '\n\n'

    out += '━━ 业务借鉴分析 ━━\n'
    biz_ecom = llm_result.get('biz_ecom')
    biz_arch = llm_result.get('biz_arch')
    if biz_ecom and biz_ecom != 'null':
        out += f'【跨境业务】{biz_ecom}\n'
    else:
        out += '【跨境业务】无直接关联\n'
    if biz_arch and biz_arch != 'null':
        out += f'【AI架构】{biz_arch}\n'
    out += '\n'

    out += '━━ 评分 ━━\n'
    out += (f'信息密度 {scores.get("info_density", "?")}/5 · '
            f'业务相关 {scores.get("relevance", "?")}/5 · '
            f'稀缺性 {scores.get("scarcity", "?")}/5 · '
            f'可复用 {scores.get("reusability", "?")}/5 · '
            f'时效 {scores.get("timeliness", "?")}/5\n')
    out += f'→ 总分 {score_20}/20\n\n'

    out += f'━━ 入库决策 ━━\n{store_icon}'

    # 素材提取
    arsenal = llm_result.get('arsenal', [])
    if arsenal:
        out += '\n\n━━ 素材提取 ━━'
        for a in arsenal[:8]:
            out += f'\n• [{a.get("type", "?")}] {a.get("content", "")}（出处：{a.get("source_label", "")}）'

    return out


def analyze_single(data):
    """
    分析单条内容 — LLM 优先，heuristic fallback

    注意 (2026-04-07 修复)：此函数不再写入任何文件。返回分析文本，
    由 caller 负责通过 `write_analysis_sidecar()` 写入 sidecar 文件。
    Raw md 正文必须保持未被 analyzer 污染。
    """
    platform = data.get('platform', 'unknown')
    url = data.get('url', '')
    error = data.get('error')
    if error:
        failure_logger.log(url=url, platform=platform, step='fetch_result_error', error=error)
        return f'❌ 抓取失败 — {platform}\n{error}'

    try:
        title = _get_title(data)
        author = _get_author(data)
        content = _get_content(data)

        if not content and not title:
            return f'❌ 未获取到有效内容 — {platform}'

        # fetcher 已知发布日期优先（若 ISO 可解析），LLM 不得覆盖
        fetcher_date = _fetcher_date(data)

        # ── LLM 分析（优先）──
        llm_result = _llm_analyze(title, author, content, platform, url, fetcher_date=fetcher_date)
        if llm_result and isinstance(llm_result, dict) and 'summary' in llm_result:
            # 解析日期：fetcher 优先，其次 LLM 输出，最后兜底留空
            resolved_date = fetcher_date
            if not resolved_date:
                llm_date = _coerce_iso_date(llm_result.get('date', ''))
                if llm_date:
                    resolved_date = llm_date
            if resolved_date:
                data['_source_date'] = resolved_date
            return _format_llm_result(llm_result, title, author, platform, url, data)

        # ── Heuristic fallback ──
        summary = _make_summary(content, title)
        points = _extract_points(content, title)
        biz_analysis = _biz_analysis(content, title, platform)

        from scorer import score_content
        score_result = score_content(title, content, platform)
        raw_score = score_result.get('total', 5.0)
        score_20 = round(raw_score * 2, 1)
        breakdown = score_result.get('breakdown', {})
        score_detail = (
            f"实战含金量 {breakdown.get('practical_value', 0):+.1f} · "
            f"反营销 {breakdown.get('anti_marketing', 0):+.1f} · "
            f"业务相关 {breakdown.get('relevance', 0):+.1f} · "
            f"信息密度 {breakdown.get('info_density', 0):+.1f}"
        )

        if score_20 >= 16:
            store_icon = '🔥 高价值入库'
            store_action = 'store'
        elif score_20 >= 10:
            store_icon = '📖 存摘要'
            store_action = 'store_low'
        else:
            store_icon = '⚪ 不入库'
            store_action = 'skip'

        data['_score'] = score_20
        data['_action'] = store_action
        if fetcher_date:
            data['_source_date'] = fetcher_date

        platform_label = _platform_label(platform)
        author_label = f' @{author}' if author else ''

        output = f'📌 {title} — {platform_label}{author_label}\n\n'
        output += f'━━ 内容概要 ━━\n{summary}\n\n'
        output += f'━━ 核心知识点 ━━\n{points}\n\n'
        output += f'━━ 业务借鉴分析 ━━\n{biz_analysis}\n\n'
        output += f'━━ 评分 ━━\n{score_20}/20 分 | {score_detail} (heuristic)\n\n'
        output += f'━━ 入库决策 ━━\n{store_icon}'

        return output

    except Exception as e:
        failure_logger.log(url=url, platform=platform, step='analyze', error=str(e), exc=e)
        raise


# ─────────── Sidecar writer (Bug 2 fix) ───────────

def _relpath_from_knowledge(raw_filepath):
    """把绝对 Raw md 路径缩减到相对 knowledge/articles/ 的展示形式。失败返回 basename。"""
    try:
        skill_dir = os.path.dirname(SCRIPT_DIR)
        knowledge_root = os.path.join(skill_dir, 'knowledge', 'articles')
        return os.path.relpath(raw_filepath, knowledge_root)
    except (ValueError, OSError):
        return os.path.basename(raw_filepath)


def write_analysis_sidecar(raw_filepath, analysis_text, data):
    """
    把 analyzer 的输出写到 sidecar 文件 `<basename>.analysis.md`，
    确保 Raw md 正文不被污染（符合 workspace-wiki schema.md §1）。

    Args:
        raw_filepath: storage.store_article 返回的 Raw md 绝对路径
        analysis_text: analyze_single() 的字符串输出
        data: fetcher dict（含 _score, _action, _source_date 等）

    Returns:
        sidecar 文件的绝对路径；若参数非法返回 None。
    """
    if not raw_filepath or not analysis_text:
        return None

    # sidecar 命名：foo.md -> foo.analysis.md（同目录）
    base, ext = os.path.splitext(raw_filepath)
    sidecar_path = f'{base}.analysis.md'

    raw_rel = _relpath_from_knowledge(raw_filepath)
    platform = data.get('platform', 'unknown')
    score = data.get('_score')
    action = data.get('_action', '')
    source_date = data.get('_source_date', '')
    analyzed_at = datetime.now().strftime('%Y-%m-%d %H:%M')

    fm_lines = [
        '---',
        'layer: analysis',
        f'raw_source: {raw_rel}',
        f'platform: {platform}',
        f'analyzed_at: {analyzed_at}',
    ]
    if source_date:
        fm_lines.append(f'source_date: {source_date}')
    if score is not None:
        fm_lines.append(f'score: {score}')
    if action:
        fm_lines.append(f'action: {action}')
    fm_lines.append('---')
    fm_lines.append('')

    body = '\n'.join(fm_lines) + analysis_text + '\n'

    try:
        with open(sidecar_path, 'w', encoding='utf-8') as f:
            f.write(body)
        return sidecar_path
    except OSError as e:
        failure_logger.log(
            url=data.get('url', ''),
            platform=platform,
            step='write_analysis_sidecar',
            error=str(e),
            exc=e,
        )
        return None


# ─────────── 本地 smoke test（不调真实 LLM）───────────

if __name__ == '__main__':
    import tempfile

    # 1) 验证 system prompt 含今天的日期
    today = _today_str()
    sp = _build_system_prompt(today)
    assert f'今天的日期是 {today}' in sp, '系统提示未注入今日日期'
    print(f'[smoke] system prompt anchored to {today}: OK')

    # 2) 验证 _coerce_iso_date 的若干格式
    twitter_sample = 'Mon Apr 07 10:25:14 +0000 2026'
    assert _coerce_iso_date(twitter_sample) == '2026-04-07', f'Twitter date 解析失败: {_coerce_iso_date(twitter_sample)}'
    assert _coerce_iso_date('2026-03-08') == '2026-03-08'
    assert _coerce_iso_date('') == ''
    assert _coerce_iso_date('nonsense') == ''
    print('[smoke] _coerce_iso_date: OK')

    # 3) 验证 _fetcher_date 取到各种 key
    assert _fetcher_date({'created_at': twitter_sample}) == '2026-04-07'
    assert _fetcher_date({'date': '2026-03-08'}) == '2026-03-08'
    assert _fetcher_date({'nothing': 'here'}) == ''
    print('[smoke] _fetcher_date: OK')

    # 4) 验证 sidecar 写入不触碰 raw md，并保持独立文件
    with tempfile.TemporaryDirectory() as td:
        raw_md = os.path.join(td, 'sample.md')
        with open(raw_md, 'w', encoding='utf-8') as f:
            f.write('---\nplatform: test\n---\n\n# hello\n\n## 原文\n\nbody only.\n')
        raw_mtime_before = os.path.getmtime(raw_md)
        raw_content_before = open(raw_md, 'r', encoding='utf-8').read()

        sidecar = write_analysis_sidecar(
            raw_md,
            '📌 test — fake\n\n━━ 内容概要 ━━\nfake analysis\n\n━━ 入库决策 ━━\n🔥 高价值入库',
            {'platform': 'test', '_score': 18, '_action': 'store', '_source_date': '2026-04-07'},
        )
        assert sidecar and sidecar.endswith('.analysis.md'), f'sidecar path 异常: {sidecar}'
        assert os.path.exists(sidecar), 'sidecar 未写出'
        assert os.path.getmtime(raw_md) == raw_mtime_before, 'raw md mtime 被修改'
        assert open(raw_md, 'r', encoding='utf-8').read() == raw_content_before, 'raw md 内容被修改'
        sidecar_body = open(sidecar, 'r', encoding='utf-8').read()
        assert 'raw_source:' in sidecar_body
        assert 'layer: analysis' in sidecar_body
        assert 'fake analysis' in sidecar_body
        print('[smoke] sidecar write: OK')
        print(f'[smoke] sidecar preview:\n{sidecar_body[:300]}...')

    print('[smoke] ALL OK')
