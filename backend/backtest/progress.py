"""
回测进度追踪器（内存单例）
engine.py 写入进度 → SSE 接口读取推送给前端
"""
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BacktestProgress:
    run_id:       int   = 0
    status:       str   = "idle"      # idle / running / done / error
    stage:        str   = ""          # 当前阶段描述
    pct:          float = 0.0         # 0-100
    current:      int   = 0           # 已处理数量
    total:        int   = 0           # 总数量
    win_rate:     Optional[float] = None
    ic:           Optional[float] = None
    log:          list  = field(default_factory=list)   # 最近 50 条日志
    started_at:   float = 0.0
    finished_at:  float = 0.0
    error_msg:    str   = ""

    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 1) if self.started_at else 0

    def push_log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {msg}")
        if len(self.log) > 80:
            self.log = self.log[-80:]

    def to_dict(self) -> dict:
        return {
            "run_id":     self.run_id,
            "status":     self.status,
            "stage":      self.stage,
            "pct":        round(self.pct, 1),
            "current":    self.current,
            "total":      self.total,
            "win_rate":   self.win_rate,
            "ic":         self.ic,
            "elapsed":    self.elapsed(),
            "log":        self.log[-20:],     # 只推最近 20 条
            "error_msg":  self.error_msg,
        }


# 全局单例（同一进程内共享）
_progress = BacktestProgress()


def get_progress() -> BacktestProgress:
    return _progress


def reset(run_id: int = 0):
    global _progress
    _progress = BacktestProgress(
        run_id=run_id,
        status="running",
        started_at=time.time(),
    )
    return _progress
