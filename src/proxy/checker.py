"""ProxyChecker: kiểm tra proxy concurrent bằng requests (ThreadPoolExecutor).

Mỗi proxy được test kết nối thật qua HTTP và HTTPS tới URL kiểm tra nhẹ
(mặc định httpbin.org/ip). Ghi nhận: alive, latency, protocol dùng được.
SOCKS cần PySocks (pip install "requests[socks]"); nếu thiếu thì fallback
kiểm tra TCP-connect (chỉ chứng minh port mở, latency vẫn đo được).
"""
from __future__ import annotations

import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests

log = logging.getLogger(__name__)

try:
    import socks  # noqa: F401  (PySocks, do requests[socks] cài)
    HAS_SOCKS = True
except ImportError:
    HAS_SOCKS = False

ProxyItem = Tuple[str, str]  # (address "IP:PORT", protocol)


@dataclass
class CheckResult:
    proxy: str
    protocol: str
    alive: bool
    latency: Optional[float] = None  # giây
    works_https: bool = False
    error: str = ""


def build_requests_proxies(address: str, protocol: str) -> dict:
    """Map (IP:PORT, protocol) -> dict proxies cho requests."""
    scheme = "http" if protocol in ("http", "https") else protocol
    url = f"{scheme}://{address}"
    return {"http": url, "https": url}


def tcp_probe(address: str, timeout: float) -> Optional[float]:
    """Fallback khi thiếu PySocks: đo thời gian TCP-connect, None nếu không mở."""
    host, _, port_s = address.partition(":")
    start = time.perf_counter()
    try:
        with socket.create_connection((host, int(port_s)), timeout=timeout):
            return time.perf_counter() - start
    except Exception:
        return None


class ProxyChecker:
    def __init__(self, http_url: str, https_url: str, timeout: float = 8,
                 workers: int = 30, user_agent: str = "proxy-pool-runner/1.0"):
        self.http_url = http_url
        self.https_url = https_url
        self.timeout = timeout
        self.workers = max(1, workers)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def check_one(self, proxy: str, protocol: str = "http",
                  direct: bool = False) -> CheckResult:
        """Kiểm tra 1 proxy. direct=True: nối thẳng không qua proxy (dùng cho test)."""
        if protocol in ("socks4", "socks5") and not HAS_SOCKS and not direct:
            latency = tcp_probe(proxy, self.timeout)
            if latency is not None:
                return CheckResult(proxy, protocol, True, latency, False, "tcp-only")
            return CheckResult(proxy, protocol, False, None, False, "tcp refused")

        proxies = None if direct else build_requests_proxies(proxy, protocol)
        # 1) HTTPS trước (khó hơn), 2) fallback HTTP.
        last = "no response"
        for url, is_https in ((self.https_url, True), (self.http_url, False)):
            start = time.perf_counter()
            try:
                r = self.session.get(url, proxies=proxies, timeout=self.timeout)
                latency = time.perf_counter() - start
                if r.status_code < 400:
                    return CheckResult(proxy, protocol, True, latency,
                                       works_https=is_https)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                continue
            except requests.RequestException as exc:
                last = f"{type(exc).__name__}: {exc}"
                continue
        return CheckResult(proxy, protocol, False, None, False, last)

    def check_all(self, items: List[ProxyItem],
                  on_result=None) -> Tuple[List[ProxyItem], List[str]]:
        """Check concurrent. Trả về (healthy[(addr, proto)], dead[addr])."""
        healthy: List[ProxyItem] = []
        dead: List[str] = []
        total = len(items)
        done = 0
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futures = {ex.submit(self.check_one, addr, proto): (addr, proto)
                       for addr, proto in items}
            for fut in as_completed(futures):
                addr, proto = futures[fut]
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001 - 1 proxy lỗi không kill run
                    res = CheckResult(addr, proto, False, None, False, str(exc))
                done += 1
                if res.alive:
                    healthy.append((addr, proto))
                else:
                    dead.append(addr)
                if on_result:
                    on_result(res)
                if done % max(1, total // 10) == 0 or done == total:
                    log.info("check %d/%d alive=%d", done, total, len(healthy))
        # Nhanh nhất trước để worker ưu tiên proxy tốt.
        return healthy, dead

    @staticmethod
    def save_lines(lines: List[str], path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
