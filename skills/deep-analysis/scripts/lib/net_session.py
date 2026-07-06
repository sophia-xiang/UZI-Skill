"""国内/境外 HTTP Session 工厂.

VPN（Clash TUN/全局模式）下，改 os.environ 无法可靠绕过代理。
用 trust_env=False 的 requests.Session 才能真正直连国内财经 API。
"""
from __future__ import annotations

import requests

_CN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}


def cn_session() -> requests.Session:
    """国内财经源专用：绕过 VPN 代理 + 浏览器头.

    trust_env=False 让 session 完全无视 HTTP_PROXY 环境变量，
    即使 Clash 是系统代理也不会被套用。
    """
    s = requests.Session()
    s.trust_env = False
    s.proxies = {"http": None, "https": None}
    s.headers.update(_CN_HEADERS)
    return s


def overseas_session() -> requests.Session:
    """境外源（WGC/JPM/LME 等）：保留 VPN 代理."""
    return requests.Session()
