"""Wind Cloud API provider · WIND_API_KEY · 每日 1000 积分限额.

万得 Wind 云端 MCP 接口，覆盖 A 股硬数据：
  · 财报深度 (get_stock_fundamentals)
  · K线行情 (get_stock_kline)
  · 估值指标 (get_stock_price_indicators)
  · 资金面/技术面 (get_stock_technicals)

积分耗尽后 is_available() 返回 False，自动 fallback 到 akshare 等免费源。

配置：~/.wind-aifinmarket/config 中 WIND_API_KEY=xxx
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date
from pathlib import Path

from . import Provider, register, ProviderError

try:
    import requests as _requests
    _REQ_OK = True
except ImportError:
    _requests = None
    _REQ_OK = False

_QUOTA_RE = re.compile(
    r"单日请求次数超限|daily.*limit|余额不足|请先充值"
    r"|insufficient.*balance|请求过于频繁|qps.*limit|too.*frequent",
    re.IGNORECASE,
)

_CFG_DIR = Path.home() / ".wind-aifinmarket"
_CFG_FILE = _CFG_DIR / "config"
_CREDIT_FILE = _CFG_DIR / "credit_usage.json"


def _read_api_key() -> str:
    if _CFG_FILE.exists():
        try:
            for raw in _CFG_FILE.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "WIND_API_KEY":
                    return v.strip().strip("\"'")
        except Exception:
            pass
    return os.environ.get("WIND_API_KEY", "").strip()


class _CreditTracker:
    def __init__(self, limit: int = 1000):
        self._limit = limit
        self._date = ""
        self._used = 0
        self._exhausted = False
        self._load()

    def _load(self):
        today = date.today().isoformat()
        try:
            if _CREDIT_FILE.exists():
                d = json.loads(_CREDIT_FILE.read_text(encoding="utf-8"))
                if d.get("date") == today:
                    self._date = today
                    self._used = d.get("used", 0)
                    self._exhausted = d.get("exhausted", False)
                    return
        except Exception:
            pass
        self._date = today
        self._used = 0
        self._exhausted = False

    def _save(self):
        try:
            _CFG_DIR.mkdir(parents=True, exist_ok=True)
            _CREDIT_FILE.write_text(json.dumps({
                "date": self._date, "used": self._used,
                "exhausted": self._exhausted, "limit": self._limit,
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def can_use(self) -> bool:
        today = date.today().isoformat()
        if today != self._date:
            self._date = today
            self._used = 0
            self._exhausted = False
            self._save()
        return not self._exhausted and self._used < self._limit

    def record(self):
        self._used += 1
        self._save()

    def mark_exhausted(self):
        self._exhausted = True
        self._save()

    @property
    def remaining(self) -> int:
        return max(0, self._limit - self._used)


class _WindProvider:
    name = "wind"
    requires_key = True
    markets = ("A",)

    _tracker = _CreditTracker(limit=1000)

    def is_available(self) -> bool:
        if not _REQ_OK:
            return False
        if not _read_api_key():
            return False
        return self._tracker.can_use()

    @staticmethod
    def _windcode(code: str) -> str:
        c = code.split(".")[0].zfill(6)
        if c.startswith(("60", "68", "90", "50", "51", "52", "56", "58", "10", "11")):
            return f"{c}.SH"
        if c.startswith(("83", "87", "88", "92")):
            return f"{c}.BJ"
        return f"{c}.SZ"

    # ── Wind MCP HTTP 调用 ──

    def _call(self, server_type: str, tool: str, args: dict) -> str:
        """JSON-RPC 2.0 → Wind MCP，返回 content[0].text 原文."""
        if not self._tracker.can_use():
            raise ProviderError("Wind 每日积分已耗尽")

        api_key = _read_api_key()
        if not api_key:
            raise ProviderError("WIND_API_KEY 未配置")

        url = f"https://mcp.wind.com.cn/vserver_{server_type}/mcp/"
        hdrs = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }

        # initialize handshake
        self._post(url, hdrs, {
            "jsonrpc": "2.0", "id": int(time.time() * 1000),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "uzi-skill-wind", "version": "1.0"},
            },
        }, timeout=30)

        # tools/call
        payload = self._post(url, hdrs, {
            "jsonrpc": "2.0", "id": int(time.time() * 1000) + 1,
            "method": "tools/call",
            "params": {
                "name": tool,
                "arguments": args,
                "_meta": {"clientVersion": "1.9.3"},
            },
        }, timeout=120)

        self._check_errors(payload)
        self._tracker.record()

        content = payload.get("result", {}).get("content", [])
        if content and isinstance(content[0], dict):
            return content[0].get("text", "")
        return json.dumps(payload.get("result", {}), ensure_ascii=False)

    def _post(self, url: str, headers: dict, body: dict, timeout: int) -> dict:
        try:
            r = _requests.post(url, headers=headers,
                               data=json.dumps(body), timeout=timeout)
        except Exception as e:
            raise ProviderError(f"Wind 网络错误: {e}")
        if r.status_code == 429:
            self._tracker.mark_exhausted()
            raise ProviderError("Wind API 限流")
        if r.status_code == 401:
            raise ProviderError("Wind API Key 无效")
        if not r.ok:
            raise ProviderError(f"Wind HTTP {r.status_code}")
        return self._parse_sse(r.text)

    @staticmethod
    def _parse_sse(text: str) -> dict:
        text = text.strip()
        if text.startswith("{"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        last = None
        for line in text.split("\n"):
            if line.startswith("data: "):
                last = line[6:]
        if last:
            return json.loads(last)
        raise ProviderError(f"Wind 响应格式无法识别: {text[:200]}")

    def _check_errors(self, payload: dict):
        if "error" in payload:
            msg = payload["error"].get("message", str(payload["error"]))
            self._raise_if_quota(msg)
            raise ProviderError(f"Wind: {msg}")

        result = payload.get("result", {})
        if result.get("isError"):
            msg = ""
            c = result.get("content", [])
            if c and isinstance(c[0], dict):
                msg = c[0].get("text", str(result))
            self._raise_if_quota(msg)
            raise ProviderError(f"Wind 工具错误: {msg[:300]}")

        # inner error: some tools embed errors inside content[0].text JSON
        c = result.get("content", [])
        if c and isinstance(c[0], dict):
            raw = c[0].get("text", "")
            try:
                inner = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return
            if isinstance(inner, dict):
                if inner.get("mcp_tool_error_code", 0) != 0:
                    msg = inner.get("mcp_tool_error_msg", str(inner))
                    self._raise_if_quota(msg)
                    raise ProviderError(f"Wind: {msg[:300]}")
                err = inner.get("error")
                if isinstance(err, dict) and (err.get("code") or err.get("message")):
                    msg = f"{err.get('code', '')}: {err.get('message', '')}"
                    self._raise_if_quota(msg)
                    raise ProviderError(f"Wind: {msg[:300]}")

    def _raise_if_quota(self, msg: str):
        if _QUOTA_RE.search(str(msg)):
            self._tracker.mark_exhausted()
            raise ProviderError(f"Wind 积分耗尽: {msg[:200]}")

    # ── 维度接口 ──

    def fetch_financials_a(self, code: str, years: int = 5) -> dict:
        wc = self._windcode(code)
        yr = date.today().year
        q = f"{wc} {yr - years}-{yr} 营业总收入 净利润 毛利率 净利率 ROE加权 资产负债率 经营现金流"
        raw = self._call("stock_data", "get_stock_fundamentals", {"question": q})
        return {"ok": True, "raw": raw, "source": "wind"}

    _KLINE_COL_MAP = {
        "TIME": "日期", "_DATE": None, "OPEN": "开盘", "MATCH": "收盘",
        "HIGH": "最高", "LOW": "最低", "VOLUME": "成交量",
        "TURNOVER": "成交额", "CHANGEHANDRATE": "换手率", "AVPRICE": "均价",
    }

    def fetch_kline_a(self, code: str, period: str = "daily",
                      start: str = "20200101") -> list[dict]:
        wc = self._windcode(code)
        end = date.today().strftime("%Y%m%d")
        raw = self._call("stock_data", "get_stock_kline", {
            "windcode": wc, "begin_date": start, "end_date": end,
        })
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return [{"raw": raw, "source": "wind"}]
        data = parsed.get("data", parsed) if isinstance(parsed, dict) else parsed
        if isinstance(data, list):
            return data
        if not isinstance(data, dict) or "columns" not in data or "rows" not in data:
            return [{"raw": raw, "source": "wind"}]
        cols = [c["name"] if isinstance(c, dict) else c for c in data["columns"]]
        rows = []
        for r in data["rows"]:
            rec = {}
            for i, v in enumerate(r):
                if i >= len(cols):
                    break
                cn = self._KLINE_COL_MAP.get(cols[i], cols[i])
                if cn is None:
                    continue
                if cn == "日期" and isinstance(v, str) and len(v) >= 10:
                    v = v[:10]
                else:
                    try:
                        v = float(v)
                    except (ValueError, TypeError):
                        pass
                rec[cn] = v
            rows.append(rec)
        return rows

    def fetch_valuation_a(self, code: str) -> dict:
        wc = self._windcode(code)
        raw = self._call("stock_data", "get_stock_price_indicators", {
            "windcode": wc,
            "indexes": "市盈率(TTM),市净率(LF),股息率,总市值2,流通市值",
        })
        return {"ok": True, "raw": raw, "source": "wind"}

    def fetch_capital_flow_a(self, code: str) -> dict:
        wc = self._windcode(code)
        q = f"{wc} 近60日融资融券余额变化 主力资金净流入 龙虎榜"
        raw = self._call("stock_data", "get_stock_technicals", {"question": q})
        return {"ok": True, "raw": raw, "source": "wind"}


register(_WindProvider())
