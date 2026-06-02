"""compute_price_pctile_life 的单元测试（上市以来总收益分位）。"""
import unittest
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.models import Stock, PriceData
from engines.price_position import compute_price_pctile_life


class TestPricePctileLife(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def _add_stock(self, code, closes, active=True):
        """closes 按日期升序；最后一个为最新收盘。"""
        self.db.add(Stock(code=code, name=code, market="A", is_active=active))
        d0 = date(2024, 1, 1)
        for i, c in enumerate(closes):
            self.db.add(PriceData(stock_code=code, trade_date=d0 + timedelta(days=i), close=c))
        self.db.commit()

    def test_percentile_of_latest_close(self):
        # closes=[10,20,30,15]，最新=15 → mean(<=15)=2/4=0.5
        self._add_stock("000001", [10, 20, 30, 15])
        n = compute_price_pctile_life(self.db, min_history=4)
        self.assertEqual(n, 1)
        s = self.db.query(Stock).filter_by(code="000001").first()
        self.assertAlmostEqual(s.price_pctile_life, 0.5, places=4)

    def test_latest_at_alltime_high_is_one(self):
        # 最新即历史最高 → 分位=1.0
        self._add_stock("000002", [10, 20, 30, 40])
        compute_price_pctile_life(self.db, min_history=4)
        s = self.db.query(Stock).filter_by(code="000002").first()
        self.assertAlmostEqual(s.price_pctile_life, 1.0, places=4)

    def test_insufficient_history_left_none(self):
        # 行数 < min_history → 不计算，保持 None
        self._add_stock("000003", [10, 20])
        n = compute_price_pctile_life(self.db, min_history=60)
        self.assertEqual(n, 0)
        s = self.db.query(Stock).filter_by(code="000003").first()
        self.assertIsNone(s.price_pctile_life)

    def test_inactive_stock_skipped(self):
        self._add_stock("000004", [10, 20, 30, 40], active=False)
        compute_price_pctile_life(self.db, min_history=4)
        s = self.db.query(Stock).filter_by(code="000004").first()
        self.assertIsNone(s.price_pctile_life)


if __name__ == "__main__":
    unittest.main()
