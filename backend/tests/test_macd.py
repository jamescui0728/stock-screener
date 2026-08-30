"""MACD 零轴上方回踩金叉的单元测试。"""
import unittest

from config import settings
from engines.macd import (
    MIN_BARS,
    compute_dif_series,
    compute_macd,
    evaluate_macd,
    is_zero_axis_pullback_cross,
)
from engines.watch_tag import compute_ema


class TestComputeDifSeries(unittest.TestCase):
    def test_不足慢线周期返回空(self):
        self.assertEqual(compute_dif_series([1.0] * 25, 12, 26), [])
        self.assertEqual(compute_dif_series([], 12, 26), [])

    def test_长度与末尾对齐(self):
        closes = [float(i) for i in range(1, 41)]     # 40 根
        difs = compute_dif_series(closes, 12, 26)
        self.assertEqual(len(difs), 40 - 26 + 1)

    def test_逐点与compute_ema一致(self):
        closes = [float(i) for i in range(1, 41)]
        difs = compute_dif_series(closes, 12, 26)
        # 末点 DIF 必须等于 EMA12(全序列) - EMA26(全序列)
        self.assertAlmostEqual(
            difs[-1], compute_ema(closes, 12) - compute_ema(closes, 26), places=9)

    def test_常数序列DIF恒为零(self):
        difs = compute_dif_series([50.0] * 60, 12, 26)
        for d in difs:
            self.assertAlmostEqual(d, 0.0, places=9)

    def test_单调上涨DIF为正(self):
        # 快线跟得更紧，上涨中 EMA12 > EMA26
        closes = [float(i) for i in range(1, 61)]
        self.assertGreater(compute_dif_series(closes, 12, 26)[-1], 0)

    def test_单调下跌DIF为负(self):
        closes = [float(i) for i in range(60, 0, -1)]
        self.assertLess(compute_dif_series(closes, 12, 26)[-1], 0)


class TestComputeMacd(unittest.TestCase):
    def test_K线不足返回None(self):
        self.assertIsNone(compute_macd([10.0] * (MIN_BARS - 1)))
        self.assertIsNone(compute_macd([]))

    def test_够根数就能算(self):
        m = compute_macd([float(i) for i in range(1, 80)])
        self.assertIsNotNone(m)
        for k in ("dif", "dea", "hist", "prev_dif", "prev_dea"):
            self.assertIn(k, m)

    def test_柱等于两倍dif减dea(self):
        m = compute_macd([float(i) for i in range(1, 80)])
        self.assertAlmostEqual(m["hist"], 2 * (m["dif"] - m["dea"]), places=9)

    def test_常数序列各项归零(self):
        m = compute_macd([50.0] * 80)
        self.assertAlmostEqual(m["dif"], 0.0, places=9)
        self.assertAlmostEqual(m["dea"], 0.0, places=9)
        self.assertAlmostEqual(m["hist"], 0.0, places=9)

    def test_自定义参数生效(self):
        closes = [float(i) for i in range(1, 80)]
        a = compute_macd(closes, fast=5, slow=10, signal=3)
        b = compute_macd(closes, fast=12, slow=26, signal=9)
        self.assertNotAlmostEqual(a["dif"], b["dif"], places=6)


class TestIsZeroAxisPullbackCross(unittest.TestCase):
    """四个条件逐个单独破坏，确认每个都是必要条件。"""

    BASE = {"dif": 1.0, "dea": 0.5, "prev_dif": 0.4, "prev_dea": 0.6}

    def test_四条件齐备则命中(self):
        self.assertTrue(is_zero_axis_pullback_cross(dict(self.BASE)))

    def test_DIF在零轴下不命中(self):
        m = dict(self.BASE, dif=-1.0, dea=-2.0, prev_dif=-3.0, prev_dea=-2.5)
        self.assertFalse(is_zero_axis_pullback_cross(m))

    def test_DEA在零轴下不命中(self):
        # DIF 已上零轴但 DEA 还在下面 —— 这是零轴下方的金叉，规则明确不做
        m = dict(self.BASE, dif=0.3, dea=-0.1, prev_dif=-0.2, prev_dea=-0.05)
        self.assertFalse(is_zero_axis_pullback_cross(m))

    def test_昨日已在上方不算新金叉(self):
        m = dict(self.BASE, prev_dif=0.9, prev_dea=0.5)
        self.assertFalse(is_zero_axis_pullback_cross(m))

    def test_今日未上穿不命中(self):
        m = dict(self.BASE, dif=0.4, dea=0.6)
        self.assertFalse(is_zero_axis_pullback_cross(m))

    def test_昨日恰好相等算回踩(self):
        # prev_dif <= prev_dea 用闭区间：贴合"回踩到位"的含义
        m = dict(self.BASE, prev_dif=0.5, prev_dea=0.5)
        self.assertTrue(is_zero_axis_pullback_cross(m))

    def test_恰好等于零轴不算上方(self):
        # 严格大于 0，站在零轴上不算"零轴上方"
        self.assertFalse(is_zero_axis_pullback_cross(
            dict(self.BASE, dea=0.0, prev_dea=0.1)))
        self.assertFalse(is_zero_axis_pullback_cross(
            dict(self.BASE, dif=0.0, dea=-0.1, prev_dif=-0.2, prev_dea=-0.15)))

    def test_None返回False(self):
        # 算不出来就不是买点，不猜
        self.assertFalse(is_zero_axis_pullback_cross(None))


class TestEvaluateMacd(unittest.TestCase):
    def test_K线不足给出说明(self):
        r = evaluate_macd([10.0] * 10)
        self.assertFalse(r["macd_cross_up"])
        self.assertIsNone(r["macd_dif"])
        self.assertIn("K 线不足", r["macd_reason"])

    def test_下跌趋势判不在零轴上方(self):
        r = evaluate_macd([float(i) for i in range(120, 20, -1)])
        self.assertFalse(r["macd_cross_up"])
        self.assertIn("不在零轴上方", r["macd_reason"])

    def test_线性等差序列DIF与DEA恒等(self):
        """
        等差数列的 DIF 会收敛成常数，DEA 是该常数的 EMA → 两者恒等。
        这是数学必然（不是 bug），真实股价不会这样，但测试构造时容易踩到：
        用线性序列去造"金叉"永远造不出来，必须用有波动的形态。
        """
        m = compute_macd([float(i) for i in range(1, 120)])
        self.assertAlmostEqual(m["dif"], m["dea"], places=6)
        self.assertFalse(is_zero_axis_pullback_cross(m))

    @staticmethod
    def _wavy_uptrend(n=160, slope=0.6, amp=8.0, period=9.0):
        """带波动的上升趋势 —— 会周期性产生零轴上方的回踩与金叉。"""
        import math
        return [100.0 + i * slope + amp * math.sin(i / period) for i in range(n)]

    def test_构造零轴上方回踩金叉(self):
        """
        震荡上行中，DIF 回落跌破 DEA（回踩）后再度上穿，且两线都在零轴上方 ——
        这正是规则描述的形态。逐根推进找出真正命中的那一天。
        """
        closes = self._wavy_uptrend()
        found = None
        for i in range(MIN_BARS, len(closes)):
            r = evaluate_macd(closes[:i])
            if r["macd_cross_up"]:
                found = r
                break
        self.assertIsNotNone(found, "震荡上行形态里应当能找到零轴上方金叉")
        self.assertGreater(found["macd_dif"], 0)
        self.assertGreater(found["macd_dea"], 0)
        self.assertIn("零轴上方回踩金叉", found["macd_reason"])

    def test_金叉只在当天为真(self):
        """命中的是"今天发生金叉"，第二天即便仍在上方也不该继续报。"""
        closes = self._wavy_uptrend()
        hit_at = None
        for i in range(MIN_BARS, len(closes)):
            if evaluate_macd(closes[:i])["macd_cross_up"]:
                hit_at = i
                break
        self.assertIsNotNone(hit_at)
        nxt = evaluate_macd(closes[:hit_at + 1])
        self.assertFalse(nxt["macd_cross_up"])
        self.assertIn("非今日金叉", nxt["macd_reason"])

    def test_零轴下方金叉不算命中(self):
        """深跌后反弹会在零轴下方金叉 —— 规则明确只做零轴上方，必须排除。"""
        closes = [200.0 - i * 2 for i in range(60)]       # 长跌，DIF/DEA 到零轴下
        closes += [closes[-1] + i * 3 for i in range(1, 8)]   # 反弹
        for extra in range(0, 6):
            r = evaluate_macd(closes + [closes[-1] + 5] * extra)
            if r["macd_dif"] is not None and r["macd_dif"] <= 0:
                self.assertFalse(r["macd_cross_up"],
                                 "零轴下方不该命中")

    def test_返回值字段齐全(self):
        r = evaluate_macd([float(i) for i in range(1, 120)])
        for k in ("macd_dif", "macd_dea", "macd_hist", "macd_cross_up", "macd_reason"):
            self.assertIn(k, r)
        self.assertTrue(r["macd_reason"].strip())

    def test_参数取自config(self):
        self.assertEqual(settings.MACD_FAST, 12)
        self.assertEqual(settings.MACD_SLOW, 26)
        self.assertEqual(settings.MACD_SIGNAL, 9)


if __name__ == "__main__":
    unittest.main()
