"""短期信号风控逻辑的单元测试。"""
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from auto_migrate import run_auto_migrations
from config import settings
from database import Base
from engines.short_signal_engine import (
    MARKET_TREND_BLOCK_HINT,
    _apply_cross_sectional_ranks,
    classify_short_signal,
    compute_industry_news_heat,
    score_market_trend,
    score_news_heat,
    short_signal_blocked_by_market,
)
from models.models import NewsItem, PriceData, Stock


class TestClassifyShortSignal(unittest.TestCase):
    def test_crash_5d_forces_strong_sell(self):
        sig = classify_short_signal(80.0, -11.0, {"pass": True})
        self.assertEqual(sig, "STRONG_SELL")

    def test_crash_5d_at_threshold(self):
        sig = classify_short_signal(80.0, -10.0, {"pass": True})
        self.assertEqual(sig, "STRONG_SELL")

    def test_high_composite_blocked_by_weak_market(self):
        sig = classify_short_signal(75.0, 2.0, {"pass": False})
        self.assertEqual(sig, "HOLD")

    def test_buy_when_market_passes(self):
        sig = classify_short_signal(73.0, 1.0, {"pass": True})
        self.assertEqual(sig, "STRONG_BUY")

    def test_hold_band_without_market_block(self):
        sig = classify_short_signal(40.0, 0.0, {"pass": False})
        self.assertEqual(sig, "HOLD")


class TestObserveCandidate(unittest.TestCase):
    def test_blocked_by_market_reason(self):
        reason = f"短期信号：综合分 73。→ 观望（{MARKET_TREND_BLOCK_HINT}）。"
        self.assertTrue(short_signal_blocked_by_market("HOLD", reason))

    def test_plain_hold_not_observe(self):
        self.assertFalse(short_signal_blocked_by_market("HOLD", "→ 观望。"))


class TestCrossSectionalRankCrashVeto(unittest.TestCase):
    """ranked 路径也必须触发 5 日急跌 veto（回归 Bugbot: Ranking skips crash veto）。"""

    def _result(self, ret_5d_pct):
        # sub_scores 给满足高分的占位；details.momentum.ret_5d 控制 veto
        sub = {
            "momentum": 80.0, "volprice": 80.0, "tech": 80.0,
            "industry_relative": 80.0, "macro": 80.0,
            "news_heat": 50.0, "pricing_power": 50.0,
            "market_trend": 100.0,
        }
        return {
            "short_composite_score": 80.0,
            "short_signal": "STRONG_BUY",
            "short_signal_reason": "",
            "sub_scores": dict(sub),
            "details": {"momentum": {"ret_5d": ret_5d_pct}},
        }

    def test_crash_stock_becomes_strong_sell_after_ranking(self):
        raw = {
            "CRASH": self._result(-12.0),   # 5 日急跌 → 必须 STRONG_SELL
            "OKAY":  self._result(1.0),      # 正常 → 不被 veto
        }
        ranked = _apply_cross_sectional_ranks(raw)
        self.assertEqual(ranked["CRASH"]["short_signal"], "STRONG_SELL")
        self.assertNotEqual(ranked["OKAY"]["short_signal"], "STRONG_SELL")


class TestScoreMarketTrend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)
        cls.bench = "IDX_000300"

    def _ensure_bench_stock(self, db):
        if not db.query(Stock).filter_by(code=self.bench).first():
            db.add(Stock(code=self.bench, name="沪深300", is_active=True))
            db.commit()

    def test_insufficient_benchmark_data_fail_closed(self):
        db = self.Session()
        try:
            m = score_market_trend(db, date(2024, 6, 1))
            self.assertFalse(m["pass"])
            self.assertFalse(m.get("data_ok", True))
        finally:
            db.close()

    def test_weak_trend_fails(self):
        db = self.Session()
        try:
            self._ensure_bench_stock(db)
            base = date(2024, 1, 1)
            for i in range(21):
                db.add(PriceData(
                    stock_code=self.bench,
                    trade_date=base + timedelta(days=i),
                    close=100.0 - i * 0.25,
                    volume=1e6,
                ))
            db.commit()
            m = score_market_trend(db, base + timedelta(days=20))
            self.assertTrue(m.get("data_ok"))
            self.assertFalse(m["pass"])
        finally:
            db.close()

    def test_warm_trend_passes(self):
        db = self.Session()
        try:
            self._ensure_bench_stock(db)
            base = date(2024, 3, 1)
            for i in range(25):
                db.add(PriceData(
                    stock_code=self.bench,
                    trade_date=base + timedelta(days=i),
                    close=100.0 + i * 0.5,
                    volume=1e6,
                ))
            db.commit()
            m = score_market_trend(db, base + timedelta(days=24))
            self.assertTrue(m["pass"])
        finally:
            db.close()


class TestAutoFollowCaps(unittest.TestCase):
    def test_max_open_positions_config(self):
        self.assertGreaterEqual(getattr(settings, "AUTO_FOLLOW_MAX_OPEN_POSITIONS", 0), 1)


class TestAutoMigrate(unittest.TestCase):
    def test_partial_index_is_created(self):
        engine = create_engine("sqlite:///:memory:")
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            db.execute(text("CREATE TABLE stocks (id INTEGER PRIMARY KEY)"))
            db.execute(text("CREATE TABLE backtest_runs (id INTEGER PRIMARY KEY)"))
            db.execute(text("CREATE TABLE backtest_records (id INTEGER PRIMARY KEY)"))
            db.execute(text(
                "CREATE TABLE price_data ("
                "id INTEGER PRIMARY KEY, stock_code VARCHAR(20), trade_date DATE)"
            ))
            db.execute(text(
                "CREATE TABLE news_items ("
                "id INTEGER PRIMARY KEY, stock_code VARCHAR(20), pub_date DATETIME)"
            ))
            db.execute(text(
                "CREATE TABLE paper_account ("
                "id INTEGER PRIMARY KEY, user_id INTEGER, name VARCHAR(50))"
            ))
            db.commit()

            result = run_auto_migrations(db)
            rows = db.execute(text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name='ix_paper_account_system_name'"
            )).fetchall()

            self.assertEqual(len(rows), 1)
            self.assertIn("ix_paper_account_system_name", result["added_indexes"])
        finally:
            db.close()


class TestSelectTradeableShortResults(unittest.TestCase):
    """验证 _select_tradeable_short_results 的同日 BUY 限额与 STRONG_BUY 优先逻辑。"""

    def setUp(self):
        from backtest.engine import _select_tradeable_short_results
        self._fn = _select_tradeable_short_results

    def _r(self, signal, score):
        return {"short_signal": signal, "short_composite_score": score}

    def test_strong_buy_prioritized_over_buy(self):
        """STRONG_BUY 应排在 BUY 前面，即使评分更低。"""
        raw = {
            "A": self._r("BUY", 75.0),
            "B": self._r("STRONG_BUY", 73.5),   # 低于 A，但是 STRONG_BUY
            "C": self._r("BUY", 76.0),
        }
        with patch.object(settings, "SHORT_MAX_BUY_PER_CHECK_DATE", 1):
            result = self._fn(raw)
        buy_codes = {code for code, d in result if d["short_signal"] in ("BUY", "STRONG_BUY")}
        self.assertIn("B", buy_codes)
        self.assertNotIn("A", buy_codes)
        self.assertNotIn("C", buy_codes)

    def test_buy_cap_applied(self):
        """超出 cap 的 BUY 信号应被截断。"""
        raw = {str(i): self._r("BUY", 74.0 + i * 0.1) for i in range(10)}
        with patch.object(settings, "SHORT_MAX_BUY_PER_CHECK_DATE", 3):
            result = self._fn(raw)
        buy_codes = [code for code, d in result if d["short_signal"] == "BUY"]
        self.assertEqual(len(buy_codes), 3)

    def test_sell_signals_not_capped(self):
        """SELL 信号不应被 BUY 上限截断，全部保留。"""
        raw = {f"S{i}": self._r("SELL", 30.0) for i in range(8)}
        raw.update({f"B{i}": self._r("BUY", 74.0) for i in range(8)})
        with patch.object(settings, "SHORT_MAX_BUY_PER_CHECK_DATE", 2):
            result = self._fn(raw)
        sell_codes = [code for code, d in result if d["short_signal"] == "SELL"]
        buy_codes  = [code for code, d in result if d["short_signal"] == "BUY"]
        self.assertEqual(len(sell_codes), 8)
        self.assertEqual(len(buy_codes), 2)

    def test_no_cap_when_zero(self):
        """SHORT_MAX_BUY_PER_CHECK_DATE = 0 时不限制。"""
        raw = {str(i): self._r("BUY", 74.0) for i in range(10)}
        with patch.object(settings, "SHORT_MAX_BUY_PER_CHECK_DATE", 0):
            result = self._fn(raw)
        buy_codes = [code for code, d in result if d["short_signal"] == "BUY"]
        self.assertEqual(len(buy_codes), 10)


class TestAutoFollowShareCalculation(unittest.TestCase):
    """验证 _shares_for_target_amount 的整手计算逻辑。"""

    def setUp(self):
        from engines.auto_follow import _shares_for_target_amount
        self._fn = _shares_for_target_amount

    def test_rounds_down_to_hundred_lot(self):
        """结果必须是 100 的整数倍，且不超出预算。"""
        shares = self._fn(10.5, 50_000)
        self.assertEqual(shares % 100, 0)
        self.assertLessEqual(shares * 10.5, 50_000)
        self.assertGreater(shares, 0)

    def test_zero_price_returns_zero(self):
        self.assertEqual(self._fn(0.0, 50_000), 0)

    def test_negative_price_returns_zero(self):
        self.assertEqual(self._fn(-5.0, 50_000), 0)

    def test_high_price_still_rounds_correctly(self):
        """单价很高时（如 200 元），结果仍是 100 整数倍且不超预算。"""
        shares = self._fn(200.0, 50_000)
        self.assertEqual(shares % 100, 0)
        self.assertLessEqual(shares * 200.0, 50_000)


class TestAutoFollowSkipLogic(unittest.TestCase):
    """验证 run_v202g_auto_follow 的跳过逻辑（已持仓 / 现金不足）。"""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    def _make_stock(self, db, code, signal="BUY", score=75.0):
        if not db.query(Stock).filter_by(code=code).first():
            db.add(Stock(code=code, name=f"测试{code}", is_active=True,
                         short_signal=signal, short_composite_score=score))
            db.commit()

    def test_already_held_stock_is_skipped(self):
        """已有持仓的股票不应重复买入。"""
        from models.models import PaperPosition
        from engines.auto_follow import run_v202g_auto_follow, get_or_create_auto_account

        db = self.Session()
        try:
            self._make_stock(db, "TEST01")
            acct = get_or_create_auto_account(db)

            # 注入一条现有持仓
            if not db.query(PaperPosition).filter_by(
                account_id=acct.id, stock_code="TEST01"
            ).first():
                db.add(PaperPosition(
                    account_id=acct.id, stock_code="TEST01",
                    shares=100, avg_cost=10.0,
                ))
                db.commit()

            # mock _get_latest_price 返回有效价格，避免调用 akshare
            with patch("engines.auto_follow._get_latest_price", return_value=10.0):
                result = run_v202g_auto_follow(db)

            skipped_codes = {s["code"] for s in result["skipped"]}
            bought_codes  = {b["code"] for b in result["bought"]}
            self.assertIn("TEST01", skipped_codes)
            self.assertNotIn("TEST01", bought_codes)
        finally:
            db.close()

    def test_cash_shortage_causes_skip(self):
        """现金不足时应跳过买入，不应报错。"""
        from engines.auto_follow import run_v202g_auto_follow, get_or_create_auto_account
        from models.models import PaperAccount

        db = self.Session()
        try:
            self._make_stock(db, "TEST02")
            acct = get_or_create_auto_account(db)

            # 把账户余额清零
            acct.cash_balance = 0.0
            db.commit()

            with patch("engines.auto_follow._get_latest_price", return_value=10.0):
                result = run_v202g_auto_follow(db)

            skipped_reasons = {s["reason"] for s in result["skipped"]}
            bought_codes    = {b["code"] for b in result["bought"]}
            # TEST02 应因现金不足而跳过（reason 含 cash_short）
            self.assertTrue(
                any("cash_short" in r for r in skipped_reasons),
                f"Expected cash_short in skipped reasons, got: {skipped_reasons}",
            )
            self.assertNotIn("TEST02", bought_codes)

            # 恢复余额，避免影响其他测试
            acct.cash_balance = settings.AUTO_FOLLOW_INITIAL_CASH
            db.commit()
        finally:
            db.close()


class TestAutoFollowReadOnlyGet(unittest.TestCase):
    """只读 GET 路径不应建账户（回归 issue #5: write-on-GET）。"""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    def test_get_performance_empty_db_creates_no_account(self):
        from engines.auto_follow import get_performance, _get_auto_account
        from models.models import PaperAccount

        db = self.Session()
        try:
            self.assertIsNone(_get_auto_account(db))
            perf = get_performance(db)
            self.assertIsNone(perf["account_id"])
            self.assertEqual(perf["n_open"], 0)
            self.assertEqual(perf["positions"], [])
            # 关键：读路径没有写库建账户
            self.assertEqual(db.query(PaperAccount).count(), 0)
        finally:
            db.close()


class TestNewsHeatScoring(unittest.TestCase):
    """阶段1：4 类舆情特征（热度激增 / 情感方向 / 事件 veto / 板块联动）。"""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.asof = date(2026, 5, 20)

    def tearDown(self):
        self.db.close()

    def _stock(self, code, ind="BK0475"):
        self.db.add(Stock(code=code, name=code, is_active=True, industry_code=ind))
        self.db.commit()

    def _news(self, code, days_ago, sent, event=None, ind="BK0475"):
        pub = datetime.combine(self.asof, datetime.min.time()) - timedelta(days=days_ago)
        self.db.add(NewsItem(
            stock_code=code, industry_code=ind, pub_date=pub,
            title=f"{code} news", sentiment_score=sent, event_type=event,
        ))
        self.db.commit()

    def test_no_news_returns_neutral(self):
        self._stock("A1")
        r = score_news_heat(self.db, "A1", self.asof)
        self.assertEqual(r["score"], 50.0)
        self.assertFalse(r["veto"])

    def test_positive_spike_pushes_bullish(self):
        """近7日多条正面新闻、前期无 → 激增 + 正情感 → 明显 > 50。"""
        self._stock("A2")
        for d in range(1, 6):
            self._news("A2", d, 0.8, event="订单合作")
        r = score_news_heat(self.db, "A2", self.asof)
        self.assertGreater(r["score"], 65)
        self.assertGreater(r["spike"], 1.0)

    def test_veto_event_caps_score_low(self):
        """退市风险 + 强负面情感 → 硬性压到 <= 15。"""
        self._stock("A3")
        self._news("A3", 1, -0.9, event="退市风险")
        self._news("A3", 2, 0.5, event="订单合作")   # 即使有正面也被 veto
        r = score_news_heat(self.db, "A3", self.asof)
        self.assertLessEqual(r["score"], 15.0)
        self.assertTrue(r["veto"])

    def test_industry_news_heat_and_sector_boost(self):
        """板块整体正面 → 个股获得 sector 加成。"""
        # 同行业多只股票的正面新闻，撑起行业热度
        for code in ("A4", "B1", "B2"):
            self.db.add(Stock(code=code, name=code, is_active=True, industry_code="BK0475"))
        self.db.commit()
        for code in ("A4", "B1", "B2"):
            for d in range(1, 12):
                self._news(code, d, 0.7, event="产品技术")
        ind_news = compute_industry_news_heat(self.db, self.asof)
        self.assertIn("BK0475", ind_news)
        self.assertGreater(ind_news["BK0475"]["score"], 50)
        # 带板块缓存 vs 不带，分数应更高（或相等）
        base = score_news_heat(self.db, "A4", self.asof)
        boosted = score_news_heat(self.db, "A4", self.asof, _cached_industry_news=ind_news)
        self.assertGreaterEqual(boosted["score"], base["score"])


class TestNewsObserveDoesNotAffectComposite(unittest.TestCase):
    """观察模式契约：weight=0 时即使 news_heat 被 veto 压到 15，composite/signal 也不变。"""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    def _seed_prices(self, db, code, days=30):
        base = date(2024, 1, 1)
        for i in range(days):
            db.add(PriceData(stock_code=code, trade_date=base + timedelta(days=i),
                             open=10 + i * 0.1, close=10 + i * 0.1, volume=1_000_000))
        db.commit()

    def test_observe_news_does_not_change_composite(self):
        from datetime import datetime
        from engines.short_signal_engine import generate_short_signal
        from models.models import NewsItem

        db = self.Session()
        try:
            db.add(Stock(code="NEWS01", name="测试", is_active=True, industry_code="BK0001"))
            self._seed_prices(db, "NEWS01")
            # 强负面监管新闻 → veto → news_heat 被压到 <=15
            db.add(NewsItem(stock_code="NEWS01", title="公司被证监会立案调查",
                            summary="涉嫌财务造假 被立案", pub_date=datetime(2024, 1, 29),
                            sentiment_score=-0.9, event_type="监管"))
            db.commit()

            common = dict(
                as_of_date=date(2024, 1, 30), write_back=False, commit=False,
                _cached_macro_100=50.0, _cached_market_trend={"pass": True},
                _cached_industry_returns={}, _cached_industries={},
            )
            # 观察模式开（线上默认，不跳过）：news_heat 被算出且 veto 压低，但 weight=0
            with patch.object(settings, "SHORT_NEWS_HEAT_WEIGHT", 0.0), \
                 patch.object(settings, "SHORT_NEWS_OBSERVE", True):
                r_obs = generate_short_signal(db, "NEWS01", **common)
            self.assertIsNotNone(r_obs)
            self.assertLessEqual(r_obs["sub_scores"]["news_heat"], 15.0)  # 确实算了且被 veto

            # 对照：回测路径跳过 observe（_skip_news_observe=True）→ news_heat 中性 50
            with patch.object(settings, "SHORT_NEWS_HEAT_WEIGHT", 0.0), \
                 patch.object(settings, "SHORT_NEWS_OBSERVE", True):
                r_off = generate_short_signal(db, "NEWS01", _skip_news_observe=True, **common)
            self.assertEqual(r_off["sub_scores"]["news_heat"], 50.0)  # 跳过 → 中性

            # 核心契约：weight=0 → 不同 news_heat 不影响 composite / signal
            self.assertEqual(r_obs["short_composite_score"], r_off["short_composite_score"])
            self.assertEqual(r_obs["short_signal"], r_off["short_signal"])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
