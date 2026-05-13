"""
FastAPI 路由
所有接口统一前缀 /api
"""
import asyncio
import json
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import get_current_user
from backtest.engine import run_backtest
from backtest.evaluator import compare_runs, get_run_report
from backtest.optimizer import optimize
from backtest.progress import get_progress
from config import get_settings_dict, save_settings
from data.fetcher import (
    fetch_all_financial_data, fetch_all_price_data, fetch_macro_data,
    fetch_stock_industry_mapping, fetch_stock_news,
)
from data.sentiment import analyze_all_news
from database import get_db
from engines.industry_scorer import score_all_industries, score_industry
from engines.signal_engine import generate_all_signals, generate_signal
from models.models import (
    BacktestRun, Industry, NewsItem, PriceData, Stock, User, Watchlist,
)

router = APIRouter(prefix="/api")


# ══════════════════════════════════════════════
# 行业接口
# ══════════════════════════════════════════════
@router.get("/industries")
def list_industries(
    min_score: float = Query(0.0),
    db: Session = Depends(get_db),
):
    """列出所有行业及评分（可按 min_score 过滤）"""
    industries = db.query(Industry).all()
    result = []
    for ind in industries:
        if ind.total_score is not None and ind.total_score < min_score:
            continue
        stock_count = db.query(Stock).filter_by(industry_code=ind.code, is_active=True).count()
        result.append({
            "code":                  ind.code,
            "name":                  ind.name,
            "total_score":           ind.total_score,
            "score_revenue_stability": ind.score_revenue_stability,
            "score_profit_stability":  ind.score_profit_stability,
            "score_anti_cycle":        ind.score_anti_cycle,
            "score_competition":       ind.score_competition,
            "stock_count":             stock_count,
            "updated_at":             str(ind.updated_at),
        })
    result.sort(key=lambda x: (x["total_score"] or 0), reverse=True)
    return result


@router.post("/industries/rescore-all")
def rescore_all_industries_route(background_tasks: BackgroundTasks):
    """后台批量重新评分所有行业"""
    from database import SessionLocal as _SL
    def _run():
        _db = _SL()
        try: score_all_industries(_db)
        finally: _db.close()
    background_tasks.add_task(_run)
    return {"message": "行业批量评分已启动（后台运行，通常需要 1-3 分钟）"}


@router.post("/industries/{code}/score")
def rescore_industry(code: str, db: Session = Depends(get_db)):
    score = score_industry(db, code)
    if score is None:
        raise HTTPException(404, "行业不存在或数据不足")
    return {"code": code, "total_score": score}


# ══════════════════════════════════════════════
# 股票筛选接口
# ══════════════════════════════════════════════
@router.get("/stocks")
def list_stocks(
    keyword: Optional[str]        = None,  # 股票代码或名称模糊搜索
    industry_code: Optional[str]  = None,
    signal: Optional[str] = Query(
        None,
        description=(
            "长期信号精确匹配单一等级：STRONG_BUY / BUY / HOLD / SELL / STRONG_SELL。"
            "大小写不敏感（内部 .upper() 统一为大写后比对，因 DB 存大写）。"
        ),
    ),
    signal_group: Optional[str] = Query(
        None,
        description=(
            "长期信号聚合过滤：buy 包含 BUY+STRONG_BUY，sell 包含 SELL+STRONG_SELL。"
            "大小写不敏感。与 signal 同时传时优先级更高（signal 被忽略）。"
        ),
    ),
    short_signal: Optional[str] = Query(
        None,
        description="短期信号精确匹配，语义同 signal（v200 新增）",
    ),
    short_signal_group: Optional[str] = Query(
        None,
        description="短期信号聚合过滤（buy / sell），语义同 signal_group（v200 新增）",
    ),
    min_fundamental: float        = Query(0.0),
    min_composite:   float        = Query(0.0),
    page:  int = Query(1, ge=1),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(Stock).filter_by(is_active=True)
    if keyword:
        import re, unicodedata
        # 标准化关键词：去空格、全角→半角
        kw = keyword.strip()
        kw_normalized = unicodedata.normalize("NFKC", kw).replace(" ", "")
        # 构建宽松正则：在每个字符之间允许任意空格
        # 例如 "万科" → "万\s*科"，这样能匹配 "万  科Ａ"
        regex_pat = r"\s*".join(re.escape(c) for c in kw_normalized)
        # SQLite 不原生支持 REGEXP，改用 Python 过滤 + 代码精确匹配
        # 先用首末字做 SQL 粗筛，再 Python 精筛
        from sqlalchemy import or_
        first_char, last_char = kw_normalized[0], kw_normalized[-1]
        q = q.filter(
            or_(
                Stock.code.contains(kw),
                Stock.name.contains(kw),
                Stock.name.contains(first_char),  # 粗筛：至少包含首字
            )
        )
        # 转 list 后 Python 精筛
        candidates = q.all()
        pat = re.compile(regex_pat, re.IGNORECASE)
        stocks_filtered = [
            s for s in candidates
            if kw in s.code or kw in s.name
            or pat.search(unicodedata.normalize("NFKC", s.name).replace(" ", ""))
            or pat.search(s.name)
        ]
        total = len(stocks_filtered)
        stocks_filtered.sort(key=lambda s: (s.composite_score or 0), reverse=True)
        stocks = stocks_filtered[(page - 1) * limit: page * limit]

        industry_scores = _get_industry_score_map(db)
        return {
            "total": total,
            "page":  page,
            "items": [_stock_summary(s, industry_scores) for s in stocks],
        }
    if industry_code:
        q = q.filter_by(industry_code=industry_code)
    # 长期信号过滤
    if signal_group:
        sg = signal_group.lower()
        if sg == "buy":
            q = q.filter(Stock.signal.in_(("BUY", "STRONG_BUY")))
        elif sg == "sell":
            q = q.filter(Stock.signal.in_(("SELL", "STRONG_SELL")))
        else:
            raise HTTPException(400, f"signal_group 仅支持 buy / sell，收到 {signal_group!r}")
    elif signal:
        q = q.filter_by(signal=signal.upper())

    # 短期信号过滤（v200 新增，与长期信号过滤可同时生效，AND 关系）
    if short_signal_group:
        sg = short_signal_group.lower()
        if sg == "buy":
            q = q.filter(Stock.short_signal.in_(("BUY", "STRONG_BUY")))
        elif sg == "sell":
            q = q.filter(Stock.short_signal.in_(("SELL", "STRONG_SELL")))
        else:
            raise HTTPException(400, f"short_signal_group 仅支持 buy / sell，收到 {short_signal_group!r}")
    elif short_signal:
        q = q.filter_by(short_signal=short_signal.upper())
    if min_fundamental > 0:
        q = q.filter(Stock.fundamental_score >= min_fundamental)
    if min_composite > 0:
        q = q.filter(Stock.composite_score >= min_composite)

    total = q.count()
    stocks = q.order_by(Stock.composite_score.desc().nullslast())\
              .offset((page - 1) * limit).limit(limit).all()

    # 批量取行业评分，避免 N+1 查询
    industry_scores = _get_industry_score_map(db)
    return {
        "total": total,
        "page":  page,
        "items": [_stock_summary(s, industry_scores) for s in stocks],
    }


@router.get("/stocks/{code}")
def get_stock_detail(code: str, db: Session = Depends(get_db)):
    stock = db.query(Stock).filter_by(code=code).first()
    if not stock:
        raise HTTPException(404, "股票不存在")

    financials = sorted(stock.financials, key=lambda f: f.period)[-10:]
    prices_last = sorted(stock.prices, key=lambda p: p.trade_date)[-252:]
    news = stock.news_list[:20]

    industry_scores = _get_industry_score_map(db)
    return {
        "info": _stock_summary(stock, industry_scores),
        "financials": [_financial_dict(f) for f in financials],
        "prices": [_price_dict(p) for p in prices_last],
        "news": [_news_dict(n) for n in news],
    }


@router.post("/stocks/{code}/signal")
def refresh_signal(code: str, db: Session = Depends(get_db)):
    result = generate_signal(db, code)
    if result is None:
        raise HTTPException(422, "数据不足，无法生成信号")
    return result


@router.post("/signals/refresh-all")
def refresh_all_signals(background_tasks: BackgroundTasks):
    """后台异步刷新所有长期信号"""
    from database import SessionLocal as _SL
    from data.task_tracker import mark_task
    def _task():
        _db = _SL()
        mark_task("信号刷新", "running")
        try:
            results = generate_all_signals(_db)
            mark_task("信号刷新", "done", f"已生成 {len(results)} 只信号")
        except Exception as e:
            mark_task("信号刷新", "error", str(e))
        finally:
            _db.close()
    background_tasks.add_task(_task)
    return {"message": "信号刷新已在后台启动"}


# ── 短期信号（v200 新增）─────────────────────────────────
from engines.short_signal_engine import (
    generate_short_signal,
    generate_all_short_signals,
)


@router.post("/stocks/{code}/short-signal")
def refresh_short_signal(code: str, db: Session = Depends(get_db)):
    """重算单只股票的短期信号"""
    result = generate_short_signal(db, code)
    if result is None:
        raise HTTPException(422, "价格数据不足（需 ≥ 21 个交易日），无法生成短期信号")
    return result


@router.post("/signals/refresh-short")
def refresh_all_short_signals(background_tasks: BackgroundTasks):
    """后台异步刷新所有短期信号"""
    from database import SessionLocal as _SL
    from data.task_tracker import mark_task
    def _task():
        _db = _SL()
        mark_task("短期信号刷新", "running")
        try:
            r = generate_all_short_signals(_db)
            mark_task("短期信号刷新", "done",
                      f"成功 {r['generated']} / 跳过 {r['skipped']} / 共 {r['total']}")
        except Exception as e:
            mark_task("短期信号刷新", "error", str(e))
        finally:
            _db.close()
    background_tasks.add_task(_task)
    return {"message": "短期信号刷新已在后台启动（约 1-2 分钟）"}


# ══════════════════════════════════════════════
# 自选股接口
# ══════════════════════════════════════════════
class WatchlistAdd(BaseModel):
    stock_code: str
    note: Optional[str] = None


@router.get("/watchlist")
def get_watchlist(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items = db.query(Watchlist).filter_by(user_id=user.id).all()
    result = []
    for item in items:
        stock = db.query(Stock).filter_by(code=item.stock_code).first()
        if stock:
            d = _stock_summary(stock)
            d["note"]     = item.note
            d["added_at"] = str(item.added_at)
            result.append(d)
    return result


@router.post("/watchlist")
def add_to_watchlist(
    body: WatchlistAdd,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    exists = (
        db.query(Watchlist)
        .filter_by(user_id=user.id, stock_code=body.stock_code)
        .first()
    )
    if exists:
        return {"message": "已在自选股中"}
    db.add(Watchlist(user_id=user.id, stock_code=body.stock_code, note=body.note))
    db.commit()
    return {"message": "添加成功"}


@router.delete("/watchlist/{code}")
def remove_from_watchlist(
    code: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = (
        db.query(Watchlist)
        .filter_by(user_id=user.id, stock_code=code)
        .first()
    )
    if not item:
        raise HTTPException(404, "不在自选股中")
    db.delete(item)
    db.commit()
    return {"message": "已移除"}


@router.get("/watchlist/news")
def get_watchlist_news(
    days:  int = Query(3,  ge=1, le=30, description="时间窗（天）"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    聚合当前用户自选股的最新消息。
    工作日早 9:00 由定时任务自动刷新；也可调用 POST /watchlist/news/refresh 手动拉取。
    """
    from datetime import datetime, timedelta

    codes = [
        r[0] for r in
        db.query(Watchlist.stock_code).filter_by(user_id=user.id).all()
    ]
    if not codes:
        return {"items": [], "last_refreshed_at": None}

    # 股票代码 → 名称，批量查，避免 N+1
    name_map = {
        s.code: s.name
        for s in db.query(Stock.code, Stock.name).filter(Stock.code.in_(codes)).all()
    }

    since = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(NewsItem)
        .filter(NewsItem.stock_code.in_(codes))
        .filter(NewsItem.pub_date >= since)
        .order_by(NewsItem.pub_date.desc())
        .limit(limit)
        .all()
    )

    items = []
    last_refreshed = None
    for n in rows:
        d = _news_dict(n)
        d["stock_code"] = n.stock_code
        d["stock_name"] = name_map.get(n.stock_code, "")
        d["url"]        = n.url
        items.append(d)
        if n.created_at and (last_refreshed is None or n.created_at > last_refreshed):
            last_refreshed = n.created_at

    return {
        "items": items,
        "last_refreshed_at": str(last_refreshed) if last_refreshed else None,
        "days": days,
        "stock_codes": codes,
    }


@router.post("/watchlist/news/refresh")
def refresh_watchlist_news(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """手动为当前用户的自选股拉取最新消息 + 情感分析"""
    codes = [
        r[0] for r in
        db.query(Watchlist.stock_code).filter_by(user_id=user.id).all()
    ]
    if not codes:
        return {"message": "自选股为空", "fetched": 0, "analyzed": 0, "stocks": 0}

    total_fetched = 0
    total_analyzed = 0
    for code in codes:
        try:
            total_fetched  += fetch_stock_news(db, code)
            total_analyzed += analyze_all_news(db, code)
        except Exception:
            # 单只失败不影响其余
            continue
    return {
        "message":  f"已刷新 {len(codes)} 只自选股的消息",
        "fetched":  total_fetched,
        "analyzed": total_analyzed,
        "stocks":   len(codes),
    }


# ══════════════════════════════════════════════
# 回测接口
# ══════════════════════════════════════════════
class BacktestRequest(BaseModel):
    params: Optional[dict] = None
    description: Optional[str] = ""
    sample_codes: Optional[list] = None


@router.post("/backtest/run")
def start_backtest(
    body: BacktestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """启动一次回测（后台执行）"""
    latest = db.query(BacktestRun).order_by(BacktestRun.version.desc()).first()
    next_version = (latest.version + 1) if latest else 1

    def _run():
        run_backtest(
            params=body.params,
            description=body.description,
            version=next_version,
            sample_codes=body.sample_codes,
        )

    background_tasks.add_task(_run)
    return {"message": f"回测 v{next_version} 已启动，稍后查询结果"}


@router.get("/backtest/runs")
def list_backtest_runs(db: Session = Depends(get_db)):
    return compare_runs(db)


@router.get("/backtest/runs/{run_id}")
def get_backtest_report(run_id: int, db: Session = Depends(get_db)):
    report = get_run_report(db, run_id)
    if not report:
        raise HTTPException(404, "回测结果不存在")
    return report


class OptimizeRequest(BaseModel):
    n_iter: int = 20
    init_points: int = 5
    sample_codes: Optional[list] = None


@router.get("/backtest/progress")
async def backtest_progress_stream():
    """
    SSE 接口：实时推送回测进度
    前端用 EventSource('/api/backtest/progress') 订阅
    每 800ms 推一次，status=done/error 后再推 3 次然后结束
    """
    async def event_generator():
        done_count = 0
        while True:
            prog = get_progress()
            data = json.dumps(prog.to_dict(), ensure_ascii=False)
            yield f"data: {data}\n\n"

            if prog.status in ("done", "error"):
                done_count += 1
                if done_count >= 3:   # 确保前端收到最终状态
                    break
            await asyncio.sleep(0.8)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # 禁止 nginx 缓冲
        },
    )


@router.get("/backtest/progress/snapshot")
def backtest_progress_snapshot():
    """普通 GET：返回当前进度快照（轮询备选方案）"""
    return get_progress().to_dict()


@router.post("/backtest/optimize")
def start_optimization(
    body: OptimizeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """启动贝叶斯参数优化（后台执行）"""
    latest = db.query(BacktestRun).order_by(BacktestRun.version.desc()).first()
    version_offset = (latest.version + 1) if latest else 1

    background_tasks.add_task(
        optimize,
        n_iter=body.n_iter,
        init_points=body.init_points,
        sample_codes=body.sample_codes,
        version_offset=version_offset,
    )
    return {"message": f"参数优化已启动，共 {body.n_iter + body.init_points} 轮"}


# ══════════════════════════════════════════════
# 模拟盘接口
# ══════════════════════════════════════════════
from engines import paper_trade as pt


class PaperTradeRequest(BaseModel):
    account_id: Optional[int] = None   # 不传则用用户默认账户
    stock_code: str
    shares: int
    price: Optional[float] = None      # 不传则用最新收盘价
    note: Optional[str] = ""


class PaperResetRequest(BaseModel):
    account_id: Optional[int] = None
    initial_cash: Optional[float] = None   # None → 使用 settings.PAPER_INIT_CASH


class PaperAccountCreateRequest(BaseModel):
    name: Optional[str] = "新账户"
    initial_cash: Optional[float] = None


class PaperAccountUpdateRequest(BaseModel):
    name: Optional[str] = None


def _resolve_account(db: Session, user_id: int, account_id: Optional[int]):
    """
    把可选的 account_id 解析成一个 user 拥有的真实账户：
    - 传了 account_id → 校验所有权，不属抛 403
    - 没传 → 返回用户最早的账户（没有则建一个默认账户）
    """
    if account_id is not None:
        try:
            return pt.get_account(db, account_id, user_id=user_id)
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except ValueError as e:
            raise HTTPException(404, str(e))
    return pt.get_or_create_default_account(db, user_id)


def _account_brief(acct) -> dict:
    return {
        "id":           acct.id,
        "name":         acct.name,
        "initial_cash": round(float(acct.initial_cash or 0), 2),
        "cash_balance": round(float(acct.cash_balance or 0), 2),
        "created_at":   str(acct.created_at) if acct.created_at else None,
        "reset_at":     str(acct.reset_at)   if acct.reset_at   else None,
    }


@router.get("/paper/rules")
def paper_rules():
    """返回当前模拟盘规则参数（供前端计算预估金额/手续费）"""
    return {
        "init_cash": pt._init_cash(),
        "fee_rate": pt._fee_rate(),
        "min_fee":  pt._min_fee(),
        "lot_size": pt._lot_size(),
    }


# /paper/cache/warmup 节流：每 user 5 秒一次（防多 tab/window 同时连点暴打 sina）
_WARMUP_THROTTLE_SEC = 5.0
_warmup_last_at: dict[int, float] = {}


@router.post("/paper/cache/warmup")
def paper_cache_warmup(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    手动刷新当前用户所有账户持仓的实时价缓存。
    适用场景：刚下单 / 刚切换账户，等不及 8 分钟 cron。
    限频：每 user 5 秒最多一次（429 Too Many Requests）。
    返回：{ refreshed: 实际打 sina 的股票数, skipped: 缓存仍新鲜跳过的数 }
    """
    import time
    from engines.paper_trade import _warmup_prices_parallel, _PRICE_CACHE, _CACHE_TTL_SEC
    from models.models import PaperPosition

    # 节流校验
    now = time.time()
    last = _warmup_last_at.get(user.id)
    if last is not None and (now - last) < _WARMUP_THROTTLE_SEC:
        wait = _WARMUP_THROTTLE_SEC - (now - last)
        raise HTTPException(429, f"刷新过于频繁，请 {wait:.1f}s 后重试")
    _warmup_last_at[user.id] = now

    # 取当前用户所有账户的持仓股票去重
    user_acct_ids = [a.id for a in pt.list_accounts(db, user_id=user.id)]
    if not user_acct_ids:
        return {"refreshed": 0, "skipped": 0, "message": "无账户"}

    codes = list({
        r[0] for r in db.query(PaperPosition.stock_code)
                        .filter(PaperPosition.account_id.in_(user_acct_ids))
                        .distinct().all()
    })
    if not codes:
        return {"refreshed": 0, "skipped": 0, "message": "无持仓"}

    # 强制刷新：清掉缓存里这些 code 的旧值，再并行预热
    now = time.time()
    fresh_count = sum(
        1 for c in codes
        if (cached := _PRICE_CACHE.get(c)) and (now - cached[2] < _CACHE_TTL_SEC)
    )
    for c in codes:
        _PRICE_CACHE.pop(c, None)

    _warmup_prices_parallel(db, codes, per_call_timeout=8)
    refreshed = sum(1 for c in codes if c in _PRICE_CACHE)
    return {
        "refreshed": refreshed,
        "skipped":   len(codes) - refreshed,
        "stale_before": len(codes) - fresh_count,
        "message": f"已刷新 {refreshed} / {len(codes)} 只持仓股的实时价",
    }


# ── 账户管理 ─────────────────────────────────────────
@router.get("/paper/accounts")
def list_paper_accounts(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出当前用户所有账户（按 id 升序）。若一个都没有则自动建一个默认账户。"""
    accts = pt.list_accounts(db, user_id=user.id)
    if not accts:
        pt.get_or_create_default_account(db, user.id)
        accts = pt.list_accounts(db, user_id=user.id)
    return [_account_brief(a) for a in accts]


@router.post("/paper/accounts")
def create_paper_account(
    body: PaperAccountCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acct = pt.create_account(
        db, user_id=user.id,
        name=body.name or "新账户",
        initial_cash=body.initial_cash,
    )
    return _account_brief(acct)


@router.put("/paper/accounts/{account_id}")
def update_paper_account(
    account_id: int,
    body: PaperAccountUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        acct = pt.update_account(db, account_id, user_id=user.id, name=body.name)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return _account_brief(acct)


@router.delete("/paper/accounts/{account_id}")
def delete_paper_account(
    account_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        pt.delete_account(db, account_id, user_id=user.id)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"message": "账户已删除"}


# ── 账户快照 / 交易 ──────────────────────────────────
@router.get("/paper/account")
def paper_account(
    account_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """账户快照：现金/持仓/总资产/盈亏。account_id 不传则用默认账户。"""
    acct = _resolve_account(db, user.id, account_id)
    return pt.account_snapshot(db, account_id=acct.id)


@router.post("/paper/buy")
def paper_buy(
    body: PaperTradeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acct = _resolve_account(db, user.id, body.account_id)
    try:
        return pt.buy(
            db, acct.id, body.stock_code, body.shares,
            body.price, body.note or "",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/paper/sell")
def paper_sell(
    body: PaperTradeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acct = _resolve_account(db, user.id, body.account_id)
    try:
        return pt.sell(
            db, acct.id, body.stock_code, body.shares,
            body.price, body.note or "",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/paper/transactions")
def paper_transactions(
    account_id: Optional[int] = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acct = _resolve_account(db, user.id, account_id)
    return pt.list_transactions(db, account_id=acct.id, limit=limit)


@router.post("/paper/reset")
def paper_reset(
    body: PaperResetRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """清空账户并重置现金。initial_cash=None → 使用设置里的 PAPER_INIT_CASH"""
    acct = _resolve_account(db, user.id, body.account_id)
    acct = pt.reset_account(
        db, acct.id, user_id=user.id, initial_cash=body.initial_cash,
    )
    return {
        "message": "账户已重置",
        "account_id":   acct.id,
        "initial_cash": acct.initial_cash,
    }


@router.get("/paper/quote/{code}")
def paper_quote(code: str, db: Session = Depends(get_db)):
    """获取股票当前报价（用于买入/卖出对话框）
    注意：必须用 Sina 实时不复权价（与真实市场价一致），
    不能直接读 price_data 表（那是后复权价，和回测一致但和市场价差几倍）。
    """
    stock = db.query(Stock).filter_by(code=code).first()
    if not stock:
        raise HTTPException(404, "股票不存在")
    price, trade_date = pt._get_latest_price_with_date(db, code)
    return {
        "code":       stock.code,
        "name":       stock.name,
        "signal":     stock.signal,
        "composite_score": stock.composite_score,
        "close":      price,
        "trade_date": trade_date,
    }


# ══════════════════════════════════════════════
# 设置接口
# ══════════════════════════════════════════════
@router.get("/settings")
def get_settings():
    """读取当前所有可配置参数"""
    return get_settings_dict()


@router.put("/settings")
def update_settings(body: dict):
    """保存用户参数（立即生效，持久化到 user_settings.json）"""
    return save_settings(body)


@router.post("/settings/reset")
def reset_settings():
    """恢复默认参数（删除 user_settings.json）"""
    from pathlib import Path
    import config as cfg
    f = Path(__file__).parent.parent / "user_settings.json"
    if f.exists():
        f.unlink()
    # 重置到默认值
    from config import Settings
    defaults = Settings()
    for k, v in defaults.__dict__.items():
        if k.isupper() and hasattr(cfg.settings, k):
            setattr(cfg.settings, k, v)
    return get_settings_dict()


# ══════════════════════════════════════════════
# 状态查询接口
# ══════════════════════════════════════════════
@router.get("/status/financial-count")
def get_financial_count(db: Session = Depends(get_db)):
    """返回已入库的财报条数（前端初始化向导用）"""
    from models.models import FinancialData
    from data.fetch_progress import get_fetch_progress
    count = db.query(FinancialData).count()
    price_count = db.query(PriceData).count()
    scored = db.query(Industry).filter(Industry.total_score.isnot(None)).count()
    prog = get_fetch_progress()
    return {
        "count": count,
        "price_count": price_count,
        "scored_industries": scored,
        "fetch": prog.to_dict(),
    }


# ══════════════════════════════════════════════
# 一键更新数据（宏观 → 行业评分 → 信号）
# ══════════════════════════════════════════════
@router.post("/data/refresh-all")
def refresh_all_data(background_tasks: BackgroundTasks):
    """一键触发：宏观数据 → 行业重新评分 → 长期信号 → 短期信号"""
    from database import SessionLocal as _SL
    from data.task_tracker import start_refresh, mark_task
    TASKS = ["宏观数据", "行业评分", "长期信号刷新", "短期信号刷新"]
    start_refresh(TASKS)

    def _run():
        _db = _SL()
        try:
            # 1. 宏观数据
            mark_task("宏观数据", "running")
            try:
                result = fetch_macro_data(_db)
                if isinstance(result, dict):
                    new, total = result["new"], result["total"]
                    msg = f"共 {total} 条" if new == 0 else f"新增 {new} 条，共 {total} 条"
                else:
                    msg = f"写入 {result} 条"
                mark_task("宏观数据", "done", msg)
            except Exception as e:
                mark_task("宏观数据", "error", str(e))

            # 2. 行业评分
            mark_task("行业评分", "running")
            try:
                results = score_all_industries(_db)
                mark_task("行业评分", "done", f"已评 {len(results)} 个行业")
            except Exception as e:
                mark_task("行业评分", "error", str(e))

            # 3. 长期信号刷新
            mark_task("长期信号刷新", "running")
            try:
                results = generate_all_signals(_db)
                mark_task("长期信号刷新", "done", f"已生成 {len(results)} 只信号")
            except Exception as e:
                mark_task("长期信号刷新", "error", str(e))

            # 4. 短期信号刷新（v200 加入）
            mark_task("短期信号刷新", "running")
            try:
                r = generate_all_short_signals(_db)
                mark_task("短期信号刷新", "done",
                          f"成功 {r['generated']} / 跳过 {r['skipped']} / 共 {r['total']} / 耗时 {r['elapsed_sec']}s")
            except Exception as e:
                mark_task("短期信号刷新", "error", str(e))
        finally:
            _db.close()

    background_tasks.add_task(_run)
    return {"message": "全量数据更新已启动"}


@router.get("/data/refresh-progress")
def get_refresh_progress_api():
    """查询一键更新进度"""
    from data.task_tracker import get_refresh_progress
    return get_refresh_progress().to_dict()


# ══════════════════════════════════════════════
# 数据管理接口
# ══════════════════════════════════════════════
@router.post("/data/update-macro")
def update_macro(background_tasks: BackgroundTasks):
    from database import SessionLocal as _SL
    def _run():
        _db = _SL()
        try: fetch_macro_data(_db)
        finally: _db.close()
    background_tasks.add_task(_run)
    return {"message": "宏观数据更新已启动"}


@router.post("/data/update-financials")
def update_financials(
    background_tasks: BackgroundTasks,
    limit: int = Query(100),
):
    from database import SessionLocal as _SL
    def _run():
        _db = _SL()
        try: fetch_all_financial_data(_db, limit)
        finally: _db.close()
    background_tasks.add_task(_run)
    return {"message": f"财报数据更新已启动（前 {limit} 只）"}


@router.post("/data/update-prices")
def update_prices(
    background_tasks: BackgroundTasks,
    limit: int = Query(0, description="0=全量"),
    mode: str = Query(
        "incremental",
        description=(
            "incremental(默认): 只对已有数据的股票增量补到今天，没数据的跳过（快）；"
            "full: 全部跑，没数据的从2010拉历史（耗时数小时）；"
            "init-missing: 只给没数据的股票做首次全量初始化"
        ),
    ),
):
    """批量拉取股票日K线。默认增量更新，速度快。首次部署/补新股历史用 mode=init-missing 或 full。"""
    if mode not in ("incremental", "full", "init-missing"):
        raise HTTPException(400, f"mode 必须是 incremental/full/init-missing，收到 {mode!r}")
    from database import SessionLocal as _SL
    def _run():
        _db = _SL()
        try: fetch_all_price_data(_db, limit, mode=mode)
        finally: _db.close()
    background_tasks.add_task(_run)
    return {"message": f"行情数据更新已启动（mode={mode}）"}


@router.post("/data/update-news/{code}")
def update_news(code: str, db: Session = Depends(get_db)):
    n = fetch_stock_news(db, code)
    m = analyze_all_news(db, code)
    return {"fetched": n, "analyzed": m}


# ══════════════════════════════════════════════
# 辅助序列化函数
# ══════════════════════════════════════════════
def _get_industry_score_map(db: Session) -> dict:
    """返回 {industry_code: {total_score, name}} 的字典，批量避免 N+1"""
    rows = db.query(Industry.code, Industry.name, Industry.total_score).all()
    return {r.code: {"name": r.name, "total_score": r.total_score} for r in rows}


def _stock_summary(s: Stock, industry_map: dict = None) -> dict:
    ind_info = (industry_map or {}).get(s.industry_code, {}) if s.industry_code else {}
    return {
        "code":                   s.code,
        "name":                   s.name,
        "industry_code":          s.industry_code,
        "industry_name":          ind_info.get("name"),
        "industry_score":         ind_info.get("total_score"),  # 行业评分（用于对比）
        "fundamental_score":      s.fundamental_score,
        "score_roe_quality":      s.score_roe_quality,
        "score_profit_growth":    s.score_profit_growth,
        "score_cashflow":         s.score_cashflow,
        "score_financial_health": s.score_financial_health,
        "score_valuation":        s.score_valuation,
        # 长期信号
        "composite_score":        s.composite_score,
        "signal":                 s.signal,
        "signal_reason":          s.signal_reason,
        "signal_updated":         str(s.signal_updated) if s.signal_updated else None,
        # 短期信号（v200 新增）
        "short_composite_score":  s.short_composite_score,
        "short_signal":           s.short_signal,
        "short_signal_reason":    s.short_signal_reason,
        "short_signal_updated":   str(s.short_signal_updated) if s.short_signal_updated else None,
        "short_score_momentum":   s.short_score_momentum,
        "short_score_volprice":   s.short_score_volprice,
        "short_score_macro":      s.short_score_macro,
        "short_score_tech":       s.short_score_tech,
        "short_score_news_heat":  s.short_score_news_heat,
    }


def _financial_dict(f) -> dict:
    return {
        "period":             str(f.period),
        "revenue":            f.revenue,
        "net_profit":         f.net_profit,
        "gross_margin":       f.gross_margin,
        "net_margin":         f.net_margin,
        "roe":                f.roe,
        "debt_ratio":         f.debt_ratio,
        "operating_cashflow": f.operating_cashflow,
        "free_cashflow":      f.free_cashflow,
        "fcf_ratio":          f.fcf_ratio,
    }


def _price_dict(p) -> dict:
    return {
        "date":   str(p.trade_date),
        "close":  p.close,
        "volume": p.volume,
        "pe_ttm": p.pe_ttm,
        "pb":     p.pb,
    }


def _news_dict(n: NewsItem) -> dict:
    return {
        "pub_date":       str(n.pub_date),
        "title":          n.title,
        "source":         n.source,
        "sentiment_score": n.sentiment_score,
        "sentiment_label": n.sentiment_label,
        "event_type":     n.event_type,
        "keywords":       n.keywords,
    }
