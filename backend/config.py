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
    # 短期信号系统（v201：反转模型）
    # ═══════════════════════════════════════════════════════════
    # v200 是"追涨"模型，回测 IC=-0.114（评分越高表现越差）→ v201 翻转方向：
    #   - momentum / volprice 翻转：跌且超卖加分，涨且过热扣分
    #   - news_heat 权重清零（回测时新闻数据稀疏，恒为 50）
    #   - macro 是诊断里唯一正 IC 的维度（BUY 子集 +0.116）→ 大幅加权
    #   - tech 板块降权（贡献微弱）
    # 持有周期假设：1-2 周（短线波段），非长期持有
    # v203 实验结果：在当前 raw scoring（已精调过的非线性奖励）+ 高度共线因子的
    # 场景下，cross-sectional ranking 反而把 IC 从 0.108 降到 0.069，桶 5 超额
    # 从 +1.56% 降到 +0.92%。原因：把"极端事件给极端分"的量子信号拉平到均匀分布，
    # 丢失了 alpha 来源。框架代码保留，default 关掉。
    # 未来若加入正交因子（盈利惊喜 / 内幕交易 / 期权偏度等）可重新打开。
    SHORT_USE_CROSS_SECTIONAL_RANKS: bool = False

    # v202b+ 生产配置（IC=+0.108, win=50.4%，加定价权 v202f）
    SHORT_MOMENTUM_WEIGHT:           float = 0.00
    SHORT_VOLPRICE_WEIGHT:           float = 0.40
    SHORT_MACRO_WEIGHT:              float = 0.30
    SHORT_TECH_WEIGHT:               float = 0.05
    SHORT_NEWS_HEAT_WEIGHT:          float = 0.00
    SHORT_INDUSTRY_RELATIVE_WEIGHT:  float = 0.15
    SHORT_PRICING_POWER_WEIGHT:      float = 0.10
    # 总和必须 = 1.0

    # v202e 阈值（目标 BUY 胜率 ≥ 85%）：
    # 精度分析（run 56, 15d cycle）：
    #   composite ≥ 71 → 胜率 88.2%（n=68， 平均超额 +10.11%）
    #   composite ≥ 73 → 胜率更高，更稀疏
    # 信号稀疏（~68 个 BUY/年），用户需等待高分共振
    SHORT_STRONG_BUY_THRESHOLD:  float = 73.0
    SHORT_BUY_THRESHOLD:         float = 71.0
    SHORT_SELL_THRESHOLD:        float = 38.0
    SHORT_STRONG_SELL_THRESHOLD: float = 28.0

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
    HOLD_MONTHS:          int = 12   # 历史字段，仅旧路径兜底用
    REPORT_LAG_DAYS:      int = 90

    # v200：长/短期回测的持有 + 检查频率（统一以 days 计）
    BACKTEST_HOLD_DAYS_LONG:        int = 365   # 长期：约 12 个月
    BACKTEST_HOLD_DAYS_SHORT:       int = 15    # 短期：3 周内自然交易周期
    BACKTEST_CHECK_FREQ_DAYS_LONG:  int = 90    # 长期：季度检查
    BACKTEST_CHECK_FREQ_DAYS_SHORT: int = 15    # 与持有期对齐，回测样本独立（避免持仓重叠）

    # 短期回测的滚动窗口默认值（与长期独立）：
    # 长期默认 train=5/val=2（共 7 年）— 因为短期数据从 2022 起，套用这套会得到 0 窗口。
    # 短期默认 train=1.5/val=0.5（共 2 年），从 today-4y 起算可得到 ~3 个窗口。
    BACKTEST_TRAIN_YEARS_SHORT: float = 1.5
    BACKTEST_VAL_YEARS_SHORT:   float = 0.5

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


def _validate_weights():
    """启动时校验权重总和 = 1.0，避免评分数值失真"""
    import logging
    logger = logging.getLogger(__name__)
    long_sum = (
        settings.FUNDAMENTAL_WEIGHT +
        settings.VALUATION_WEIGHT +
        settings.SENTIMENT_WEIGHT +
        settings.MACRO_WEIGHT
    )
    if abs(long_sum - 1.0) > 0.001:
        logger.warning(f"长期信号权重总和 = {long_sum:.3f} ≠ 1.0，请检查配置")
    short_sum = (
        settings.SHORT_MOMENTUM_WEIGHT +
        settings.SHORT_VOLPRICE_WEIGHT +
        settings.SHORT_MACRO_WEIGHT +
        settings.SHORT_TECH_WEIGHT +
        settings.SHORT_NEWS_HEAT_WEIGHT +
        settings.SHORT_INDUSTRY_RELATIVE_WEIGHT +
        settings.SHORT_PRICING_POWER_WEIGHT
    )
    if abs(short_sum - 1.0) > 0.001:
        logger.warning(f"短期信号权重总和 = {short_sum:.3f} ≠ 1.0，请检查配置")


_validate_weights()


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
    _validate_weights()
    return get_settings_dict()
