"""Pipeline crawl + check chạy song song (streaming).

Thay vì: crawl hết (đợi) -> check hết (đợi), pipeline đẩy batch của mỗi
source sang checker ngay khi source đó xong. Tổng thời gian ≈ max(crawl,
check) thay vì tổng hai giai đoạn. Dedupe liên tục qua các batch bằng
tập seen thread-safe nên checker không bao giờ check trùng.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Optional

log = logging.getLogger(__name__)


class CrawlCheckPipeline:
    def __init__(self, crawler, checker):
        self.crawler = crawler
        self.checker = checker
        self._lock = threading.Lock()
        self._seen: set[tuple] = set()
        self._futures: list = []
        self._ex: Optional[ThreadPoolExecutor] = None

    @staticmethod
    def _key(addr: str, proto: str) -> tuple:
        host, _, port = addr.partition(":")
        return (host, port, proto)

    def _on_batch(self, batch) -> None:
        fresh = []
        with self._lock:
            for addr, proto in batch:
                key = self._key(addr, proto)
                if key in self._seen:
                    continue
                self._seen.add(key)
                fresh.append((addr, proto))
            assert self._ex is not None
            for addr, proto in fresh:
                self._futures.append(
                    self._ex.submit(self.checker.check_one, addr, proto))
        if fresh:
            log.debug("pipeline: +%d proxy mới -> checker", len(fresh))

    def run(self, sources: list | None = None,
            on_result: Optional[Callable] = None):
        """Chạy pipeline. Trả về (raw_items, healthy[(addr, proto)], dead[addr])."""
        import time as _time
        started = _time.perf_counter()
        self._ex = ThreadPoolExecutor(max_workers=self.checker.workers)
        try:
            raw_items = self.crawler.crawl(sources, on_batch=self._on_batch)
            total = len(self._futures)
            log.info("crawl xong, chờ %d check (checker=%d workers)...",
                     total, self.checker.workers)
            results = []
            done = 0
            step = max(100, total // 100)  # ~100 dòng tiến trình cho cả run
            for fut in as_completed(self._futures):
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001
                    log.error("check lỗi: %s", exc)
                    continue
                results.append(res)
                done += 1
                if on_result:
                    on_result(res)
                if done % step == 0 or done == total:
                    elapsed = _time.perf_counter() - started
                    rate = done / elapsed if elapsed > 0 else 0
                    alive_n = sum(1 for r in results if r.alive)
                    eta = (total - done) / rate if rate > 0 else 0
                    log.info("check %d/%d (%d%%) alive=%d %.1f/s ETA~%.0fs",
                             done, total, 100 * done // max(1, total),
                             alive_n, rate, eta)
        finally:
            if self._ex is not None:
                self._ex.shutdown(wait=True)
        alive_sorted = sorted((r for r in results if r.alive),
                              key=lambda r: (r.latency is None, r.latency or 0))
        healthy = [(r.proxy, r.protocol) for r in alive_sorted]
        dead = [r.proxy for r in results if not r.alive]
        log.info("pipeline xong: raw=%d unique=%d alive=%d dead=%d",
                 len(raw_items), len(self._seen), len(healthy), len(dead))
        return raw_items, healthy, dead
