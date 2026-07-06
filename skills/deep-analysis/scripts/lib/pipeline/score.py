"""pipeline.score · Scoring 阶段 · v3.0.0 Phase 6c · 真正接管主干.

拆分为 3 个独立阶段供 run_pipeline 按需调用：
  - score_part2(ticker) → autofill + score_dimensions → dimensions.json
  - score_part3(ticker) → generate_panel → panel.json（依赖 dimensions.json）
  - score_part4(ticker) → generate_synthesis → synthesis.json（依赖 dimensions + panel）

每个阶段只在用户确认后才执行，减少不必要的计算和 token 消耗。
"""
from __future__ import annotations

import json
from pathlib import Path


def _cache_dir(ticker: str) -> Path:
    from lib.market_router import parse_ticker
    ti = parse_ticker(ticker)
    import run_real_test as rrt
    return Path(rrt.__file__).parent / ".cache" / ti.full


def score_part2(ticker: str) -> dict:
    """Part 2 · autofill 定性维度 + 22 维打分 → dimensions.json."""
    import run_real_test as rrt
    from lib.market_router import parse_ticker

    ti = parse_ticker(ticker)
    cache = _cache_dir(ticker)
    raw_path = cache / "raw_data.json"
    if not raw_path.exists():
        raise FileNotFoundError(f"score_part2: {raw_path} 不存在 · 必须先跑 Part 1")

    raw = json.loads(raw_path.read_text(encoding="utf-8"))

    try:
        rrt._autofill_qualitative_via_mx(raw, ti.full)
    except Exception as e:
        print(f"   ⚠️ autofill_via_mx 跳过: {type(e).__name__}: {str(e)[:80]}")

    try:
        from lib.playwright_fallback import autofill_via_playwright
        autofill_via_playwright(raw, ti.full)
    except Exception as e:
        print(f"   ⚠️ Playwright 兜底跳过: {type(e).__name__}: {str(e)[:80]}")

    raw_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    dims_scored = rrt.score_dimensions(raw)
    (cache / "dimensions.json").write_text(
        json.dumps(dims_scored, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return dims_scored


def score_part3(ticker: str) -> dict:
    """Part 3 · 65 评委投票 → panel.json（需要 dimensions.json + raw_data.json）."""
    import run_real_test as rrt

    cache = _cache_dir(ticker)
    raw = json.loads((cache / "raw_data.json").read_text(encoding="utf-8"))
    dims_scored = json.loads((cache / "dimensions.json").read_text(encoding="utf-8"))

    panel = rrt.generate_panel(dims_scored, raw)
    (cache / "panel.json").write_text(
        json.dumps(panel, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return panel


def score_part4(ticker: str) -> dict:
    """Part 4 前置 · 综合研判 → synthesis.json（需要 raw + dimensions + panel）."""
    import run_real_test as rrt

    cache = _cache_dir(ticker)
    raw = json.loads((cache / "raw_data.json").read_text(encoding="utf-8"))
    dims_scored = json.loads((cache / "dimensions.json").read_text(encoding="utf-8"))
    panel = json.loads((cache / "panel.json").read_text(encoding="utf-8"))

    synthesis = rrt.generate_synthesis(raw, dims_scored, panel, agent_analysis=None)
    (cache / "synthesis.json").write_text(
        json.dumps(synthesis, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return synthesis


def score_from_cache(ticker: str) -> dict:
    """兼容接口 · 一次跑完 Part 2+3+4 synthesis."""
    dims_scored = score_part2(ticker)
    panel = score_part3(ticker)
    synthesis = score_part4(ticker)
    return {"dimensions": dims_scored, "panel": panel, "synthesis": synthesis}
