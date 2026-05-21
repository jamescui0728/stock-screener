"""短期信号风控逻辑的单元测试。"""
import unittest
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import settings
from database import Base
from engines.short_signal_engine import (
    MARKET_TREND_BLOCK_HINT,
    classify_short_signal,
    score_market_trend,
    short_signal_blocked_by_market,
)
from models.models import PriceData, Stock


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


if __name__ == "__main__":
    unittest.main()
