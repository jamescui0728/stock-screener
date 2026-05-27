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
    "扭亏为盈": 0.7, "扭亏": 0.6, "预增": 0.6, "同比增长": 0.4,
    "毛利率提升": 0.5, "净利大增": 0.7, "创历史新高": 0.6,
    "分红": 0.5, "股息": 0.5, "回购": 0.6, "高送转": 0.5,
    # 资本类
    "大股东增持": 0.9, "管理层增持": 0.8, "回购股票": 0.7,
    "回购注销": 0.7, "增持计划": 0.6, "举牌": 0.6,
    "战略投资": 0.5, "扩大产能": 0.4, "资产注入": 0.6,
    "并购重组": 0.6, "重大资产重组": 0.6, "收购": 0.4, "拟收购": 0.4,
    # 政策类
    "政策支持": 0.5, "利好政策": 0.6, "税收优惠": 0.5,
    "纳入": 0.4, "纳入指数": 0.5, "国产替代": 0.5, "自主可控": 0.4,
    "专项补贴": 0.5, "特别国债": 0.4,
    # 市场类
    "市场份额提升": 0.6, "中标": 0.5, "签订合同": 0.4, "大额订单": 0.6,
    "获得批准": 0.5, "产品获批": 0.6, "获批上市": 0.6, "临床获批": 0.5,
    "签约": 0.4, "战略合作": 0.4, "独家供应": 0.5, "新增订单": 0.5,
    "提价": 0.5, "涨价": 0.5, "满产": 0.4, "量产": 0.4, "投产": 0.4,
    "技术突破": 0.5, "获得专利": 0.4, "中标金额": 0.5,
}

NEGATIVE_KEYWORDS = {
    # 业绩类
    "业绩变脸": -0.9, "净利润下滑": -0.7, "亏损": -0.8, "巨亏": -0.9,
    "业绩预警": -0.7, "业绩不及预期": -0.8, "营收下降": -0.6,
    "业绩暴雷": -0.9, "暴雷": -0.8, "预亏": -0.7, "亏损扩大": -0.8,
    "商誉减值": -0.7, "计提减值": -0.6, "毛利率下滑": -0.5,
    # 监管类
    "被立案": -0.9, "立案调查": -0.9, "收到罚款": -0.8, "监管处罚": -0.9,
    "证监会": -0.5, "违规": -0.8, "造假": -1.0, "财务造假": -1.0,
    "欺诈": -1.0, "内幕交易": -0.9, "问询函": -0.6, "关注函": -0.5,
    "警示函": -0.6, "责令整改": -0.6, "处分": -0.6,
    # 资本类
    "大股东减持": -0.7, "减持": -0.5, "清仓式减持": -0.8, "高管辞职": -0.6,
    "核心员工离职": -0.5, "股权质押": -0.5, "强制平仓": -0.8, "质押爆仓": -0.9,
    "限售解禁": -0.5, "解禁": -0.4, "实控人被留置": -0.9, "被留置": -0.8,
    # 经营类
    "停产": -0.7, "关厂": -0.8, "裁员": -0.6, "诉讼": -0.5, "仲裁": -0.5,
    "债务违约": -0.9, "流动性危机": -0.9, "被诉讼": -0.6, "债务逾期": -0.8,
    "评级下调": -0.6, "停牌": -0.4, "终止": -0.4, "退市风险": -0.9,
    "退市": -0.9, "ST": -0.7, "戴帽": -0.7, "限制消费": -0.6,
}

EVENT_TYPE_PATTERNS = {
    # 顺序敏感：越具体/越强信号的事件排在前面（_classify_event 取首个命中）
    "退市风险": r"退市|\*?ST|戴帽|面值退",
    "监管":     r"证监会|立案|罚款|违规|处罚|监管|问询函|关注函|警示函|留置",
    "并购重组": r"并购|重组|资产注入|借壳|收购|要约",
    "解禁":     r"解禁|限售",
    "增减持":   r"增持|减持|回购|举牌|股东|管理层",
    "业绩":     r"业绩|净利润|营收|盈利|亏损|预增|预亏|扭亏",
    "高管":     r"高管|董事|总经理|CEO|辞职|离职|任命",
    "政策":     r"政策|补贴|税收|扶持|规划|国产替代|特别国债",
    "订单合作": r"中标|合同|签约|订单|战略合作|独家",
    "产品技术": r"获批|上市|临床|专利|量产|投产|技术突破|新产品",
    "财务":     r"减值|商誉|债务|融资|借款|质押|违约",
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
