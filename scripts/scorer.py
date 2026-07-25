#!/usr/bin/env python3
"""关键词启发式评分模块（LLM 分析不可用时的 fallback）"""
import json
import sys

SCORE_CRITERIA = {
    "practical_value": {
        "weight": 0.35,
        "description": "实战含金量：有具体数据/案例/步骤，能直接复用"
    },
    "anti_marketing": {
        "weight": 0.25,
        "description": "反营销号：客观、有争议性观点、不是纯鸡汤卖课"
    },
    "relevance": {
        "weight": 0.25,
        "description": "业务相关度：跨境电商/AI应用/内容创作/TikTok/流量变现"
    },
    "info_density": {
        "weight": 0.15,
        "description": "信息密度：单位字数包含的新信息量"
    }
}

def score_content(title, content, platform="web"):
    """
    基于内容长度和关键词的基础评分（LLM增强版后续迭代）

    参数:
        title: 标题
        content: 正文
        platform: 平台名（用于平台特定规则，暂未使用）

    返回:
        {
            "total": 评分 (1.0-10.0),
            "action": "store" | "store_low" | "skip",
            "criteria": 评分维度,
            "breakdown": {各维度得分明细}
        }
    """
    score = 5.0  # 基准分
    breakdown = {}

    # 1. 信息密度：内容长度合理范围加分
    content_len = len(content) if content else 0
    if 500 <= content_len <= 5000:
        density_score = 1.0
    elif content_len > 5000:
        density_score = 0.5
    elif content_len < 200:
        density_score = -1.5
    else:
        density_score = 0.0

    score += density_score
    breakdown["info_density"] = density_score

    # 2. 实战关键词
    practical_keywords = ['步骤', '方法', '案例', '数据', '实测', '复盘', '经验', 'ROI', '转化率', '利润']
    marketing_keywords = ['限时', '秒杀', '暴富', '躺赚', '割韭菜', '百万', '千万']
    relevance_keywords = ['跨境', 'TikTok', '小红书', 'AI', '选品', '流量', '内容创作', '变现', '电商']

    text = (title or '') + (content or '')
    practical_hits = sum(1 for k in practical_keywords if k in text)
    marketing_hits = sum(1 for k in marketing_keywords if k in text)
    relevance_hits = sum(1 for k in relevance_keywords if k in text)

    practical_score = min(practical_hits * 0.3, 1.5)
    marketing_penalty = -min(marketing_hits * 0.5, 2.0)
    relevance_score = min(relevance_hits * 0.3, 1.5)

    score += practical_score
    score += marketing_penalty
    score += relevance_score

    breakdown["practical_value"] = practical_score
    breakdown["anti_marketing"] = marketing_penalty
    breakdown["relevance"] = relevance_score

    # 限制在 1.0-10.0 范围
    total_score = round(min(max(score, 1.0), 10.0), 1)

    # 决策逻辑
    if total_score >= 7.0:
        action = "store"
    elif total_score >= 5.0:
        action = "store_low"
    else:
        action = "skip"

    return {
        "total": total_score,
        "action": action,
        "criteria": SCORE_CRITERIA,
        "breakdown": breakdown
    }


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        title_arg = sys.argv[1]
        content_arg = sys.argv[2]
        platform_arg = sys.argv[3] if len(sys.argv) >= 4 else "web"
        result = score_content(title_arg, content_arg, platform_arg)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        # 测试用例
        test_title = "TikTok 跨境电商选品实战：ROI 从 1.2 提升到 3.5 的完整复盘"
        test_content = """
        这是一篇关于跨境电商的实战经验分享。

        ## 背景
        我运营独立站已经 2 年，主要市场是美国。今年 3 月通过 TikTok 推广，ROI 从 1.2 提升到 3.5。

        ## 方法
        1. 选品：使用 Google Trends + Amazon BSR 数据交叉验证
        2. 素材：请本地 UGC 创作者拍摄，转化率提升 40%
        3. 投放：小预算测试 10 个素材，筛选出 CTR > 3% 的放量

        ## 数据
        - 测试预算：$500
        - 爆款素材 CTR：4.2%
        - 最终 ROI：3.5
        - 利润率：35%

        ## 踩坑
        不要盲目追热度，要看利润空间。
        """

        result = score_content(test_title, test_content, "web")
        print(json.dumps(result, ensure_ascii=False, indent=2))
