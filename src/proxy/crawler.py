"""ProxyCrawler: lấy proxy từ các public source hợp pháp, chuẩn hóa IP:PORT.

Chỉ dùng HTTP GET tới file/API công khai, không auth, không bypass bất kỳ
cơ chế bảo vệ nào. Nguồn tham khảo (xem README để ghi license đầy đủ):
  - TheSpeedX/PROXY-List (MIT) — raw txt
  - monosans/proxy-list (MIT) — raw txt
  - proxifly/free-proxy-list — jsDelivr mirror
  - ProxyScrape/free-proxy-list (MIT code) + public API api.proxyscrape.com
  - api.openproxylist.xyz — plain text
  - proxylist.geonode.com — JSON API phân trang
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

import requests

log = logging.getLogger(__name__)

# Bắt "1.2.3.4:8080", kèm scheme prefix nếu có (để suy ra protocol).
PROXY_RE = re.compile(
    r"(?:(?P<scheme>https?|socks4|socks5)://)?"
    r"(?P<host>(?:\d{1,3}\.){3}\d{1,3})\s*:\s*(?P<port>\d{2,5})"
)

ProxyItem = Tuple[str, str]  # (address "IP:PORT", protocol)

SOURCES = [
    {"name": "thespeedx-http", "kind": "text", "protocol": "http",
     "url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"},
    {"name": "thespeedx-socks4", "kind": "text", "protocol": "socks4",
     "url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt"},
    {"name": "thespeedx-socks5", "kind": "text", "protocol": "socks5",
     "url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt"},
    {"name": "monosans-http", "kind": "text", "protocol": "http",
     "url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"},
    {"name": "monosans-socks4", "kind": "text", "protocol": "socks4",
     "url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt"},
    {"name": "monosans-socks5", "kind": "text", "protocol": "socks5",
     "url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt"},
    {"name": "proxifly-http", "kind": "text", "protocol": "http",
     "url": "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt"},
    {"name": "proxifly-socks5", "kind": "text", "protocol": "socks5",
     "url": "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/socks5/data.txt"},
    {"name": "openproxy-http", "kind": "text", "protocol": "http",
     "url": "https://api.openproxylist.xyz/http.txt"},
    {"name": "openproxy-socks5", "kind": "text", "protocol": "socks5",
     "url": "https://api.openproxylist.xyz/socks5.txt"},
    {"name": "proxyscrape-api-http", "kind": "proxyscrape", "protocol": "http",
     "url": "https://api.proxyscrape.com/v4/free-proxy-list/get"},
    {"name": "geonode-http", "kind": "geonode", "protocol": "http",
     "url": "https://proxylist.geonode.com/api/proxy-list"},
]


def _valid_octets(host: str) -> bool:
    return all(0 <= int(o) <= 255 for o in host.split("."))


def parse_items(text: str, default_protocol: str, source: str = "") -> List[ProxyItem]:
    """Trích IP:PORT từ text bất kỳ, validate, dedupe giữ thứ tự."""
    seen: set[tuple] = set()
    out: List[ProxyItem] = []
    for m in PROXY_RE.finditer(text or ""):
        host, port_s = m.group("host"), m.group("port")
        try:
            port = int(port_s)
        except ValueError:
            continue
        if not (1 <= port <= 65535) or not _valid_octets(host):
            continue
        scheme = (m.group("scheme") or default_protocol).lower()
        if scheme not in ("http", "https", "socks4", "socks5"):
            scheme = default_protocol
        key = (host, port, scheme)
        if key in seen:
            continue
        seen.add(key)
        out.append((f"{host}:{port}", scheme))
    return out


def deduplicate(items: List[ProxyItem]) -> List[ProxyItem]:
    seen: set[tuple] = set()
    unique: List[ProxyItem] = []
    for addr, proto in items:
        host, _, port = addr.partition(":")
        key = (host, port, proto)
        if key in seen:
            continue
        seen.add(key)
        unique.append((addr, proto))
    return unique


class ProxyCrawler:
    """Crawl concurrent các source public, giới hạn workers + retry hiền hòa."""

    def __init__(self, workers: int = 10, timeout: int = 15,
                 user_agent: str = "proxy-pool-runner/1.0"):
        self.workers = max(1, workers)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def _fetch_text(self, url: str, params: dict | None = None,
                    retries: int = 2) -> str:
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code == 429:  # tôn trọng rate limit, không né
                    log.warning("rate-limited (429): %s, bỏ qua source", url)
                    return ""
                r.raise_for_status()
                return r.text
            except Exception as exc:  # noqa: BLE001 - 1 source lỗi không kill run
                last = exc
                log.warning("fetch %s lỗi (lần %d): %s", url, attempt + 1, exc)
        log.error("bỏ source %s: %s", url, last)
        return ""

    def _fetch_source(self, src: dict) -> List[ProxyItem]:
        kind = src["kind"]
        if kind == "text":
            text = self._fetch_text(src["url"])
            return parse_items(text, src["protocol"], src["name"])
        if kind == "proxyscrape":
            text = self._fetch_text(src["url"], params={
                "request": "display_proxies", "protocol": "http",
                "proxy_format": "protocolipport", "format": "text"})
            return parse_items(text, src["protocol"], src["name"])
        if kind == "geonode":
            out: List[ProxyItem] = []
            for page in (1, 2):  # chỉ 2 trang đầu để nhẹ tải lên nguồn
                text = self._fetch_text(src["url"], params={
                    "limit": "200", "page": str(page),
                    "sort_by": "lastChecked", "sort_type": "desc",
                    "protocols": src["protocol"]})
                try:
                    rows = (__import__("json").loads(text) or {}).get("data", [])
                except Exception:
                    break
                if not rows:
                    break
                for row in rows:
                    out.extend(parse_items(
                        f"{row.get('ip', '')}:{row.get('port', '')}",
                        src["protocol"], src["name"]))
            return out
        return []

    def crawl(self, sources: list | None = None,
              on_batch=None) -> List[ProxyItem]:
        """Crawl concurrent. on_batch(batch) được gọi ngay khi mỗi source xong
        (từ thread của crawler) để pipeline vừa crawl vừa check."""
        sources = sources if sources is not None else SOURCES
        items: List[ProxyItem] = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futures = {ex.submit(self._fetch_source, s): s for s in sources}
            for fut in as_completed(futures):
                src = futures[fut]
                try:
                    batch = fut.result()
                except Exception as exc:  # noqa: BLE001
                    log.error("source %s crash: %s", src["name"], exc)
                    batch = []
                log.info("source %-22s -> %5d proxy", src["name"], len(batch))
                if on_batch is not None:
                    try:
                        on_batch(batch)
                    except Exception as exc:  # noqa: BLE001
                        log.error("on_batch lỗi: %s", exc)
                items.extend(batch)
        unique = deduplicate(items)
        log.info("crawl xong: %d raw -> %d unique", len(items), len(unique))
        return unique

    @staticmethod
    def save(items: List[ProxyItem], path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for addr, _ in items:
                f.write(addr + "\n")

    @staticmethod
    def load(path: str, protocol: str = "http") -> List[ProxyItem]:
        """Đọc file IP:PORT (kèm scheme prefix nếu có)."""
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            return []
        return parse_items(text, protocol, "file")
