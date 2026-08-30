"""自选股 EMA20 跟随标签的单元测试。"""
import unittest
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import settings
from database import Base
from engines.watch_tag import (
    TAG_FOLLOW,
    TAG_HOLD,
    TAG_NO_DATA,
    TAG_STOP_LOSS,
    TAG_TAKE_PROFIT,
    _qfq_factor,
    classify_watch_tag,
    compute_ema,
    compute_volume_ratio,
    compute_watch_tags,
    load_cost_basis,
)
from models.models import PaperAccount, PaperPosition, PriceData, Stock, User


class TestComputeEma(unittest.TestCase):
    def test_不足周期返回None(self):
        self.assertIsNone(compute_ema([1.0] * 19, 20))
        self.assertIsNone(compute_ema([], 20))
        self.assertIsNone(compute_ema(None, 20))

    def test_恰好周期时等于SMA(self):
        # 种子就是前 period 根的 SMA，没有后续递推
        closes = [float(i) for i in range(1, 21)]     # 1..20，均值 10.5
        self.assertAlmostEqual(compute_ema(closes, 20), 10.5, places=9)

    def test_常数序列EMA等于该常数(self):
        self.assertAlmostEqual(compute_ema([7.0] * 60, 20), 7.0, places=9)

    def test_递推公式逐步核对(self):
        # 前 3 根做种子（SMA=2.0），再用 alpha=2/4=0.5 递推两步
        closes = [1.0, 2.0, 3.0, 10.0, 20.0]
        alpha = 2.0 / (3 + 1)
        expect = 2.0
        expect = 10.0 * alpha + expect * (1 - alpha)   # 6.0
        expect = 20.0 * alpha + expect * (1 - alpha)   # 13.0
        self.assertAlmostEqual(compute_ema(closes, 3), expect, places=9)
        self.assertAlmostEqual(compute_ema(closes, 3), 13.0, places=9)

    def test_EMA比SMA更贴近近期价格(self):
        # 前 20 根低位、后 20 根高位：EMA 应明显高于同期 SMA
        closes = [10.0] * 20 + [20.0] * 20
        ema = compute_ema(closes, 20)
        sma = sum(closes[-20:]) / 20
        self.assertGreater(ema, 10.0)
        self.assertLessEqual(ema, sma)


class TestComputeVolumeRatio(unittest.TestCase):
    def test_不足周期加一返回None(self):
        self.assertIsNone(compute_volume_ratio([100.0] * 20, 20))

    def test_均量不含当日(self):
        # 前 20 日均量 100，当日 300 → 3.0（若把当日算进分母会变成 ~2.6）
        vols = [100.0] * 20 + [300.0]
        self.assertAlmostEqual(compute_volume_ratio(vols, 20), 3.0, places=9)

    def test_均量为零返回None(self):
        self.assertIsNone(compute_volume_ratio([0.0] * 20 + [50.0], 20))

    def test_平量为1倍(self):
        self.assertAlmostEqual(compute_volume_ratio([100.0] * 21, 20), 1.0, places=9)


class TestClassifyWatchTag(unittest.TestCase):
    def test_无价或无EMA判数据不足(self):
        self.assertEqual(classify_watch_tag(None, 10.0, 1.0, None)["tag"], TAG_NO_DATA)
        self.assertEqual(classify_watch_tag(10.0, None, 1.0, None)["tag"], TAG_NO_DATA)

    def test_跌破均线判止损(self):
        r = classify_watch_tag(close=9.0, ema=10.0, vol_ratio=1.0, gain_pct=12.5, cost=8.0)
        self.assertEqual(r["tag"], TAG_STOP_LOSS)
        self.assertIsNone(r["tp_level"])

    def test_止损优先于止盈(self):
        # 已较成本涨 100%，但收盘跌破均线 —— 按规则「跌破均线则全部清仓」，走止损
        r = classify_watch_tag(close=9.0, ema=10.0, vol_ratio=5.0, gain_pct=100.0, cost=4.5)
        self.assertEqual(r["tag"], TAG_STOP_LOSS)

    def test_止损优先于放量跟进(self):
        r = classify_watch_tag(close=9.0, ema=10.0, vol_ratio=9.9, gain_pct=None, cost=None)
        self.assertEqual(r["tag"], TAG_STOP_LOSS)

    def test_恰好等于均线不算跌破(self):
        # close == ema 属于"站稳"，不该被判止损
        r = classify_watch_tag(close=10.0, ema=10.0, vol_ratio=1.0, gain_pct=None, cost=None)
        self.assertEqual(r["tag"], TAG_HOLD)

    def test_涨七成判止盈二档(self):
        r = classify_watch_tag(close=17.0, ema=10.0, vol_ratio=1.0, gain_pct=70.0, cost=10.0)
        self.assertEqual(r["tag"], TAG_TAKE_PROFIT)
        self.assertEqual(r["tp_level"], 2)

    def test_涨三成判止盈一档(self):
        r = classify_watch_tag(close=13.0, ema=10.0, vol_ratio=1.0, gain_pct=30.0, cost=10.0)
        self.assertEqual(r["tag"], TAG_TAKE_PROFIT)
        self.assertEqual(r["tp_level"], 1)

    def test_止盈档为闭区间(self):
        # 恰好 30% / 70% 应该触发（规则说"涨30%出一部分"，含等于）
        self.assertEqual(classify_watch_tag(13.0, 10.0, 1.0, 30.0, 10.0)["tp_level"], 1)
        self.assertEqual(classify_watch_tag(17.0, 10.0, 1.0, 70.0, 10.0)["tp_level"], 2)

    def test_止盈优先于放量跟进(self):
        # 涨到档了就该减仓，不该因为放量而提示加仓
        r = classify_watch_tag(close=13.0, ema=10.0, vol_ratio=9.9, gain_pct=30.0, cost=10.0)
        self.assertEqual(r["tag"], TAG_TAKE_PROFIT)

    def test_线上放量判可跟进(self):
        r = classify_watch_tag(close=11.0, ema=10.0,
                               vol_ratio=settings.WATCH_VOL_SPIKE_RATIO, gain_pct=5.0, cost=10.0)
        self.assertEqual(r["tag"], TAG_FOLLOW)

    def test_线上未放量判持有(self):
        r = classify_watch_tag(close=11.0, ema=10.0,
                               vol_ratio=settings.WATCH_VOL_SPIKE_RATIO - 0.1, gain_pct=5.0, cost=10.0)
        self.assertEqual(r["tag"], TAG_HOLD)

    def test_无成本价时跳过止盈档(self):
        # 不在模拟盘持仓 → 算不出涨幅，只在 持有/可跟进/止损 之间判
        r = classify_watch_tag(close=99.0, ema=10.0, vol_ratio=1.0, gain_pct=None, cost=None)
        self.assertEqual(r["tag"], TAG_HOLD)
        self.assertIsNone(r["tp_level"])

    def test_量比为None时不判可跟进(self):
        r = classify_watch_tag(close=11.0, ema=10.0, vol_ratio=None, gain_pct=None, cost=None)
        self.assertEqual(r["tag"], TAG_HOLD)
        self.assertIn("量能数据不足", r["reason"])

    def test_理由文案非空(self):
        for args in [(9.0, 10.0, 1.0, 12.5, 8.0), (13.0, 10.0, 1.0, 30.0, 10.0),
                     (11.0, 10.0, 2.0, 5.0, 10.0), (11.0, 10.0, 1.0, None, None),
                     (None, None, None, None, None)]:
            self.assertTrue(classify_watch_tag(*args)["reason"].strip())


class TestComputeWatchTagsDB(unittest.TestCase):
    """端到端：真的写库、真的批量算。"""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

        self.user = User(phone="13800000000", password_hash="x", name="t", is_active=True)
        self.db.add(self.user)
        self.db.commit()

        self.acct = PaperAccount(user_id=self.user.id, name="默认账户", cash_balance=100000.0)
        self.db.add(self.acct)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _add_prices(self, code: str, closes: list, volumes: list = None):
        self.db.add(Stock(code=code, name=code, is_active=True))
        base = date(2026, 1, 1)
        vols = volumes or [100.0] * len(closes)
        for i, (c, v) in enumerate(zip(closes, vols)):
            self.db.add(PriceData(stock_code=code, trade_date=base + timedelta(days=i),
                                  close=c, volume=v))
        self.db.commit()

    def test_线上无持仓判持有(self):
        self._add_prices("000001", [10.0] * 30 + [11.0])
        r = compute_watch_tags(self.db, ["000001"], self.user.id)
        self.assertEqual(r["000001"]["tag"], TAG_HOLD)
        self.assertTrue(r["000001"]["above_ema"])
        self.assertIsNone(r["000001"]["cost"])

    def test_跌破均线判止损(self):
        self._add_prices("000002", [10.0] * 30 + [8.0])
        r = compute_watch_tags(self.db, ["000002"], self.user.id)
        self.assertEqual(r["000002"]["tag"], TAG_STOP_LOSS)
        self.assertFalse(r["000002"]["above_ema"])

    def _seed_raw_price(self, code: str, price: float, date_str: str = None):
        """
        往 paper_trade 的实时价缓存里塞一个不复权价。

        止盈涨幅必须用不复权价与 avg_cost 比 —— 后复权的 price_data.close 与
        avg_cost 不同口径，混算会得出荒谬的涨幅（真实库上实测出过 +17022%）。
        """
        import time as _time

        from engines.paper_trade import _PRICE_CACHE
        _PRICE_CACHE[code] = (price, date_str or "2026-01-31", _time.time())
        self.addCleanup(_PRICE_CACHE.pop, code, None)

    def test_持仓成本带出止盈(self):
        # 后复权收盘 14.0，但不复权实时价 13.0 —— 涨幅必须按 13.0 算 = +30%
        self._add_prices("000003", [10.0] * 30 + [14.0])
        self.db.add(PaperPosition(account_id=self.acct.id, stock_code="000003",
                                  shares=100, avg_cost=10.0))
        self.db.commit()
        self._seed_raw_price("000003", 13.0)

        r = compute_watch_tags(self.db, ["000003"], self.user.id)
        self.assertEqual(r["000003"]["tag"], TAG_TAKE_PROFIT)
        self.assertEqual(r["000003"]["tp_level"], 1)
        self.assertAlmostEqual(r["000003"]["cost"], 10.0)
        self.assertAlmostEqual(r["000003"]["raw_price"], 13.0)
        self.assertEqual(r["000003"]["raw_price_date"], "2026-01-31")
        self.assertAlmostEqual(r["000003"]["gain_pct"], 30.0, places=1)
        self.assertTrue(r["000003"]["gain_available"])

    def test_涨幅用不复权价而非后复权收盘价(self):
        """这条是上面那个 bug 的回归测试：拿 close 去减 cost 会算出 +900%。"""
        self._add_prices("000009", [100.0] * 30 + [100.0])   # 后复权收盘 100
        self.db.add(PaperPosition(account_id=self.acct.id, stock_code="000009",
                                  shares=100, avg_cost=10.0))
        self.db.commit()
        self._seed_raw_price("000009", 10.5)                  # 不复权实时价 10.5

        r = compute_watch_tags(self.db, ["000009"], self.user.id)
        self.assertAlmostEqual(r["000009"]["gain_pct"], 5.0, places=1)
        self.assertNotEqual(r["000009"]["tag"], TAG_TAKE_PROFIT)
        self.assertEqual(r["000009"]["tag"], TAG_HOLD)

    def test_拿不到不复权价时不出止盈(self):
        """宁可不判，也不要退回后复权价硬算一个错数字。"""
        self._add_prices("000010", [10.0] * 30 + [99.0])
        self.db.add(PaperPosition(account_id=self.acct.id, stock_code="000010",
                                  shares=100, avg_cost=10.0))
        self.db.commit()
        # 故意不塞缓存

        r = compute_watch_tags(self.db, ["000010"], self.user.id)
        self.assertIsNone(r["000010"]["gain_pct"])
        self.assertIsNone(r["000010"]["raw_price"])
        self.assertFalse(r["000010"]["gain_available"])
        self.assertNotEqual(r["000010"]["tag"], TAG_TAKE_PROFIT)

    def test_距均线百分比与复权口径无关(self):
        """
        close/ema20 是后复权值（万科实测 678 元），不能直接显示给人看。
        above_ema_pct 是比值，整条序列同比例缩放后它不变 —— 前端只展示这个。
        """
        self._add_prices("000013", [10.0] * 30 + [11.0])
        self._add_prices("000014", [1000.0] * 30 + [1100.0])   # 同形态放大 100 倍
        r = compute_watch_tags(self.db, ["000013", "000014"], self.user.id)
        self.assertAlmostEqual(r["000013"]["above_ema_pct"],
                               r["000014"]["above_ema_pct"], places=6)
        self.assertGreater(r["000013"]["above_ema_pct"], 0)

    def test_跌破时距均线为负(self):
        self._add_prices("000015", [10.0] * 30 + [9.0])
        r = compute_watch_tags(self.db, ["000015"], self.user.id)
        self.assertLess(r["000015"]["above_ema_pct"], 0)

    def test_前复权换算后价格与成交价同量级(self):
        """
        万科真实场景：后复权收盘 526、EMA20 532，但真实成交价 3.11。
        页面上必须全部是前复权 —— 否则一张卡里 3.11 和 526 并存，看着像坏了。
        """
        # 30 根 500 + 末根 526，K 线末日 = 2026-01-30（base + 30 天）
        self._add_prices("000002", [500.0] * 30 + [526.0])
        last_date = str(date(2026, 1, 1) + timedelta(days=30))
        self._seed_raw_price("000002", 3.11, last_date)

        r = compute_watch_tags(self.db, ["000002"], self.user.id)["000002"]
        self.assertEqual(r["price_basis"], "qfq")
        # 末值必须恰好等于真实成交价
        self.assertAlmostEqual(r["close"], 3.11, places=2)
        # EMA20 落到同一量级（3 元附近），不再是 500 多
        self.assertLess(r["ema20"], 4.0)
        self.assertGreater(r["ema20"], 2.0)
        # 理由文案里也不能再出现后复权数字
        self.assertNotIn("526", r["tag_reason"])
        self.assertNotIn("532", r["tag_reason"])

    def test_前复权不改变判定结果(self):
        """因子是正数常量，整条序列同比例缩放，站上/跌破的结论必须完全一致。"""
        closes = [500.0] * 30 + [480.0]          # 跌破
        self._add_prices("000016", closes)
        last_date = str(date(2026, 1, 1) + timedelta(days=30))

        r_hfq = compute_watch_tags(self.db, ["000016"], self.user.id)["000016"]
        self.assertEqual(r_hfq["price_basis"], "hfq")     # 没实时价，不换算

        self._seed_raw_price("000016", 2.88, last_date)
        r_qfq = compute_watch_tags(self.db, ["000016"], self.user.id)["000016"]
        self.assertEqual(r_qfq["price_basis"], "qfq")

        self.assertEqual(r_hfq["tag"], r_qfq["tag"])
        self.assertEqual(r_hfq["above_ema"], r_qfq["above_ema"])
        self.assertAlmostEqual(r_hfq["above_ema_pct"], r_qfq["above_ema_pct"], places=6)

    def test_K线与实时价不同日则不换算(self):
        """行情没更新时因子会吸收掉这期间的涨跌，宁可不换算也不能算错。"""
        self._add_prices("000017", [500.0] * 30 + [526.0])
        self._seed_raw_price("000017", 3.11, "2030-12-31")   # 日期对不上

        r = compute_watch_tags(self.db, ["000017"], self.user.id)["000017"]
        self.assertEqual(r["price_basis"], "hfq")
        self.assertAlmostEqual(r["close"], 526.0, places=2)   # 保持后复权原值
        # 换算不成立时理由里不许出现绝对价格，只讲百分比
        self.assertNotIn("526", r["tag_reason"])
        self.assertIsNotNone(r["above_ema_pct"])

    def test_无持仓的票也带出成交价(self):
        """加了「最新价」列之后，没持仓的票也要显示成交价（早先只给有持仓的取）。"""
        self._add_prices("000012", [10.0] * 30 + [11.0])
        self._seed_raw_price("000012", 7.77)
        r = compute_watch_tags(self.db, ["000012"], self.user.id)
        self.assertAlmostEqual(r["000012"]["raw_price"], 7.77)
        self.assertIsNone(r["000012"]["cost"])        # 没持仓
        self.assertIsNone(r["000012"]["gain_pct"])    # 没成本就没涨幅

    def test_默认不发网络请求(self):
        """自选股是高频列表页，默认路径不许打 sina。"""
        from unittest.mock import patch

        self._add_prices("000011", [10.0] * 30 + [11.0])
        self.db.add(PaperPosition(account_id=self.acct.id, stock_code="000011",
                                  shares=100, avg_cost=10.0))
        self.db.commit()

        with patch("engines.paper_trade._warmup_prices_parallel") as warm:
            compute_watch_tags(self.db, ["000011"], self.user.id)
            warm.assert_not_called()
            compute_watch_tags(self.db, ["000011"], self.user.id, refresh_price=True)
            warm.assert_called_once()

    def test_放量判可跟进(self):
        closes = [10.0] * 30 + [11.0]
        vols = [100.0] * 30 + [500.0]
        self._add_prices("000004", closes, vols)
        r = compute_watch_tags(self.db, ["000004"], self.user.id)
        self.assertEqual(r["000004"]["tag"], TAG_FOLLOW)
        self.assertGreaterEqual(r["000004"]["vol_ratio"], settings.WATCH_VOL_SPIKE_RATIO)

    def test_无价格数据的股票仍出现在结果里(self):
        # 用户库里就有 4 只这样的票，不能让它们从列表里消失
        self.db.add(Stock(code="601377", name="x", is_active=True))
        self.db.commit()
        r = compute_watch_tags(self.db, ["601377"], self.user.id)
        self.assertIn("601377", r)
        self.assertEqual(r["601377"]["tag"], TAG_NO_DATA)
        self.assertIsNone(r["601377"]["ema20"])

    def test_K线不足20根判数据不足(self):
        self._add_prices("000005", [10.0] * 19)
        r = compute_watch_tags(self.db, ["000005"], self.user.id)
        self.assertEqual(r["000005"]["tag"], TAG_NO_DATA)

    def test_空代码列表不报错(self):
        self.assertEqual(compute_watch_tags(self.db, [], self.user.id), {})

    def test_批量与逐只结果一致(self):
        self._add_prices("000006", [10.0] * 30 + [12.0])
        self._add_prices("000007", [10.0] * 30 + [7.0])
        both = compute_watch_tags(self.db, ["000006", "000007"], self.user.id)
        one = compute_watch_tags(self.db, ["000006"], self.user.id)
        self.assertEqual(both["000006"]["tag"], one["000006"]["tag"])
        self.assertEqual(both["000006"]["ema20"], one["000006"]["ema20"])
        self.assertEqual(both["000007"]["tag"], TAG_STOP_LOSS)

    def test_价格陈旧时仍能算(self):
        # price_data 落后现实日期几个月（用户库当前就是这个状态）：
        # 回溯窗口以"最新交易日"为基准，不能以 today 为基准，否则会筛空
        self.db.add(Stock(code="000008", name="x", is_active=True))
        old = date(2020, 1, 1)
        for i in range(30):
            self.db.add(PriceData(stock_code="000008", trade_date=old + timedelta(days=i),
                                  close=10.0, volume=100.0))
        self.db.add(PriceData(stock_code="000008", trade_date=old + timedelta(days=30),
                              close=12.0, volume=100.0))
        self.db.commit()
        r = compute_watch_tags(self.db, ["000008"], self.user.id)
        self.assertEqual(r["000008"]["tag"], TAG_HOLD)
        self.assertIsNotNone(r["000008"]["ema20"])


class TestQfqFactor(unittest.TestCase):
    def test_同日才成立(self):
        self.assertAlmostEqual(_qfq_factor(500.0, date(2026, 8, 27), 10.0, "2026-08-27"),
                               0.02, places=9)

    def test_日期不同返回None(self):
        # K 线陈旧时，因子会把这期间的涨跌当成除权，算出来是错的 —— 必须拒绝
        self.assertIsNone(_qfq_factor(500.0, date(2026, 5, 6), 10.0, "2026-08-27"))

    def test_带时分秒的日期也能对上(self):
        self.assertIsNotNone(_qfq_factor(500.0, date(2026, 8, 27), 10.0, "2026-08-27 00:00:00"))

    def test_缺值返回None(self):
        self.assertIsNone(_qfq_factor(None, date(2026, 8, 27), 10.0, "2026-08-27"))
        self.assertIsNone(_qfq_factor(500.0, date(2026, 8, 27), None, "2026-08-27"))
        self.assertIsNone(_qfq_factor(0.0, date(2026, 8, 27), 10.0, "2026-08-27"))


class TestLoadCostBasis(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.user = User(phone="13800000001", password_hash="x", is_active=True)
        self.db.add(self.user)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_无账户返回空(self):
        self.assertEqual(load_cost_basis(self.db, self.user.id), {})

    def test_不建账户(self):
        load_cost_basis(self.db, self.user.id)
        self.assertEqual(self.db.query(PaperAccount).count(), 0)

    def test_默认取最早账户(self):
        a1 = PaperAccount(user_id=self.user.id, name="早期", cash_balance=1.0)
        a2 = PaperAccount(user_id=self.user.id, name="新的", cash_balance=1.0)
        self.db.add_all([a1, a2])
        self.db.commit()
        self.db.add(PaperPosition(account_id=a1.id, stock_code="AAA", shares=100, avg_cost=5.0))
        self.db.add(PaperPosition(account_id=a2.id, stock_code="BBB", shares=100, avg_cost=9.0))
        self.db.commit()
        self.assertEqual(load_cost_basis(self.db, self.user.id), {"AAA": 5.0})
        self.assertEqual(load_cost_basis(self.db, self.user.id, a2.id), {"BBB": 9.0})

    def test_清仓持仓不计入(self):
        a = PaperAccount(user_id=self.user.id, name="x", cash_balance=1.0)
        self.db.add(a)
        self.db.commit()
        self.db.add(PaperPosition(account_id=a.id, stock_code="CCC", shares=0, avg_cost=5.0))
        self.db.commit()
        self.assertEqual(load_cost_basis(self.db, self.user.id), {})


if __name__ == "__main__":
    unittest.main()
