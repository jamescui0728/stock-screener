"""
舆情分析引擎
策略：关键词规则引擎（快速）+ 可选接入 Claude API（精准）
输出：每条新闻 sentiment_score ∈ [-1, +1]，event_type 分类
"""
import re
from typing import Optional
import jieba
import jieba.posseg as pseg
from sqlalchemy.orm import Session
from models.models import NewsItem

# ──────────────────────────────────────────────
# 关键词词典
# ──────────────────────────────────────────────
POSITIVE_KEYWORDS = {
    # 业绩类
    "业绩超预期": 0.8, "净利润增长": 0.7, "营收创新高": 0.8,
    "业绩预增": 0.7, "超预期": 0.6, "利润大增": 0.7,
    "分红": 0.5, "股息": 0.5, "回购": 0.6,
    # 资本类
    "大股东增持": 0.9, "管理层增持": 0.8, "回购股票": 0.7,
    "战略投资": 0.5, "扩大产能": 0.4,
    # 政策类
    "政策支持": 0.5, "利好政策": 0.6, "税收优惠": 0.5,
    # 市场类
    "市场份额提升": 0.6, "中标": 0.5, "签订合同": 0.4,
    "获得批准": 0.5, "产品获批": 0.6,
}

NEGATIVE_KEYWORDS = {
    # 业绩类
    "业绩变脸": -0.9, "净利润下滑": -0.7, "亏损": -0.8,
    "业绩预警": -0.7, "业绩不及预期": -0.8, "营收下降": -0.6,
    "商誉减值": -0.7, "计提减值": -0.6,
    # 监管类
    "被立案": -0.9, "收到罚款": -0.8, "监管处罚": -0.9,
    "证监会": -0.5, "违规": -0.8, "造假": -1.0,
    "欺诈": -1.0, "内幕交易": -0.9,
    # 资本类
    "大股东减持": -0.7, "高管辞职": -0.6, "核心员工离职": -0.5,
    "股权质押": -0.5, "强制平仓": -0.8,
    # 经营类
    "停产": -0.7, "关厂": -0.8, "裁员": -0.6,
    "债务违约": -0.9, "流动性危机": -0.9, "被诉讼": -0.6,
}

EVENT_TYPE_PATTERNS = {
    "业绩":    r"业绩|净利润|营收|盈利|亏损",
    "监管":    r"证监会|立案|罚款|违规|处罚|监管",
    "增减持":  r"增持|减持|回购|股东|管理层",
    "高管":    r"高管|董事|总经理|CEO|辞职|离职|任命",
    "政策":    r"政策|补贴|税收|扶持|规划",
    "市场":    r"中标|合同|签约|市场份额|产品",
    "财务":    r"减值|商誉|债务|融资|借款",
}


# ──────────────────────────────────────────────
# 核心分析函数
# ──────────────────────────────────────────────
def analyze_sentiment(text: str) -> dict:
    """
    输入：新闻标题 + 摘要
    输出：{score, label, keywords, event_type}
    """
    if not text:
        return {"score": 0.0, "label": "neutral", "keywords": [], "event_type": None}

    score = 0.0
    hit_keywords = []

    # 1. 关键词匹配（正面）
    for kw, weight in POSITIVE_KEYWORDS.items():
        if kw in text:
            score += weight
            hit_keywords.append(kw)

    # 2. 关键词匹配（负面）
    for kw, weight in NEGATIVE_KEYWORDS.items():
        if kw in text:
            score += weight  # weight 已为负数
            hit_keywords.append(kw)

    # 3. 否定修饰处理（"未出现亏损" → 负转正）
    negation_pattern = r"(未|没有|不|无)\s*([^\s，。,]{1,4})"
    for m in re.finditer(negation_pattern, text):
        negated_word = m.group(2)
        for kw, weight in NEGATIVE_KEYWORDS.items():
            if negated_word in kw:
                score -= weight * 0.5   # 反转一半

    # 4. 截断到 [-1, 1]
    score = max(-1.0, min(1.0, score))

    # 5. 标签
    if score >= 0.3:
        label = "positive"
    elif score <= -0.3:
        label = "negative"
    else:
        label = "neutral"

    # 6. 事件分类
    event_type = _classify_event(text)

    return {
        "score": round(score, 4),
        "label": label,
        "keywords": hit_keywords[:10],
        "event_type": event_type,
    }


def _classify_event(text: str) -> Optional[str]:
    for event, pattern in EVENT_TYPE_PATTERNS.items():
        if re.search(pattern, text):
            return event
    return None


# ──────────────────────────────────────────────
# 批量分析 & 写库
# ──────────────────────────────────────────────
def analyze_all_news(db: Session, stock_code: Optional[str] = None, limit: int = 500) -> int:
    """对未分析的新闻做情感分析，写回数据库"""
    query = db.query(NewsItem).filter(NewsItem.sentiment_score.is_(None))
    if stock_code:
        query = query.filter_by(stock_code=stock_code)
    news_list = query.limit(limit).all()

    updated = 0
    for news in news_list:
        combined = (news.title or "") + " " + (news.summary or "")
        result = analyze_sentiment(combined)
        news.sentiment_score = result["score"]
        news.sentiment_label = result["label"]
        news.keywords        = result["keywords"]
        news.event_type      = result["event_type"]
        updated += 1

    db.commit()
    return updated


def get_stock_sentiment_score(db: Session, stock_code: str, days: int = 90) -> float:
    """
    返回近 N 天的综合舆情评分（满分 20）
    逻辑：
      - 一票否决事件（造假/立案）→ 直接返回 0
      - 按时间衰减加权平均情感分 → 映射到 [0, 20]
    """
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)
    news_list = (
        db.query(NewsItem)
        .filter(
            NewsItem.stock_code == stock_code,
            NewsItem.pub_date >= cutoff,
            NewsItem.sentiment_score.isnot(None),
        )
        .order_by(NewsItem.pub_date.desc())
        .all()
    )

    if not news_list:
        return 12.0  # 无新闻时给中性分

    # 一票否决：造假/立案/债务违约
    veto_events = {"监管", "业绩"}
    for news in news_list:
        if news.event_type in veto_events and (news.sentiment_score or 0) < -0.7:
            return 0.0  # 极端负面事件直接清零

    # 时间衰减加权（越近权重越高）
    total_weight = 0.0
    weighted_sum = 0.0
    for i, news in enumerate(news_list):
        decay = 0.9 ** i   # 指数衰减
        weighted_sum += (news.sentiment_score or 0) * decay
        total_weight += decay

    avg_score = weighted_sum / total_weight if total_weight > 0 else 0.0

    # [-1, 1] → [0, 20]
    normalized = (avg_score + 1) / 2 * 20
    return round(max(0.0, min(20.0, normalized)), 2)
