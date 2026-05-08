"""
轻量级后台任务状态追踪（内存单例）
用于「更新数据」按钮的多步进度反馈
"""
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TaskState:
    name:        str   = ""
    status:      str   = "idle"    # idle / running / done / error
    started_at:  float = 0.0
    finished_at: float = 0.0
    detail:      str   = ""

    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 0) if self.started_at else 0

    def to_dict(self):
        return {
            "name":    self.name,
            "status":  self.status,
            "elapsed": self.elapsed(),
            "detail":  self.detail,
        }


@dataclass
class RefreshProgress:
    tasks: list = field(default_factory=list)   # List[TaskState]
    overall: str = "idle"   # idle / running / done

    def to_dict(self):
        return {
            "overall": self.overall,
            "tasks":   [t.to_dict() for t in self.tasks],
        }


_prog = RefreshProgress()


def get_refresh_progress() -> RefreshProgress:
    return _prog


def start_refresh(task_names: list) -> RefreshProgress:
    global _prog
    _prog = RefreshProgress(
        overall="running",
        tasks=[TaskState(name=n, status="pending") for n in task_names],
    )
    return _prog


def mark_task(name: str, status: str, detail: str = ""):
    for t in _prog.tasks:
        if t.name == name:
            t.status = status
            t.detail = detail
            if status == "running":
                t.started_at = time.time()
            elif status in ("done", "error"):
                t.finished_at = time.time()
    # 所有任务完成 → overall = done
    if all(t.status in ("done", "error") for t in _prog.tasks):
        _prog.overall = "done"
