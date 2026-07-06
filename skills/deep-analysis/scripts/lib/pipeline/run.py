"""pipeline.run · 编排入口 · collect → score → synthesize.

**v3.0.0 默认路径**：`run.py <ticker>` 默认走这里。`UZI_LEGACY=1` 才走 legacy.

用法：
    from lib.pipeline.run import run_pipeline
    report_path = run_pipeline("300470.SZ")
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .collect import collect as pipeline_collect
from .score import score_from_cache
from .synthesize import synthesize_and_render


def run_pipeline(ticker: str, resume: bool = True) -> str | None:
    """完整管道入口（v3.0.0 主干）.

    默认只跑 Part 1（数据采集）· Part 2-4 需用户确认后才执行。
    设置 UZI_AUTO_FULL=1 跳过确认一把跑完（适合 CI / 快速模式）。

    Returns:
        HTML 报告路径（str）· 如果仅完成 Part 1 返回 None.
    """
    _preflight_guards(ticker)

    # ── Part 1 · 22 维数据采集（始终自动执行）──
    print(f"🚀 [pipeline.run] Part 1 · 数据采集 · {ticker}")
    raw_previous = _load_cache(ticker) if resume else {}
    raw_dict = pipeline_collect(ticker, raw_previous=raw_previous, max_workers=6)

    from lib.market_router import parse_ticker as _parse_ticker
    _ti = _parse_ticker(ticker)
    _basic = raw_dict.get("0_basic") or {}
    _basic_market = _basic.get("market") if isinstance(_basic, dict) else None
    raw_data_compatible = {
        "ticker": ticker,
        "market": _basic_market if _basic_market in ("A", "H", "U") else _ti.market,
        "code": _ti.code,
        "full": _ti.full,
        "dimensions": {k: v for k, v in raw_dict.items()
                       if k not in ("fund_managers", "similar_stocks")},
    }
    for k in ("fund_managers", "similar_stocks"):
        if k in raw_dict:
            raw_data_compatible[k] = raw_dict[k]

    _write_cache(ticker, raw_data_compatible)
    print(f"✅ [pipeline.run] Part 1 完成 · raw_data.json 已写入")

    # ── Part 2-4 · 需确认后才执行 ──
    auto_full = os.environ.get("UZI_AUTO_FULL") == "1"
    if not auto_full:
        if sys.stdin.isatty():
            print()
            print("━" * 50)
            print("📋 Part 1 数据采集已完成。后续阶段：")
            print("   Part 2 · 22 维打分 + 定性判断")
            print("   Part 3 · 65 评委量化审判")
            print("   Part 4 · 综合研判 + 报告组装")
            print("━" * 50)
            try:
                choice = input("是否继续执行 Part 2-4？(y/N): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = ""
            if choice not in ("y", "yes"):
                print("   ℹ️  仅完成数据采集。Agent 可按需手动启动后续阶段。")
                return None
        else:
            print("   ℹ️  默认仅完成 Part 1 数据采集（设置 UZI_AUTO_FULL=1 自动执行全部）")
            return None

    # ── Part 2+3 · 打分 + 评委 ──
    print(f"\n🔢 [pipeline.run] Part 2+3 · scoring + panel")
    score_from_cache(ticker)

    # ── Part 4+5 · 综合研判 + 报告 ──
    print(f"\n📊 [pipeline.run] Part 4+5 · synthesize + render")
    return synthesize_and_render(ticker)


def _preflight_guards(ticker: str) -> None:
    """v3.0.0 · pipeline 不覆盖的场景 · 抛异常让 run.py 回退 legacy（legacy 有完整解析）."""
    from lib.market_router import is_chinese_name, parse_ticker, classify_security_type

    if is_chinese_name(ticker):
        raise ValueError(
            f"pipeline: 中文名 {ticker!r} 需 legacy 解析 · fallback"
        )

    try:
        ti = parse_ticker(ticker)
        if ti.market == "A":
            sec_type = classify_security_type(ti.code)
            if sec_type in ("etf", "lof", "convertible_bond", "index"):
                raise ValueError(
                    f"pipeline: {sec_type} 证券类型需 legacy 处理 · fallback"
                )
    except ValueError:
        raise
    except Exception:
        pass


def _load_cache(ticker: str) -> dict:
    """读已有 raw_data.json · 用于 resume."""
    from lib.market_router import parse_ticker
    ti = parse_ticker(ticker)
    import run_real_test as rrt
    cache_path = Path(rrt.__file__).parent / ".cache" / ti.full / "raw_data.json"
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache(ticker: str, raw: dict) -> None:
    """写 raw_data.json · 让 legacy stage1 的 resume 能复用."""
    from lib.market_router import parse_ticker
    ti = parse_ticker(ticker)
    import run_real_test as rrt
    cache_dir = Path(rrt.__file__).parent / ".cache" / ti.full
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "raw_data.json"
    try:
        cache_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        print(f"   ⚠️ 写 cache 失败: {e}")
