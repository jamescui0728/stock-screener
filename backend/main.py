"""
FastAPI 应用入口
启动顺序：建表 → 初始化基础数据 → 启动定时任务 → 挂载路由
"""
import logging
import sys
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.auth_routes import router as auth_router
from api.routes import router
from config import settings
from database import init_db, SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


# ──────────────────────────────────────────────
# 定时任务
# ──────────────────────────────────────────────
def _daily_data_update():
    """每天凌晨 2 点：宏观数据 → 财报 → 情感分析 → 长期信号 → 短期信号"""
    from data.fetcher import fetch_macro_data, fetch_all_financial_data
    from data.sentiment import analyze_all_news
    from engines.signal_engine import generate_all_signals
    from engines.short_signal_engine import generate_all_short_signals

    db = SessionLocal()
    try:
        logger.info("定时任务：开始每日数据更新")
        fetch_macro_data(db)
        fetch_all_financial_data(db, limit=200)
        analyze_all_news(db)
        generate_all_signals(db)
        # v200 加入：短期信号也需要每日刷新（依赖最新宏观 + 价格 + 新闻）
        generate_all_short_signals(db)
        logger.info("定时任务：每日更新完成")
    except Exception as e:
        logger.error(f"定时任务失败: {e}")
    finally:
        db.close()


def _weekday_refresh_watchlist_news():
    """工作日 09:00：汇总所有用户自选股的最新消息"""
    from data.fetcher import fetch_stock_news
    from data.sentiment import analyze_all_news
    from models.models import Watchlist

    db = SessionLocal()
    try:
        # 收集所有用户的自选股代码（去重），避免多用户重复抓取
        codes = {r[0] for r in db.query(Watchlist.stock_code).distinct().all()}
        if not codes:
            logger.info("定时任务：自选股为空，跳过新闻刷新")
            return
        logger.info(f"定时任务：开始刷新 {len(codes)} 只自选股的消息")
        fetched = analyzed = 0
        for code in codes:
            try:
                fetched  += fetch_stock_news(db, code)
                analyzed += analyze_all_news(db, code)
            except Exception as e:
                logger.warning(f"  ↳ {code} 刷新失败: {e}")
        logger.info(f"定时任务：自选股消息刷新完成（新增 {fetched} 条，分析 {analyzed} 条）")
    except Exception as e:
        logger.error(f"自选股消息定时刷新失败: {e}")
    finally:
        db.close()


def _weekly_rescore():
    """每周日凌晨 3 点：重新计算行业评分"""
    from engines.industry_scorer import score_all_industries
    db = SessionLocal()
    try:
        logger.info("定时任务：重新计算行业评分")
        score_all_industries(db)
        logger.info("定时任务：行业评分完成")
    except Exception as e:
        logger.error(f"行业评分失败: {e}")
    finally:
        db.close()


def _refresh_paper_price_cache():
    """
    定期刷新所有用户持仓的实时价缓存（保证用户打开模拟盘页面时是热的）。
    跑在 A 股交易时段（每 8 分钟），不阻塞主任务。

    设计上限：单次预热假设持仓股 <= 100 只，否则 worker 池（16）会成为瓶颈，
    线性外推 100 / 16 × 8s ≈ 50s。超过 100 只会打警告，仍跑但首次访问可能受影响。
    """
    from engines.paper_trade import _warmup_prices_parallel
    from models.models import PaperPosition

    db = SessionLocal()
    try:
        codes = [r[0] for r in db.query(PaperPosition.stock_code).distinct().all()]
        if not codes:
            logger.debug("实时价缓存预热：无持仓，跳过")
            return
        if len(codes) > 100:
            logger.warning(
                f"实时价缓存预热：去重后 {len(codes)} 只持仓股，超过 100 只软上限。"
                f"预计耗时 ~{len(codes) // 16 * 8}s，期间用户首次访问可能仍要等 sina。"
            )
        logger.info(f"实时价缓存预热: 刷新 {len(codes)} 只持仓...")
        _warmup_prices_parallel(db, codes, per_call_timeout=8)
        logger.debug("实时价缓存刷新完成")
    except Exception as e:
        # 双重保险：_warmup_prices_parallel 内部已吞 per-future 异常，但留这里防御外层
        # （比如 db.query 失败 / SessionLocal 拿不到连接）
        logger.warning(f"实时价缓存刷新失败: {e}")
    finally:
        db.close()


def _startup_prewarm_paper_prices():
    """
    服务启动后立即异步预热持仓价缓存，避免用户首次访问模拟盘页面时
    要等 10+ 秒拉 sina。不阻塞 startup（在 thread 里跑）。
    """
    import threading
    threading.Thread(target=_refresh_paper_price_cache, name="paper_price_prewarm",
                     daemon=True).start()


# ──────────────────────────────────────────────
# 应用生命周期
# ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 启动 ──
    logger.info("初始化数据库...")
    init_db()

    # 幂等 schema 迁移：补齐历史 volume 里缺失的新列 / 索引
    # （避免 Docker 重建镜像后跑 SELECT 新列时 OperationalError）
    from auto_migrate import run_auto_migrations
    _mig_db = SessionLocal()
    try:
        run_auto_migrations(_mig_db)
    finally:
        _mig_db.close()

    logger.info("注册定时任务...")
    scheduler.add_job(
        _daily_data_update, "cron",
        hour=2, minute=0, id="daily_update",
    )
    scheduler.add_job(
        _weekly_rescore, "cron",
        day_of_week="sun", hour=3, minute=0, id="weekly_rescore",
    )
    scheduler.add_job(
        _weekday_refresh_watchlist_news, "cron",
        day_of_week="mon-fri", hour=9, minute=0, id="weekday_watchlist_news",
    )
    # A 股交易时段精细预热持仓实时价（每 8 分钟一次，缓存 TTL 10 分钟，永不过期）
    # 早盘段 9:00-11:59（覆盖正式交易 9:30-11:30 + 集合竞价前 30 分钟 + 午休 30 分钟内的盘后价）
    # 午后段 13:00-15:59（覆盖正式交易 13:00-15:00 + 收盘后 1 小时的盘后价）
    # 注：午休 12:00-12:59 与全天 16:00 后不跑，避免无谓打 sina
    scheduler.add_job(
        _refresh_paper_price_cache, "cron",
        day_of_week="mon-fri", hour="9-11", minute="*/8",
        id="paper_price_warmup_am",
    )
    scheduler.add_job(
        _refresh_paper_price_cache, "cron",
        day_of_week="mon-fri", hour="13-15", minute="*/8",
        id="paper_price_warmup_pm",
    )
    scheduler.start()

    # 启动后立即异步预热一次（不阻塞 ready）
    _startup_prewarm_paper_prices()

    logger.info("服务启动完成 ✓")

    yield

    # ── 关闭 ──
    scheduler.shutdown(wait=False)
    logger.info("服务已关闭")


# ──────────────────────────────────────────────
# FastAPI 应用
# ──────────────────────────────────────────────
app = FastAPI(
    title="长期价值股票筛选系统",
    description="基于 AKShare 数据的行业 + 公司两级筛选，含回测与买卖信号",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


# ──────────────────────────────────────────────
# 项目文档浏览（渲染 docs/*.md 为 HTML）
# 路径：
#   GET /readme              → README.md
#   GET /project-docs/       → 文档索引（列出 docs/ 下所有 .md）
#   GET /project-docs/{name} → 渲染指定文档（不带 .md 后缀也可）
# ──────────────────────────────────────────────
from pathlib import Path
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

_PROJECT_ROOT = Path(__file__).parent.parent          # stock-screener/
_DOCS_DIR     = _PROJECT_ROOT / "docs"                # stock-screener/docs/
_README       = _PROJECT_ROOT / "README.md"

_DOC_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>{title} · 长期价值股票筛选系统</title>
  <style>
    body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
            max-width: 920px; margin: 32px auto; padding: 0 24px; line-height: 1.7;
            color: #24292f; background: #ffffff; }}
    h1, h2, h3, h4 {{ color: #1f2328; margin-top: 1.5em; line-height: 1.3; }}
    h1 {{ border-bottom: 2px solid #d0d7de; padding-bottom: .3em; }}
    h2 {{ border-bottom: 1px solid #eaeef2; padding-bottom: .2em; }}
    code {{ background: #f6f8fa; padding: 2px 6px; border-radius: 4px;
            font-family: SF Mono, Menlo, Consolas, monospace; font-size: 0.9em; }}
    pre {{ background: #f6f8fa; padding: 14px 18px; border-radius: 6px;
           overflow-x: auto; line-height: 1.45; }}
    pre code {{ background: none; padding: 0; }}
    table {{ border-collapse: collapse; margin: 12px 0; width: 100%; }}
    th, td {{ border: 1px solid #d0d7de; padding: 8px 12px; text-align: left; }}
    th {{ background: #f6f8fa; }}
    blockquote {{ border-left: 4px solid #d0d7de; color: #59636e;
                  margin: 12px 0; padding: 0 16px; }}
    a {{ color: #0969da; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .nav {{ font-size: 13px; color: #59636e; margin-bottom: 24px; }}
    .nav a {{ margin-right: 14px; }}
  </style>
</head>
<body>
  <div class="nav">
    <a href="/readme">📖 README</a>
    <a href="/project-docs/ARCHITECTURE">🏗️ 架构</a>
    <a href="/project-docs/API">📡 API</a>
    <a href="/project-docs/DEVELOPMENT">🛠️ 开发</a>
    <a href="/project-docs/CHANGELOG">📝 变更</a>
    <a href="/docs">⚡ OpenAPI</a>
  </div>
  {body}
</body>
</html>"""


def _render_md_file(md_path: Path, title: str) -> HTMLResponse:
    """读取 markdown 文件并渲染成带导航的 HTML 页面。"""
    if not md_path.exists():
        raise HTTPException(404, f"文档不存在：{md_path.name}")
    import markdown as _md
    text = md_path.read_text(encoding="utf-8")
    html_body = _md.markdown(
        text,
        extensions=["fenced_code", "tables", "toc", "codehilite"],
        extension_configs={"codehilite": {"css_class": "highlight", "guess_lang": False}},
    )
    return HTMLResponse(_DOC_HTML_TEMPLATE.format(title=title, body=html_body))


@app.get("/readme", response_class=HTMLResponse)
def show_readme():
    """渲染项目根目录的 README.md"""
    return _render_md_file(_README, "项目总览")


@app.get("/project-docs/", response_class=HTMLResponse)
def docs_index():
    """文档索引：列出 docs/ 下所有 .md 文件"""
    if not _DOCS_DIR.exists():
        raise HTTPException(404, "docs/ 目录不存在")
    files = sorted(_DOCS_DIR.glob("*.md"))
    items = "\n".join(
        f'<li><a href="/project-docs/{f.stem}">{f.stem}</a> '
        f'<span style="color:#888">({f.stat().st_size:,} bytes)</span></li>'
        for f in files
    )
    body = f"""<h1>项目文档</h1>
    <p>从 <code>docs/</code> 目录加载的全部 markdown 文档：</p>
    <ul>{items}</ul>
    <p style="color:#666;margin-top:24px">提示：访问 <a href="/readme">/readme</a> 查看项目总览。</p>"""
    return HTMLResponse(_DOC_HTML_TEMPLATE.format(title="文档索引", body=body))


@app.get("/project-docs/{name}", response_class=HTMLResponse)
def show_doc(name: str):
    """渲染 docs/<name>.md（name 不带扩展名也可）"""
    # 安全：阻止路径穿越
    if "/" in name or ".." in name or name.startswith("."):
        raise HTTPException(400, "非法文档名")
    if not name.endswith(".md"):
        name = name + ".md"
    return _render_md_file(_DOCS_DIR / name, name[:-3])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
