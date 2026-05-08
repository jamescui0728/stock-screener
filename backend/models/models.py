"""
数据库模型定义
所有表通过 SQLAlchemy ORM 映射到 SQLite
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, Boolean,
    DateTime, Date, ForeignKey, Text, JSON,
)
from sqlalchemy.orm import relationship
from database import Base


# ──────────────────────────────────────────────
# 用户（多用户 + 手机号登录）
# ──────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    phone         = Column(String(20), unique=True, index=True, nullable=False)
    password_hash = Column(String(200), nullable=False)
    name          = Column(String(50), nullable=True)       # 昵称（可选）
    is_active     = Column(Boolean, default=True)
    is_admin      = Column(Boolean, default=False)
    created_at    = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)

    watchlist     = relationship("Watchlist", back_populates="user",
                                 cascade="all, delete-orphan")
    paper_accounts = relationship("PaperAccount", back_populates="user",
                                  cascade="all, delete-orphan",
                                  order_by="PaperAccount.id")


# ──────────────────────────────────────────────
# 行业
# ──────────────────────────────────────────────
class Industry(Base):
    __tablename__ = "industries"

    id          = Column(Integer, primary_key=True, index=True)
    code        = Column(String(20), unique=True, index=True, nullable=False)
    name        = Column(String(100), nullable=False)
    level       = Column(Integer, default=1)          # 1=一级行业 2=二级
    parent_code = Column(String(20), nullable=True)

    # 最新评分
    score_revenue_stability  = Column(Float, nullable=True)   # 营收稳定性
    score_profit_stability   = Column(Float, nullable=True)   # 盈利稳定性
    score_anti_cycle         = Column(Float, nullable=True)   # 抗周期性
    score_competition        = Column(Float, nullable=True)   # 竞争格局
    total_score              = Column(Float, nullable=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    stocks = relationship("Stock", back_populates="industry")


# ──────────────────────────────────────────────
# 股票
# ──────────────────────────────────────────────
class Stock(Base):
    __tablename__ = "stocks"

    id          = Column(Integer, primary_key=True, index=True)
    code        = Column(String(10), unique=True, index=True, nullable=False)
    name        = Column(String(100), nullable=False)
    market      = Column(String(10), default="A")     # A / HK / US
    industry_code = Column(String(20), ForeignKey("industries.code"), nullable=True)
    listed_date = Column(Date, nullable=True)
    is_active   = Column(Boolean, default=True)
    total_shares = Column(Float, nullable=True)       # 总股本（用于 PE/PB 估算）

    # 最新公司评分
    score_roe_quality     = Column(Float, nullable=True)
    score_profit_growth   = Column(Float, nullable=True)
    score_cashflow        = Column(Float, nullable=True)
    score_financial_health = Column(Float, nullable=True)
    score_valuation       = Column(Float, nullable=True)
    fundamental_score     = Column(Float, nullable=True)

    # 综合建议分与信号
    composite_score  = Column(Float, nullable=True)
    signal           = Column(String(15), nullable=True)   # STRONG_BUY / BUY / HOLD / SELL / STRONG_SELL
    signal_reason    = Column(Text, nullable=True)
    signal_updated   = Column(DateTime, nullable=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    industry     = relationship("Industry", back_populates="stocks")
    financials   = relationship("FinancialData", back_populates="stock", order_by="FinancialData.period")
    prices       = relationship("PriceData", back_populates="stock", order_by="PriceData.trade_date")
    news_list    = relationship("NewsItem", back_populates="stock", order_by="NewsItem.pub_date.desc()")
    watchlist    = relationship("Watchlist", back_populates="stock")


# ──────────────────────────────────────────────
# 财务数据（年报 / 季报）
# ──────────────────────────────────────────────
class FinancialData(Base):
    __tablename__ = "financial_data"

    id         = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String(10), ForeignKey("stocks.code"), nullable=False, index=True)
    period     = Column(Date, nullable=False)          # 报告期
    report_type = Column(String(10), default="annual") # annual / quarter
    pub_date   = Column(Date, nullable=True)           # 实际发布日（防未来泄露）

    # 利润表
    revenue        = Column(Float, nullable=True)   # 营业收入
    gross_profit   = Column(Float, nullable=True)
    operating_profit = Column(Float, nullable=True)
    net_profit     = Column(Float, nullable=True)
    gross_margin   = Column(Float, nullable=True)   # %
    net_margin     = Column(Float, nullable=True)   # %

    # 资产负债表
    total_assets   = Column(Float, nullable=True)
    total_equity   = Column(Float, nullable=True)
    total_debt     = Column(Float, nullable=True)
    debt_ratio     = Column(Float, nullable=True)   # %
    interest_coverage = Column(Float, nullable=True)

    # 现金流量表
    operating_cashflow = Column(Float, nullable=True)
    capex              = Column(Float, nullable=True)
    free_cashflow      = Column(Float, nullable=True)
    fcf_ratio          = Column(Float, nullable=True)  # FCF / 净利润

    # 衍生指标
    roe   = Column(Float, nullable=True)   # %
    roic  = Column(Float, nullable=True)   # %
    eps   = Column(Float, nullable=True)
    bvps  = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    stock = relationship("Stock", back_populates="financials")


# ──────────────────────────────────────────────
# 价格数据
# ──────────────────────────────────────────────
class PriceData(Base):
    __tablename__ = "price_data"

    id         = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String(10), ForeignKey("stocks.code"), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    open       = Column(Float)
    high       = Column(Float)
    low        = Column(Float)
    close      = Column(Float)
    volume     = Column(Float)
    pe_ttm     = Column(Float, nullable=True)
    pb         = Column(Float, nullable=True)
    market_cap = Column(Float, nullable=True)

    stock = relationship("Stock", back_populates="prices")


# ──────────────────────────────────────────────
# 宏观指标
# ──────────────────────────────────────────────
class MacroData(Base):
    __tablename__ = "macro_data"

    id         = Column(Integer, primary_key=True, index=True)
    date       = Column(Date, nullable=False, index=True)
    indicator  = Column(String(50), nullable=False, index=True)  # PMI / CPI / M2 ...
    value      = Column(Float, nullable=True)
    unit       = Column(String(20), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


# ──────────────────────────────────────────────
# 新闻 / 舆情
# ──────────────────────────────────────────────
class NewsItem(Base):
    __tablename__ = "news_items"

    id          = Column(Integer, primary_key=True, index=True)
    stock_code  = Column(String(10), ForeignKey("stocks.code"), nullable=True, index=True)
    industry_code = Column(String(20), nullable=True)
    pub_date    = Column(DateTime, nullable=False, index=True)
    title       = Column(String(500), nullable=False)
    summary     = Column(Text, nullable=True)
    source      = Column(String(100), nullable=True)
    url         = Column(String(500), nullable=True)

    # 情感分析结果
    sentiment_score = Column(Float, nullable=True)   # -1.0 ~ +1.0
    sentiment_label = Column(String(10), nullable=True)  # positive/negative/neutral
    keywords        = Column(JSON, nullable=True)        # 关键词列表
    event_type      = Column(String(50), nullable=True)  # 业绩/监管/增持/高管...

    created_at = Column(DateTime, default=datetime.utcnow)

    stock = relationship("Stock", back_populates="news_list")


# ──────────────────────────────────────────────
# 自选股
# ──────────────────────────────────────────────
class Watchlist(Base):
    __tablename__ = "watchlist"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    stock_code = Column(String(10), ForeignKey("stocks.code"), nullable=False)
    note       = Column(Text, nullable=True)
    added_at   = Column(DateTime, default=datetime.utcnow)
    alert_on_signal_change = Column(Boolean, default=True)

    stock = relationship("Stock", back_populates="watchlist")
    user  = relationship("User", back_populates="watchlist")


# ──────────────────────────────────────────────
# 回测结果
# ──────────────────────────────────────────────
class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id          = Column(Integer, primary_key=True, index=True)
    run_at      = Column(DateTime, default=datetime.utcnow)
    version     = Column(Integer, default=1)
    description = Column(Text, nullable=True)

    # 参数快照
    params = Column(JSON, nullable=True)

    # 汇总指标
    win_rate          = Column(Float, nullable=True)   # 买入胜率 %
    sell_accuracy     = Column(Float, nullable=True)   # 卖出有效率 %
    annualized_alpha  = Column(Float, nullable=True)   # 年化超额收益 %
    ic_mean           = Column(Float, nullable=True)   # IC 均值
    ic_ir             = Column(Float, nullable=True)   # IC / IC_std
    sharpe_ratio      = Column(Float, nullable=True)
    max_drawdown      = Column(Float, nullable=True)   # %
    composite_score   = Column(Float, nullable=True)   # 优化目标综合分

    # 按时间窗口的详细结果
    window_results = Column(JSON, nullable=True)

    # 误差案例分析
    false_buy_patterns  = Column(JSON, nullable=True)
    false_sell_patterns = Column(JSON, nullable=True)

    records = relationship("BacktestRecord", back_populates="run")


class BacktestRecord(Base):
    __tablename__ = "backtest_records"

    id          = Column(Integer, primary_key=True, index=True)
    run_id      = Column(Integer, ForeignKey("backtest_runs.id"), nullable=False, index=True)
    stock_code  = Column(String(10), nullable=False)
    signal_date = Column(Date, nullable=False)
    signal      = Column(String(15), nullable=False)   # STRONG_BUY / BUY / SELL / STRONG_SELL（HOLD 不入回测）
    composite_score = Column(Float, nullable=True)
    entry_price  = Column(Float, nullable=True)
    exit_price   = Column(Float, nullable=True)
    hold_months  = Column(Integer, nullable=True)
    stock_return = Column(Float, nullable=True)        # 持有期收益率 %
    bench_return = Column(Float, nullable=True)        # 同期基准收益率 %
    excess_return = Column(Float, nullable=True)       # 超额收益 %
    is_win       = Column(Boolean, nullable=True)      # 是否跑赢基准

    # 用于误差分析
    sub_scores   = Column(JSON, nullable=True)
    news_summary = Column(Text, nullable=True)

    run = relationship("BacktestRun", back_populates="records")


# ──────────────────────────────────────────────
# 模拟盘（Paper Trading）
# ──────────────────────────────────────────────
# 设计：每个用户一个账户（user_id 唯一）
#   cash_balance 随交易实时扣加，positions/transactions 分两张表。
#   avg_cost 用加权平均法更新；卖出只动 shares，不动 avg_cost（直到清仓）。
class PaperAccount(Base):
    __tablename__ = "paper_account"

    id            = Column(Integer, primary_key=True)
    user_id       = Column(Integer, ForeignKey("users.id"),
                           nullable=True, index=True)
    name          = Column(String(50), default="模拟账户")
    initial_cash  = Column(Float, default=1_000_000.0)   # 初始资金 100 万
    cash_balance  = Column(Float, default=1_000_000.0)   # 当前现金
    created_at    = Column(DateTime, default=datetime.utcnow)
    reset_at      = Column(DateTime, default=datetime.utcnow)

    user          = relationship("User", back_populates="paper_accounts")
    positions     = relationship(
        "PaperPosition", back_populates="account", cascade="all, delete-orphan"
    )
    transactions  = relationship(
        "PaperTransaction", back_populates="account", cascade="all, delete-orphan"
    )


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    id          = Column(Integer, primary_key=True, index=True)
    account_id  = Column(Integer, ForeignKey("paper_account.id"), nullable=False, index=True)
    stock_code  = Column(String(10), ForeignKey("stocks.code"), nullable=False, index=True)
    shares      = Column(Float, default=0.0)           # 持股数
    avg_cost    = Column(Float, default=0.0)           # 加权平均成本
    opened_at   = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    account = relationship("PaperAccount", back_populates="positions")


class PaperTransaction(Base):
    __tablename__ = "paper_transactions"

    id          = Column(Integer, primary_key=True, index=True)
    account_id  = Column(Integer, ForeignKey("paper_account.id"), nullable=False, index=True)
    stock_code  = Column(String(10), ForeignKey("stocks.code"), nullable=False, index=True)
    stock_name  = Column(String(100), nullable=True)   # 冗余一份便于流水展示
    side        = Column(String(4), nullable=False)    # BUY / SELL
    shares      = Column(Float, nullable=False)
    price       = Column(Float, nullable=False)
    amount      = Column(Float, nullable=False)        # shares × price
    fee         = Column(Float, default=0.0)           # 手续费（万分之三）
    trade_time  = Column(DateTime, default=datetime.utcnow, index=True)
    note        = Column(Text, nullable=True)

    # 卖出时记录这一单的已实现盈亏
    realized_pnl = Column(Float, nullable=True)

    account = relationship("PaperAccount", back_populates="transactions")
