"""动态申万二级行业映射：股票代码 → 申万二级行业名称。

替代硬编码行业表。数据源（VPN 下稳定）：
  ak.sw_index_second_info()      —— 全部申万二级行业（代码 + 名称）
  ak.index_component_sw(code)    —— 每个二级行业的成分股

首次调用遍历 ~130 个二级行业成分股，构建 {6位代码: 二级行业名} 映射并
缓存到本地（默认 30 天）；之后为 O(1) 字典查询。存储的名称即申万口径，
可被 fetch_industry._sw_industry_metrics 精确匹配，彻底消除证监会 vs 申万
命名错配（如"畜牧业" ↔ "养殖业"）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

try:
    import akshare as ak
except ImportError:
    ak = None

# 缓存文件放在模块目录，跨运行持久（分类极少变，30 天刷新一次）
_CACHE_FILE = Path(__file__).with_name("_sw_industry_map.json")
_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 天
_MEM_CACHE: dict | None = None
_BUILD_ATTEMPTED = False  # 本进程内只尝试构建一次，避免网络故障时反复卡顿


def _norm(code: str) -> str:
    """归一化为 6 位数字代码（补前导零）。"""
    return str(code).strip().zfill(6)[-6:]


def _load_disk() -> dict | None:
    if not _CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        if time.time() - payload.get("_built_at", 0) < _TTL_SECONDS:
            m = payload.get("map")
            return m if m else None
    except Exception:
        pass
    return None


def _save_disk(mapping: dict) -> None:
    try:
        _CACHE_FILE.write_text(
            json.dumps({"_built_at": time.time(), "map": mapping}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _build_map() -> dict:
    """遍历申万二级行业成分股，构建 {6位代码: 二级行业名}。

    约 130 次 index_component_sw 调用（每次 ~0.1s）。若网络故障导致
    连续多次失败则提前放弃（返回空，交由上层 baostock 兜底）。
    """
    if ak is None:
        return {}
    try:
        info = ak.sw_index_second_info()
    except Exception:
        return {}
    name_col = next((c for c in info.columns if "名称" in c), None)
    code_col = next((c for c in info.columns if "代码" in c), None)
    if not name_col or not code_col:
        return {}

    mapping: dict[str, str] = {}
    attempts = 0
    successes = 0
    for _, r in info.iterrows():
        # 去掉罗马数字后缀，得到干净的申万二级名（"白酒Ⅱ"→"白酒"）
        name = str(r[name_col]).replace("Ⅱ", "").replace("Ⅲ", "").strip()
        idx_code = str(r[code_col]).split(".")[0]  # "801017.SI" -> "801017"
        attempts += 1
        try:
            cons = ak.index_component_sw(symbol=idx_code)
        except Exception:
            # 网络整体不可用时提前放弃，避免 130 次超时累积
            if successes == 0 and attempts >= 8:
                return {}
            continue
        successes += 1
        ccol = next((c for c in cons.columns if "代码" in c), None)
        if not ccol:
            continue
        for c in cons[ccol].astype(str):
            mapping.setdefault(_norm(c), name)
    return mapping


def get_sw_industry(code: str, allow_build: bool = True) -> str | None:
    """返回股票 6 位代码对应的申万二级行业名；未知/不可用返回 None。

    优先级：内存缓存 → 磁盘缓存（30 天内）→（allow_build 时）现场构建并落盘。
    构建在单进程内只尝试一次。
    """
    global _MEM_CACHE, _BUILD_ATTEMPTED
    key = _norm(code)

    if _MEM_CACHE is None:
        _MEM_CACHE = _load_disk()
    if _MEM_CACHE:
        return _MEM_CACHE.get(key)

    if not allow_build or _BUILD_ATTEMPTED:
        return None

    _BUILD_ATTEMPTED = True
    built = _build_map()
    if built:
        _MEM_CACHE = built
        _save_disk(built)
        return built.get(key)
    return None
