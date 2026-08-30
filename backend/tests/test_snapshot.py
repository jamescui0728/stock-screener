"""快照浅增量的标定与安全闸门测试。

重点不是"能不能补上"，而是**该拒绝时会不会拒绝** —— 这条路径直接写 price_data，
写错了会污染 EMA / MACD / 缺口三套规则的输入。
"""
import unittest
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data.fetcher import (
    SNAPSHOT_MAX_PRICE_ERR_PCT,
    SNAPSHOT_MIN_CALIB,
    _recent_trading_dates,
    calibrate_snapshot,
)
from database import Base
from models.models import PriceData


def _snap(close, prev_close, volume=1000.0, **kw):
    d = {"open": close, "high": close, "low": close, "close": close,
         "prev_close": prev_close, "volume": volume, "turnover": 1.0}
    d.update(kw)
    return d


class TestCalibrateSnapshot(unittest.TestCase):
    def test_空标定集(self):
        r = calibrate_snapshot({}, {})
        self.assertEqual(r["n"], 0)
        self.assertIsNone(r["vol_ratio"])
        self.assertIsNone(r["price_err_pct"])

    def test_理想情况误差为零(self):
        """
        构造：某股不复权 昨收 10 / 今收 11，后复权同一天是 20 / 22（因子恒为 2）。
        用「因子 = 前日hfq收 / 快照昨收」推出的预测收盘应当精确等于已知的 22。
        """
        snapshot = {"000001": _snap(close=11.0, prev_close=10.0, volume=500.0)}
        known    = {"000001": (20.0, 22.0, 50000.0)}   # (前日hfq收, 当日真实hfq收, 当日真实volume)
        r = calibrate_snapshot(snapshot, known)
        self.assertEqual(r["n"], 1)
        self.assertAlmostEqual(r["price_err_pct"], 0.0, places=9)
        self.assertAlmostEqual(r["vol_ratio"], 100.0, places=9)   # 50000 股 / 500 手

    def test_成交量单位比实测反推而非硬编码(self):
        """上游把单位从「手」改成「股」时，vol_ratio 应当变成 1 而不是仍按 100 算。"""
        snapshot = {c: _snap(11.0, 10.0, volume=50000.0) for c in ("a", "b", "c")}
        known    = {c: (20.0, 22.0, 50000.0) for c in ("a", "b", "c")}
        self.assertAlmostEqual(calibrate_snapshot(snapshot, known)["vol_ratio"], 1.0)

    def test_取中位数抗单点异常(self):
        snapshot = {c: _snap(11.0, 10.0, volume=500.0) for c in ("a", "b", "c")}
        known = {"a": (20.0, 22.0, 50000.0),
                 "b": (20.0, 22.0, 50000.0),
                 "c": (20.0, 22.0, 999999.0)}   # 离群点
        self.assertAlmostEqual(calibrate_snapshot(snapshot, known)["vol_ratio"], 100.0)

    def test_除权导致的误差会被量化出来(self):
        """
        当日除权：真实 hfq 收盘不等于「昨收因子 × 今收」。
        标定函数必须把这个偏差如实报出来，好让上层据此中止。
        """
        snapshot = {"000001": _snap(close=11.0, prev_close=10.0)}
        known    = {"000001": (20.0, 24.2, 1000.0)}   # 预测 22，真实 24.2
        r = calibrate_snapshot(snapshot, known)
        # 标准相对误差 |22 - 24.2| / 24.2 = 9.09%
        self.assertAlmostEqual(r["price_err_pct"], 100 * 2.2 / 24.2, places=6)
        self.assertGreater(r["price_err_pct"], SNAPSHOT_MAX_PRICE_ERR_PCT)

    def test_缺字段的股票不计入(self):
        snapshot = {"a": _snap(11.0, None), "b": _snap(11.0, 10.0)}
        known    = {"a": (20.0, 22.0, 1000.0), "b": (20.0, 22.0, 1000.0)}
        self.assertEqual(calibrate_snapshot(snapshot, known)["n"], 1)

    def test_快照里没有的股票不计入(self):
        r = calibrate_snapshot({}, {"a": (20.0, 22.0, 1000.0)})
        self.assertEqual(r["n"], 0)
        self.assertEqual(r["samples"], 1)

    def test_阈值常量是保守的(self):
        # 纯比例换算在同一天内应当几乎无误差，阈值不该被放宽到能放过除权
        self.assertLessEqual(SNAPSHOT_MAX_PRICE_ERR_PCT, 1.0)
        self.assertGreaterEqual(SNAPSHOT_MIN_CALIB, 10)


class TestRecentTradingDates(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def test_从已有数据反推交易日历(self):
        # 周末不写数据 —— 反推出的序列里自然就没有周末
        for d in [date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26),
                  date(2026, 8, 27), date(2026, 8, 28)]:
            self.db.add(PriceData(stock_code="000001", trade_date=d, close=10.0, volume=1.0))
        self.db.commit()
        got = _recent_trading_dates(self.db)
        self.assertEqual(got[-1], date(2026, 8, 28))
        self.assertEqual(got[-2], date(2026, 8, 27))
        self.assertEqual(len(got), 5)

    def test_升序返回(self):
        for i in range(5):
            self.db.add(PriceData(stock_code="x", trade_date=date(2026, 8, 24) + timedelta(days=i),
                                  close=1.0, volume=1.0))
        self.db.commit()
        got = _recent_trading_dates(self.db)
        self.assertEqual(got, sorted(got))

    def test_多股票去重(self):
        for code in ("a", "b", "c"):
            for i in range(3):
                self.db.add(PriceData(stock_code=code, trade_date=date(2026, 8, 26) + timedelta(days=i),
                                      close=1.0, volume=1.0))
        self.db.commit()
        self.assertEqual(len(_recent_trading_dates(self.db)), 3)

    def test_空库返回空(self):
        self.assertEqual(_recent_trading_dates(self.db), [])


if __name__ == "__main__":
    unittest.main()
