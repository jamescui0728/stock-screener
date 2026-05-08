"""
财报抓取进度追踪器（内存单例）
fetch_all_financial_data 写入 → 前端轮询
"""
import time
from dataclasses import dataclass, field


@dataclass
class FetchProgress:
    status:    str   = "idle"    # idle / running / done / error
    total:     int   = 0
    current:   int   = 0
    saved:     int   = 0
    skipped:   int   = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    error_msg: str   = ""

    def pct(self) -> float:
        return round(self.current / max(self.total, 1) * 100, 1)

    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 0) if self.started_at else 0

    def eta(self) -> str:
        if self.current <= 0 or self.status != "running":
            return "-"
        elapsed = time.time() - self.started_at
        rate = self.current / elapsed          # stocks/sec
        remain = (self.total - self.current) / max(rate, 0.01)
        m, s = divmod(int(remain), 60)
        return f"{m}分{s}秒" if m else f"{s}秒"

    def to_dict(self) -> dict:
        return {
            "status":   self.status,
            "total":    self.total,
            "current":  self.current,
            "saved":    self.saved,
            "skipped":  self.skipped,
            "pct":      self.pct(),
            "elapsed":  self.elapsed(),
            "eta":      self.eta(),
            "error_msg": self.error_msg,
        }


_progress = FetchProgress()


def get_fetch_progress() -> FetchProgress:
    return _progress


def reset_fetch_progress(total: int) -> FetchProgress:
    global _progress
    _progress = FetchProgress(
        status="running",
        total=total,
        started_at=time.time(),
    )
    return _progress
