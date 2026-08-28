"""跳空缺口信号（突破 / 加油 / 衰竭 / 加仓）的单元测试。"""
import unittest
from datetime import date, timedelta

from config import settings
from engines.gap import (
    GAP_ADD,
    GAP_BREAKOUT,
    GAP_EXHAUST,
    GAP_RUNAWAY,
    MIN_BARS,
    _avg_vol,
    evaluate_gap_signal,
    find_gaps,
    gap_unfilled_through,
    is_bottom_before,
    upper_shadow_ratios,
)

BASE = date(2026, 1, 1)


def bar(i, o, h, l, c, v):
    """构造一根 K 线，字段顺序与 load_ohlc_series 一致。"""
    return (BASE + timedelta(days=i), o, h, l, c, v)


def flat(n, price=10.0, vol=1000.0, start=0):
    """n 根横盘 K 线（振幅 ±1%），用于把序列凑够长度。"""
    return [bar(start + i, price, price * 1.01, price * 0.99, price, vol)
            for i in range(n)]


class TestFindGaps(unittest.TestCase):
    def test_识别向上跳空(self):
        bars = [bar(0, 10, 10.5, 9.8, 10.2, 1000),
                bar(1, 11, 11.5, 10.8, 11.2, 2000)]   # low 10.8 > 前高 10.5
        gaps = find_gaps(bars)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["idx"], 1)
        self.assertAlmostEqual(gaps[0]["lower"], 10.5)   # 下沿 = 前一日最高
        self.assertAlmostEqual(gaps[0]["upper"], 10.8)   # 上沿 = 当日最低

    def test_向下跳空不算(self):
        bars = [bar(0, 10, 10.5, 9.8, 10.2, 1000),
                bar(1, 9, 9.2, 8.8, 9.0, 2000)]
        self.assertEqual(find_gaps(bars), [])

    def test_相接不算跳空(self):
        # 今日最低 == 昨日最高，价格连续，没有真空区
        bars = [bar(0, 10, 10.5, 9.8, 10.2, 1000),
                bar(1, 10.6, 11, 10.5, 10.9, 2000)]
        self.assertEqual(find_gaps(bars), [])

    def test_幅度不够被滤掉(self):
        # 缺口 0.1%，低于默认 1% 门槛
        bars = [bar(0, 10, 10.0, 9.8, 9.9, 1000),
                bar(1, 10.1, 10.3, 10.01, 10.2, 2000)]
        self.assertEqual(find_gaps(bars), [])
        self.assertEqual(len(find_gaps(bars, min_pct=0.05)), 1)

    def test_多个跳空按序返回(self):
        bars = [bar(0, 10, 10.5, 9.8, 10.2, 1000),
                bar(1, 11, 11.5, 10.8, 11.2, 2000),
                bar(2, 11.6, 12, 11.6, 11.9, 1500),
                bar(3, 13, 13.5, 12.5, 13.2, 1800)]
        idxs = [g["idx"] for g in find_gaps(bars)]
        self.assertEqual(idxs, [1, 3])


class TestGapUnfilled(unittest.TestCase):
    def _bars(self, after_lows):
        b = [bar(0, 10, 10.5, 9.8, 10.2, 1000),
             bar(1, 11, 11.5, 10.8, 11.2, 2000)]
        for k, lo in enumerate(after_lows):
            b.append(bar(2 + k, 11.2, 11.8, lo, 11.4, 1500))
        return b

    def test_未回补(self):
        bars = self._bars([10.8, 10.9, 11.0])     # 都在下沿 10.5 之上
        gap = find_gaps(bars)[0]
        self.assertTrue(gap_unfilled_through(bars, gap, 4))

    def test_跌破下沿即回补(self):
        bars = self._bars([10.8, 10.4, 11.0])     # 第二天 10.4 < 下沿 10.5
        gap = find_gaps(bars)[0]
        self.assertFalse(gap_unfilled_through(bars, gap, 4))

    def test_恰好触及下沿算回补(self):
        bars = self._bars([10.8, 10.5, 11.0])     # low == lower，缺口被填平
        gap = find_gaps(bars)[0]
        self.assertFalse(gap_unfilled_through(bars, gap, 4))

    def test_进缺口但未跌破下沿不算回补(self):
        # 10.6 在缺口区间 [10.5, 10.8] 内，但没把缺口填完
        bars = self._bars([10.6, 10.7, 11.0])
        gap = find_gaps(bars)[0]
        self.assertTrue(gap_unfilled_through(bars, gap, 4))


class TestUpperShadow(unittest.TestCase):
    def test_长上影(self):
        # 开 10 收 10.2（实体 0.2），最高 12（上影 1.8），最低 9.9
        r_body, r_range = upper_shadow_ratios(bar(0, 10, 12, 9.9, 10.2, 1000))
        self.assertAlmostEqual(r_body, 1.8 / 0.2, places=6)
        self.assertGreater(r_range, 0.5)

    def test_光头阳线无上影(self):
        r_body, r_range = upper_shadow_ratios(bar(0, 10, 11, 9.9, 11, 1000))
        self.assertAlmostEqual(r_body, 0.0)
        self.assertAlmostEqual(r_range, 0.0)

    def test_十字星实体为零返回无穷(self):
        r_body, r_range = upper_shadow_ratios(bar(0, 10, 11, 9.5, 10, 1000))
        self.assertEqual(r_body, float("inf"))
        self.assertGreater(r_range, 0)

    def test_一字板振幅为零不报错(self):
        self.assertEqual(upper_shadow_ratios(bar(0, 10, 10, 10, 10, 1000)), (0.0, 0.0))


class TestIsBottomBefore(unittest.TestCase):
    def test_贴近低点算底部(self):
        bars = flat(40, price=10.0)
        bars.append(bar(40, 12, 12.5, 11.5, 12.2, 3000))
        self.assertTrue(is_bottom_before(bars, 40))   # 前收 10 距低点 9.9 仅 1%

    def test_远离低点不算底部(self):
        bars = flat(40, price=10.0)
        # 再涨一大段，前收拉到 20，距低点 100%
        bars += [bar(40 + i, 20, 20.2, 19.8, 20.0, 1000) for i in range(5)]
        bars.append(bar(45, 22, 22.5, 21.5, 22.2, 3000))
        self.assertFalse(is_bottom_before(bars, 45))

    def test_窗口太短返回False(self):
        self.assertFalse(is_bottom_before(flat(10) + [bar(10, 12, 13, 11, 12, 1000)], 10))


class TestAvgVol(unittest.TestCase):
    def test_不含当日(self):
        bars = flat(20, vol=1000.0) + [bar(20, 10, 10.1, 9.9, 10, 99999.0)]
        self.assertAlmostEqual(_avg_vol(bars, 20), 1000.0)

    def test_历史不足返回None(self):
        self.assertIsNone(_avg_vol(flat(5), 5))


class TestEvaluateGapSignal(unittest.TestCase):
    def test_K线不足给出说明(self):
        r = evaluate_gap_signal(flat(10))
        self.assertIsNone(r["gap_signal"])
        self.assertIn("K 线不足", r["gap_reason"])

    def test_横盘无信号(self):
        r = evaluate_gap_signal(flat(80))
        self.assertIsNone(r["gap_signal"])
        self.assertIn("无符合条件", r["gap_reason"])

    def test_突破_底部放量跳空三天不补(self):
        bars = flat(60, price=10.0, vol=1000.0)          # 长期横盘在底部
        # 跳空日：low 10.8 > 前高 10.1，放量 3×
        bars.append(bar(60, 10.9, 11.3, 10.8, 11.2, 3000.0))
        # 之后三天都不跌破下沿 10.1
        for k in range(3):
            bars.append(bar(61 + k, 11.2, 11.6, 11.0, 11.4, 1500.0))
        r = evaluate_gap_signal(bars)
        self.assertEqual(r["gap_signal"], GAP_BREAKOUT)
        self.assertEqual(r["gap_days_since"], 0)         # 今天正是确认日
        self.assertAlmostEqual(r["gap_lower"], 10.1)
        self.assertAlmostEqual(r["gap_upper"], 10.8)

    def test_突破_三天内补缺口则不成立(self):
        bars = flat(60, price=10.0, vol=1000.0)
        bars.append(bar(60, 10.9, 11.3, 10.8, 11.2, 3000.0))
        bars.append(bar(61, 11.2, 11.6, 11.0, 11.4, 1500.0))
        bars.append(bar(62, 11.0, 11.2, 10.0, 10.3, 1800.0))   # 跌破下沿 10.1
        bars.append(bar(63, 10.3, 10.6, 10.1, 10.5, 1200.0))
        self.assertIsNone(evaluate_gap_signal(bars)["gap_signal"])

    def test_突破_量能不足则不成立(self):
        bars = flat(60, price=10.0, vol=1000.0)
        bars.append(bar(60, 10.9, 11.3, 10.8, 11.2, 1100.0))   # 仅 1.1×，低于 2.0
        for k in range(3):
            bars.append(bar(61 + k, 11.2, 11.6, 11.0, 11.4, 1000.0))
        self.assertIsNone(evaluate_gap_signal(bars)["gap_signal"])

    def test_衰竭_天量长上影(self):
        # 先横盘垫底，再单调拉升 40%，最后一天天量长上影
        bars = flat(40, price=10.0, vol=1000.0)
        p = 10.0
        for k in range(21):
            p *= 1.02
            bars.append(bar(40 + k, p, p * 1.01, p * 0.99, p, 1000.0))
        top = bars[-1][4]
        # 上影 = 高 - max(开,收)，做得足够长
        bars.append(bar(61, top, top * 1.15, top * 0.99, top * 1.01, 5000.0))
        r = evaluate_gap_signal(bars)
        self.assertEqual(r["gap_signal"], GAP_EXHAUST)
        self.assertIn("天量长上影", r["gap_reason"])

    def test_衰竭_优先于其他信号(self):
        """衰竭是风险提示，同期有看涨信号时也必须先报出来。"""
        bars = flat(40, price=10.0, vol=1000.0)
        p = 10.0
        for k in range(21):
            p *= 1.02
            bars.append(bar(40 + k, p, p * 1.01, p * 0.99, p, 1000.0))
        top = bars[-1][4]
        bars.append(bar(61, top, top * 1.15, top * 0.99, top * 1.01, 5000.0))
        self.assertEqual(evaluate_gap_signal(bars)["gap_signal"], GAP_EXHAUST)

    def test_衰竭_涨幅不够不成立(self):
        bars = flat(60, price=10.0, vol=1000.0)
        bars.append(bar(60, 10.0, 11.5, 9.9, 10.1, 5000.0))   # 天量长上影但没大涨
        self.assertNotEqual(evaluate_gap_signal(bars)["gap_signal"], GAP_EXHAUST)

    def test_加仓_回踩上沿守住且缩量(self):
        # 垫底横盘要够长：evaluate_gap_signal 要求至少 MIN_BARS(62) 根
        bars = flat(50, price=10.0, vol=1000.0)
        # 跳空：下沿 10.1，上沿 10.8
        bars.append(bar(50, 10.9, 11.3, 10.8, 11.2, 3000.0))
        # 涨上去 20%+（相对上沿 10.8 → 13 以上）
        p = 11.2
        for k in range(12):
            p *= 1.02
            bars.append(bar(51 + k, p, p * 1.01, p * 0.995, p, 1200.0))
        # 回调到缺口上沿附近，最低恰好守在 10.8 之上，且量逐日递减
        bars.append(bar(63, 12.0, 12.1, 11.5, 11.6, 900.0))
        bars.append(bar(64, 11.6, 11.7, 11.0, 11.1, 700.0))
        bars.append(bar(65, 11.1, 11.2, 10.85, 10.95, 500.0))
        r = evaluate_gap_signal(bars)
        self.assertEqual(r["gap_signal"], GAP_ADD)
        self.assertAlmostEqual(r["gap_upper"], 10.8)
        self.assertIn("守住未破", r["gap_reason"])

    def test_加仓_跌进缺口则不成立(self):
        """用户选定的是严格口径：进了缺口就不算守住。"""
        bars = flat(50, price=10.0, vol=1000.0)
        bars.append(bar(50, 10.9, 11.3, 10.8, 11.2, 3000.0))
        p = 11.2
        for k in range(12):
            p *= 1.02
            bars.append(bar(51 + k, p, p * 1.01, p * 0.995, p, 1200.0))
        bars.append(bar(63, 12.0, 12.1, 11.5, 11.6, 900.0))
        bars.append(bar(64, 11.6, 11.7, 11.0, 11.1, 700.0))
        # 最低 10.6 跌进缺口 [10.1, 10.8]，虽未补完但按严格口径不算守住
        bars.append(bar(65, 11.1, 11.2, 10.6, 10.7, 500.0))
        self.assertNotEqual(evaluate_gap_signal(bars)["gap_signal"], GAP_ADD)

    def test_加仓_量能未萎缩则不成立(self):
        bars = flat(50, price=10.0, vol=1000.0)
        bars.append(bar(50, 10.9, 11.3, 10.8, 11.2, 3000.0))
        p = 11.2
        for k in range(12):
            p *= 1.02
            bars.append(bar(51 + k, p, p * 1.01, p * 0.995, p, 1200.0))
        bars.append(bar(63, 12.0, 12.1, 11.5, 11.6, 900.0))
        bars.append(bar(64, 11.6, 11.7, 11.0, 11.1, 1500.0))   # 放量，非递减
        bars.append(bar(65, 11.1, 11.2, 10.85, 10.95, 2000.0))
        self.assertNotEqual(evaluate_gap_signal(bars)["gap_signal"], GAP_ADD)

    def test_信号有效期内仍显示并标出第N日(self):
        bars = flat(60, price=10.0, vol=1000.0)
        bars.append(bar(60, 10.9, 11.3, 10.8, 11.2, 3000.0))
        for k in range(3):
            bars.append(bar(61 + k, 11.2, 11.6, 11.0, 11.4, 1500.0))
        # 确认日之后再走两天，缺口仍未补
        bars.append(bar(64, 11.4, 11.7, 11.1, 11.5, 1200.0))
        bars.append(bar(65, 11.5, 11.8, 11.2, 11.6, 1100.0))
        r = evaluate_gap_signal(bars)
        self.assertEqual(r["gap_signal"], GAP_BREAKOUT)
        self.assertEqual(r["gap_days_since"], 2)

    def test_超出有效期后消失(self):
        bars = flat(60, price=10.0, vol=1000.0)
        bars.append(bar(60, 10.9, 11.3, 10.8, 11.2, 3000.0))
        for k in range(3):
            bars.append(bar(61 + k, 11.2, 11.6, 11.0, 11.4, 1500.0))
        # 确认日后再走满有效期
        for k in range(settings.GAP_SIGNAL_VALID_DAYS + 1):
            bars.append(bar(64 + k, 11.4, 11.7, 11.1, 11.5, 1200.0))
        self.assertIsNone(evaluate_gap_signal(bars)["gap_signal"])

    def test_返回值字段齐全(self):
        for bars in (flat(10), flat(80)):
            r = evaluate_gap_signal(bars)
            for k in ("gap_signal", "gap_signal_label", "gap_days_since",
                      "gap_lower", "gap_upper", "gap_reason"):
                self.assertIn(k, r)

    def test_阈值全部取自config(self):
        self.assertEqual(settings.GAP_HOLD_DAYS, 3)
        self.assertEqual(settings.GAP_SIGNAL_VALID_DAYS, 5)
        self.assertEqual(settings.GAP_BOTTOM_MAX_ABOVE_PCT, 20.0)


if __name__ == "__main__":
    unittest.main()


class TestIncompleteBars(unittest.TestCase):
    """
    load_ohlc_series 只保证 close 非空，开高低量可能是 None。
    缺口判定必须自己把残缺 K 线挡在外面，而且不能靠"过滤掉残缺行" ——
    删掉中间一行会让两侧 K 线变相邻，凭空造出假缺口。
    """

    def test_残缺K线不报错(self):
        bars = flat(80)
        bars[10] = (BASE + timedelta(days=10), None, None, None, 10.0, 1000.0)
        r = evaluate_gap_signal(bars)          # 不抛异常即可
        self.assertIn("gap_signal", r)

    def test_只截取残缺之后的连续尾段(self):
        # 前 60 根残缺，后面完整但不足 MIN_BARS → 应判数据不足
        bars = flat(90)
        for i in range(60):
            bars[i] = (BASE + timedelta(days=i), None, None, None, 10.0, 1000.0)
        r = evaluate_gap_signal(bars)
        self.assertIsNone(r["gap_signal"])
        self.assertIn("K 线不足", r["gap_reason"])

    def test_不会因删行造出假缺口(self):
        """中间挖掉一根后，两侧不该被当成跳空。"""
        bars = flat(80, price=10.0)
        bars[40] = (BASE + timedelta(days=40), None, None, None, 10.0, 1000.0)
        r = evaluate_gap_signal(bars)
        self.assertIsNone(r["gap_signal"])

    def test_缺成交量也算残缺(self):
        bars = flat(80)
        bars[5] = (BASE + timedelta(days=5), 10.0, 10.1, 9.9, 10.0, None)
        r = evaluate_gap_signal(bars)
        self.assertIn("gap_signal", r)


class TestDateDiscontinuity(unittest.TestCase):
    """
    数据空洞不能被当成跳空。只补最近若干天、或增量抓取中断，都会在 price_data
    里留下几个月的洞；相邻两行一比，那段正常涨幅就成了一个巨大的假缺口。
    """

    def test_跨越数据空洞不算跳空(self):
        b = [bar(0, 10, 10.5, 9.8, 10.2, 1000)]
        # 下一根隔了 100 天，价格高一大截 —— 是数据洞，不是跳空
        b.append((BASE + timedelta(days=100), 20, 21, 19.5, 20.5, 2000))
        self.assertEqual(find_gaps(b), [])

    def test_长假休市仍算跳空(self):
        # 春节/国庆最长休市约 9-11 个自然日，必须仍能识别
        b = [bar(0, 10, 10.5, 9.8, 10.2, 1000)]
        b.append((BASE + timedelta(days=10), 11, 11.5, 10.8, 11.2, 2000))
        gaps = find_gaps(b)
        self.assertEqual(len(gaps), 1)
        self.assertAlmostEqual(gaps[0]["lower"], 10.5)

    def test_恰好超过阈值不算(self):
        from engines.gap import MAX_BAR_GAP_DAYS
        b = [bar(0, 10, 10.5, 9.8, 10.2, 1000)]
        b.append((BASE + timedelta(days=MAX_BAR_GAP_DAYS + 1), 11, 11.5, 10.8, 11.2, 2000))
        self.assertEqual(find_gaps(b), [])

    def test_空洞不会污染突破信号(self):
        bars = flat(60, price=10.0, vol=1000.0)
        # 断档 90 天后价格翻倍，若不校验日期会被判成底部放量跳空
        last = BASE + timedelta(days=59)
        for k in range(4):
            d = last + timedelta(days=90 + k)
            bars.append((d, 21, 22, 20.8, 21.5, 3000.0))
        self.assertIsNone(evaluate_gap_signal(bars)["gap_signal"])


class TestConfirmDate(unittest.TestCase):
    """
    gap_days_since 是相对"序列末根"的交易日数，K 线停更时会骗人
    （末根是 4 个月前时，"+4" 看着像 4 天前）。必须同时给出确认日的真实日期。
    """

    def test_突破带出确认日期(self):
        bars = flat(60, price=10.0, vol=1000.0)
        bars.append(bar(60, 10.9, 11.3, 10.8, 11.2, 3000.0))
        for k in range(3):
            bars.append(bar(61 + k, 11.2, 11.6, 11.0, 11.4, 1500.0))
        r = evaluate_gap_signal(bars)
        self.assertEqual(r["gap_signal"], GAP_BREAKOUT)
        # 确认日 = 跳空后第 3 根，即下标 63
        self.assertEqual(r["gap_confirm_date"], bars[63][0])

    def test_有效期内确认日期不变(self):
        bars = flat(60, price=10.0, vol=1000.0)
        bars.append(bar(60, 10.9, 11.3, 10.8, 11.2, 3000.0))
        for k in range(3):
            bars.append(bar(61 + k, 11.2, 11.6, 11.0, 11.4, 1500.0))
        confirm_date = bars[63][0]
        bars.append(bar(64, 11.4, 11.7, 11.1, 11.5, 1200.0))
        bars.append(bar(65, 11.5, 11.8, 11.2, 11.6, 1100.0))
        r = evaluate_gap_signal(bars)
        self.assertEqual(r["gap_days_since"], 2)
        self.assertEqual(r["gap_confirm_date"], confirm_date)   # 日期是绝对的，不随天数漂移

    def test_无信号时确认日期为None(self):
        self.assertIsNone(evaluate_gap_signal(flat(80))["gap_confirm_date"])
