import json
from pathlib import Path
from pydantic_settings import BaseSettings

_OVERRIDE_FILE = Path(__file__).parent / "user_settings.json"


class Settings(BaseSettings):
    # 数据库
    DATABASE_URL: str = "sqlite:///./stock_screener.db"

    # 数据更新
    DATA_UPDATE_INTERVAL_HOURS: int = 24
    AKSHARE_TIMEOUT: int = 30

    # ───────── 评分权重（可被回测优化器覆盖） ─────────
    FUNDAMENTAL_WEIGHT: float = 0.40   # 基本面
    VALUATION_WEIGHT:   float = 0.25   # 估值安全边际
    SENTIMENT_WEIGHT:   float = 0.15   # 舆情
    MACRO_WEIGHT:       float = 0.20   # 宏观/市场环境（上调：宏观是最强预测因子）

    # ───────── 买卖阈值（5 等级）─────────
    # 数据驱动校准（基于 run#25 的 845 条 BUY 信号分桶分析）：
    #   60-64 区间胜率仅 43-47%（亏损区），64+ 区间胜率 >57%
    # 因此 BUY 阈值设为 64，同时要求 ROE 质地 >=15 作为质量门槛
    #
    # 5 等级映射（由 generate_signal 应用，需配合各项 *_MIN_SCORE 门槛）：
    #   composite >= 80 + 全门槛通过   → STRONG_BUY  必买
    #   composite >= 64 + 全门槛通过   → BUY         买入
    #   composite >= 50 或被门槛降级   → HOLD        持有
    #   composite >= 30                → SELL        卖出
    #   composite <  30 或 veto 触发   → STRONG_SELL 必卖
    #
    # ⚠️ 注意阈值的非对称语义：
    #   STRONG_BUY_THRESHOLD = 80：composite **>=** 80 时进入必买（"上界，达到则触发"）
    #   STRONG_SELL_THRESHOLD = 30：composite **<**  30 时进入必卖（"下界，跌破则触发"）
    #   两者数值上对称（高分阈/低分阈），但比较方向相反。修改时务必看清。
    STRONG_BUY_THRESHOLD:  float = 80.0
    BUY_THRESHOLD:         float = 64.0
    HOLD_LOW:              float = 50.0
    SELL_THRESHOLD:        float = 50.0
    STRONG_SELL_THRESHOLD: float = 30.0

    # ───────── 公司基本面准入线 ─────────
    ROE_MIN:                  float = 15.0
    ROE_MIN_YEARS:            int   = 7
    MAX_DEBT_RATIO:           float = 0.65
    MIN_PROFIT_GROWTH_YEARS:  int   = 5
    MIN_FCF_RATIO:            float = 0.8

    # ───────── 行业准入线 ─────────
    # v103 数据驱动校准（run#26 分析）：
    #   行业评分 <50：n=113，胜率 48.4%（亏损区）
    #   行业评分 ≥50：n=177，胜率 70.1%
    INDUSTRY_MIN_SCORE: float = 50.0

    # ───────── 宏观环境准入线（v104 新增） ─────────
    # run#28 分析：winners 宏观分均值 60.88 vs losers 57.05（+3.83 gap）
    #   macro >= 55：n=116，胜率 75.86%
    #   macro >= 65：n= 66，胜率 84.85%
    # 取 55 作为平衡点（样本量与胜率权衡）
    MACRO_MIN_SCORE: float = 55.0

    # ───────── 估值安全边际准入线（v107 新增） ─────────
    # run#31 分析（v106 修复 PE/PB 后 BUY 分桶）：
    #   valuation_pct <50：n=11 胜率 63.6%
    #   valuation_pct ≥75：n=23 胜率 82.6%
    # 即"越便宜胜率越高"。取 70 作为门槛，拒绝估值偏贵信号
    VALUATION_MIN_SCORE: float = 70.0

    # ───────── 金融行业宏观门槛（v108 新增） ─────────
    # run#32 分析：v107 中 2 笔独立误判全部为银行股（002142/600036）2022-10-01
    #   误判时 macro_pct=63.7；银行成功 trades macro ∈ [60.9, 70.9]
    # 银行对宏观利率（LPR）与信贷周期敏感，非金融用 MACRO_MIN=55 已够，
    # 金融行业要求 macro_pct >= 65，规避 NIM 压力期
    FIN_MACRO_MIN_SCORE: float = 65.0

    # ═══════════════════════════════════════════════════════════
    # 短期信号系统（v200 新增）
    # ═══════════════════════════════════════════════════════════
    # 与长期信号并行：长期看"能不能赚钱"，短期看"现在涨不涨"。
    # 5 维度（独立于长期）：
    #   动量 35% + 量价 15% + 宏观 25% + 科技板块 15% + 新闻热度 10%
    # 持有周期假设：1-2 周（短线波段），非长期持有
    SHORT_MOMENTUM_WEIGHT:  float = 0.35   # 价格动量 (5/20/60 日收益、MA20/60、RSI)
    SHORT_VOLPRICE_WEIGHT:  float = 0.15   # 量价关系 (涨幅 × 量比)
    SHORT_MACRO_WEIGHT:     float = 0.25   # 宏观环境 (复用 score_macro)
    SHORT_TECH_WEIGHT:      float = 0.15   # 科技 / 政策板块
    SHORT_NEWS_HEAT_WEIGHT: float = 0.10   # 新闻热度 + 情感
    # 总和必须 = 1.0（启动时校验，否则评分会失真）

    # 短期 5 等级阈值（与长期信号阈值独立）
    SHORT_STRONG_BUY_THRESHOLD:  float = 75.0
    SHORT_BUY_THRESHOLD:         float = 60.0
    SHORT_SELL_THRESHOLD:        float = 45.0
    SHORT_STRONG_SELL_THRESHOLD: float = 30.0
    # 触发必卖的硬条件：近 5 日跌幅 > 10%
    SHORT_HARD_SELL_5D_DROP: float = -0.10

    # 科技 / 政策催化白名单（行业代码用 BK 前缀，对应 industries 表）
    # 这些行业获得短期"科技板块"满分基础，再叠加行业评分
    TECH_INDUSTRIES: set = frozenset({
        "BK0475",  # 半导体
        "BK0438",  # 软件开发
        "BK0464",  # 计算机设备
        "BK0436",  # 通信设备
        "BK1015",  # 能源金属（锂、镍）
        "BK1320",  # 逆变器
        "BK0457",  # 电网设备
        "BK1304",  # 锂电专用设备
        "BK0480",  # 互联网服务
        "BK1216",  # 医药生物（创新药/生物制品）
        "BK1495",  # 光伏设备
        "BK0479",  # 消费电子
    })

    # ───────── 回测参数 ─────────
    BACKTEST_START_YEAR:  int = 2014
    BACKTEST_TRAIN_YEARS: int = 5
    BACKTEST_VAL_YEARS:   int = 2
    HOLD_MONTHS:          int = 12
    REPORT_LAG_DAYS:      int = 90

    # 回测优化目标
    OPT_W_WIN_RATE: float = 0.50
    OPT_W_IC:       float = 0.30
    OPT_W_SHARPE:   float = 0.20

    # ───────── 模拟盘（Paper Trading） ─────────
    # 初始资金只在下次"重置账户"时生效（不会改现有账户余额）
    PAPER_INIT_CASH: float = 1_000_000.0
    PAPER_FEE_RATE:  float = 0.0003        # 手续费率（万分之三）
    PAPER_MIN_FEE:   float = 5.0           # 单笔最低手续费（元）
    PAPER_LOT_SIZE:  int   = 100           # 最小交易单位（A 股 = 100 股）

    # ───────── 用户认证（JWT） ─────────
    # 生产部署时请通过 .env 覆盖 JWT_SECRET（仅本机/测试环境可沿用默认值）
    JWT_SECRET:        str = "change-me-in-production-this-should-be-random-and-long"
    JWT_ALGORITHM:     str = "HS256"
    JWT_EXPIRE_HOURS:  int = 24 * 7        # token 有效期（小时），默认 7 天

    class Config:
        env_file = ".env"


# ── 全局单例，启动时加载用户覆盖 ──
settings = Settings()

def _apply_overrides():
    """从 user_settings.json 覆盖全局 settings"""
    if not _OVERRIDE_FILE.exists():
        return
    try:
        overrides = json.loads(_OVERRIDE_FILE.read_text())
        for k, v in overrides.items():
            if hasattr(settings, k):
                setattr(settings, k, type(getattr(settings, k))(v))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"加载用户设置失败: {e}")

_apply_overrides()


def get_settings_dict() -> dict:
    """返回当前所有可配置参数（排除内部字段）"""
    exclude = {"DATABASE_URL", "DATA_UPDATE_INTERVAL_HOURS", "AKSHARE_TIMEOUT"}
    return {k: v for k, v in settings.__dict__.items() if k.isupper() and k not in exclude}


def save_settings(updates: dict) -> dict:
    """
    保存用户自定义参数到 user_settings.json，并立即应用到全局 settings
    返回应用后的完整参数字典
    """
    # 读取现有覆盖
    existing = {}
    if _OVERRIDE_FILE.exists():
        try:
            existing = json.loads(_OVERRIDE_FILE.read_text())
        except Exception:
            pass

    # 合并并校验
    for k, v in updates.items():
        if not hasattr(settings, k):
            continue
        orig_type = type(getattr(settings, k))
        try:
            coerced = orig_type(v)
            existing[k] = coerced
            setattr(settings, k, coerced)
        except (ValueError, TypeError):
            pass

    _OVERRIDE_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2))
    return get_settings_dict()
