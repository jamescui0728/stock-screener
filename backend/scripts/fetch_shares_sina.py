"""
从 Sina stock_zh_a_daily 批量拉取 outstanding_share 作为 total_shares 的代理。
- 绕开东财的 stock_individual_info_em 限流
- Sina 接口返回的 outstanding_share 是流通股（单位：股）
- 对于股份集中度高的公司，流通股 ≈ 总股本（误差一般 <20%）
- 估值分位计算是相对自身历史的 percentile，绝对 scale 不敏感，所以足够用
"""
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3
import akshare as ak

DB = Path(__file__).resolve().parent.parent / "stock_screener.db"


def fetch_shares(code: str) -> Optional[float]:
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    try:
        df = ak.stock_zh_a_daily(
            symbol=f"{prefix}{code}",
            start_date="20260401",
            end_date="20260418",
            adjust="",
        )
        if df is None or df.empty or "outstanding_share" not in df.columns:
            return None
        return float(df.iloc[-1]["outstanding_share"])
    except Exception:
        return None


def main():
    conn = sqlite3.connect(str(DB), timeout=30.0)
    cur = conn.cursor()

    # 取有价格但缺 shares 的股票
    cur.execute(
        """
        SELECT DISTINCT p.stock_code
        FROM price_data p
        LEFT JOIN stocks s ON p.stock_code = s.code
        WHERE (s.total_shares IS NULL OR s.total_shares = 0)
        ORDER BY p.stock_code
        """
    )
    todo = [r[0] for r in cur.fetchall()]
    total = len(todo)
    print(f"待拉取: {total} 只", flush=True)

    t0 = time.time()
    ok = 0
    fail = 0
    for i, code in enumerate(todo, 1):
        shares = fetch_shares(code)
        if shares and shares > 0:
            cur.execute("UPDATE stocks SET total_shares=? WHERE code=?", (shares, code))
            ok += 1
        else:
            fail += 1
        if i % 25 == 0:
            conn.commit()
            eta_s = (time.time() - t0) / i * (total - i)
            print(
                f"[{i}/{total}] ok={ok} fail={fail} "
                f"elapsed={time.time()-t0:.0f}s eta={eta_s:.0f}s",
                flush=True,
            )
        time.sleep(0.15)

    conn.commit()
    conn.close()
    print(f"\n完成: ok={ok} fail={fail} 耗时={time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
