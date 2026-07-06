"""pipeline.run · 编排入口 · collect → score → synthesize → render.

**v3.0.0 默认路径**：`run.py <ticker>` 默认走这里。`UZI_LEGACY=1` 才走 legacy.

Part 1（数据采集 + 打分 + 报告）始终自动执行，但跳过大佬模块数据。
大佬分析（世纪分歧/评委打分板/大佬群聊/大佬抄作业）需询问后才采集+计算。
UZI_AUTO_FULL=1 跳过询问直接全跑。
UZI_SKIP_PANEL=1 强制跳过大佬模块。

用法：
    from lib.pipeline.run import run_pipeline
    report_path = run_pipeline("300470.SZ")
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .collect import collect as pipeline_collect, collect_panel_data
from .score import score_part2, score_part3, score_part4
from .synthesize import synthesize_and_render


_EMPTY_PANEL = {"investors": [], "panel_consensus": 50, "signal_distribution": {}}


def run_pipeline(ticker: str, resume: bool = True) -> str:
    """完整管道入口.

    Returns:
        HTML 报告路径.
    """
    _preflight_guards(ticker)

    # ── Part 1 · 22 维数据采集（不含大佬模块数据）──
    print(f"🚀 [pipeline] Part 1 · 数据采集 · {ticker}")
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
    print(f"✅ [pipeline] Part 1 完成 · raw_data.json 已写入")

    # ── 22 维打分（始终执行，耗时短）──
    print(f"\n🔢 [pipeline] 22 维打分")
    score_part2(ticker)

    # ── 大佬分析模块询问 ──
    include_panel = _should_include_panel()
    if include_panel:
        # 采集大佬模块专用数据（fund_holders + similar_stocks）
        panel_data = collect_panel_data(ticker)
        # 合并到 raw_data.json
        raw = _load_raw(ticker)
        for k, v in panel_data.items():
            if k in ("fund_managers", "similar_stocks"):
                raw[k] = v
            else:
                raw.setdefault("dimensions", {})[k] = v
        _write_cache(ticker, raw)

        print(f"\n⚖️  [pipeline] 65 评委量化审判")
        score_part3(ticker)
    else:
        print(f"\n⏭️  [pipeline] 跳过大佬分析模块 · 生成精简报告")
        _write_empty_panel(ticker)

    # ── 综合研判 + 报告 ──
    print(f"\n📊 [pipeline] 综合研判 + 报告")
    score_part4(ticker)
    return synthesize_and_render(ticker)


def _should_include_panel() -> bool:
    """判断是否启动大佬分析模块."""
    if os.environ.get("UZI_SKIP_PANEL") == "1":
        return False
    if os.environ.get("UZI_AUTO_FULL") == "1":
        return True
    if not sys.stdin.isatty():
        return False
    try:
        print()
        print("━" * 50)
        print("📋 是否启动大佬分析模块？包含：")
        print("   · 世纪分歧（Great Divide 多空辩论）")
        print("   · 评委打分板（65 位投资大佬评分）")
        print("   · 大佬群聊现场（群贤议事厅）")
        print("   · 大佬抄作业（基金经理持仓）")
        print("   跳过可节省大量时间和 token")
        print("━" * 50)
        choice = input("启动？(y/N): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        choice = ""
    return choice in ("y", "yes")


def _write_empty_panel(ticker: str) -> None:
    """写空 panel.json."""
    cache = _cache_dir(ticker)
    (cache / "panel.json").write_text(
        json.dumps(_EMPTY_PANEL, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _cache_dir(ticker: str) -> Path:
    from lib.market_router import parse_ticker
    ti = parse_ticker(ticker)
    import run_real_test as rrt
    return Path(rrt.__file__).parent / ".cache" / ti.full


def _load_raw(ticker: str) -> dict:
    """读 raw_data.json."""
    p = _cache_dir(ticker) / "raw_data.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _preflight_guards(ticker: str) -> None:
    """pipeline 不覆盖的场景 · 抛异常让 run.py 回退 legacy."""
    from lib.market_router import is_chinese_name, parse_ticker, classify_security_type

    if is_chinese_name(ticker):
        raise ValueError(f"pipeline: 中文名 {ticker!r} 需 legacy 解析 · fallback")

    try:
        ti = parse_ticker(ticker)
        if ti.market == "A":
            sec_type = classify_security_type(ti.code)
            if sec_type in ("etf", "lof", "convertible_bond", "index"):
                raise ValueError(f"pipeline: {sec_type} 证券类型需 legacy 处理 · fallback")
    except ValueError:
        raise
    except Exception:
        pass


def _load_cache(ticker: str) -> dict:
    """读已有 raw_data.json · 用于 resume."""
    p = _cache_dir(ticker) / "raw_data.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache(ticker: str, raw: dict) -> None:
    """写 raw_data.json."""
    cache = _cache_dir(ticker)
    cache.mkdir(parents=True, exist_ok=True)
    try:
        (cache / "raw_data.json").write_text(
            json.dumps(raw, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    except Exception as e:
        print(f"   ⚠️ 写 cache 失败: {e}")
