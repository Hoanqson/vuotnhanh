"""Runner: multi-thread workers qua ProxyPool + background refill + dashboard.

Mỗi worker:
    get proxy -> SiteClient(Session riêng).run_once()
    -> success: release() | failure: mark_dead() + retry proxy khác (<= max_retries)
    -> nghỉ random delay_min..delay_max

Background refill thread: khi pool < min_pool_size thì crawl -> dedup ->
check -> add healthy vào pool. Runner không chết vì hết proxy: worker chờ
pool có hàng (get timeout) thay vì crash.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from src.proxy.checker import ProxyChecker
from src.proxy.crawler import ProxyCrawler
from src.proxy.pipeline import CrawlCheckPipeline
from src.proxy.pool import ProxyPool
from src.runner.site_client import InvalidResponseError, SiteClient
from src.utils.stats import Stats, render_dashboard

log = logging.getLogger(__name__)

# Lỗi do proxy/mạng -> đổi proxy và retry. Lỗi nghiệp vụ -> không retry proxy.
RETRYABLE = (requests.Timeout, requests.ConnectionError)


class Runner:
    def __init__(self, cfg: dict, pool: ProxyPool, stats: Stats):
        self.cfg = cfg
        self.pool = pool
        self.stats = stats
        self.stop_event = threading.Event()
        self.feed_done = threading.Event()  # main bật khi không còn proxy mới
        self._refilling = threading.Lock()

    # -- refill --
    def refill_once(self) -> int:
        """Crawl + check streaming -> add healthy. Trả về số proxy thêm được."""
        if not self._refilling.acquire(blocking=False):
            return 0  # refill khác đang chạy
        try:
            crawler = ProxyCrawler(workers=self.cfg.get("crawl_workers", 10))
            checker = ProxyChecker(
                self.cfg["check_http_url"], self.cfg["check_https_url"],
                timeout=self.cfg.get("proxy_check_timeout", 8),
                workers=self.cfg.get("checker_workers", 30))
            _raw, healthy, _dead = CrawlCheckPipeline(crawler, checker).run(
                on_result=lambda r: self.stats.add_check(r.alive))
            added = self.pool.add_many(healthy)
            log.info("refill: +%d healthy vào pool (pool=%d)",
                     added, self.pool.size())
            return added
        finally:
            self._refilling.release()

    def _refill_loop(self) -> None:
        interval = self.cfg.get("refill_interval", 60)
        min_size = self.cfg.get("min_pool_size", 20)
        while not self.stop_event.wait(interval):
            if self.pool.size() < min_size:
                log.info("pool thấp (%d < %d), refill...",
                         self.pool.size(), min_size)
                try:
                    self.refill_once()
                except Exception as exc:  # noqa: BLE001 - refill lỗi không kill run
                    log.error("refill lỗi: %s", exc)

    # -- worker --
    def _attempt(self, client: SiteClient, max_retries: int) -> float:
        """Thử request, proxy lỗi thì đổi proxy khác. Trả về latency lần success."""
        last_err: Exception | None = None
        for attempt in range(max_retries + 1):
            item = self.pool.get(timeout=10)
            if item is None:
                raise RuntimeError("pool cạn proxy (refill đang bổ sung, thử lại sau)")
            addr, proto = item
            try:
                latency = client.run_once(addr, proto)
            except RETRYABLE as exc:
                last_err = exc
                log.warning("proxy %s lỗi mạng (%s), đổi proxy (%d/%d)",
                            addr, exc, attempt + 1, max_retries + 1)
                self.pool.mark_dead(addr)
                continue
            except (requests.HTTPError, InvalidResponseError,
                    ValueError, KeyError) as exc:
                # Lỗi nghiệp vụ/HTTP từ site: trả proxy về, không đổ lỗi proxy.
                last_err = exc
                log.warning("request lỗi nghiệp vụ: %s", exc)
                self.pool.release(addr)
                break
            else:
                self.pool.release(addr)
                return latency
        raise last_err if last_err else RuntimeError("request thất bại")

    def _worker_loop(self, worker_id: int) -> None:
        client = SiteClient(self.cfg["site"],
                            timeout=self.cfg.get("request_timeout", 12))
        max_retries = self.cfg.get("max_retries", 2)
        max_requests = self.cfg.get("max_requests", 0)
        dmin, dmax = self.cfg.get("delay_min", 1), self.cfg.get("delay_max", 2)
        ok_count = 0
        round_count = 0
        rest_every = self.cfg.get("long_rest_every", 0)
        rest_min = self.cfg.get("long_rest_min", 300)
        rest_max = self.cfg.get("long_rest_max", 600)
        log.info("worker-%d bắt đầu (site=%s)", worker_id,
                 self.cfg["site"].get("action_url"))
        while not self.stop_event.is_set():
            if max_requests and not self.stats.claim_turn(max_requests):
                break
            try:
                latency = self._attempt(client, max_retries)
            except Exception as exc:  # noqa: BLE001 - 1 lượt lỗi không kill worker
                if (self.cfg.get("stop_when_exhausted")
                        and self.feed_done.is_set() and self.pool.size() == 0):
                    log.info("worker-%d dừng (hết proxy)", worker_id)
                    break
                log.warning("worker-%d lượt THẤT BẠI: %s", worker_id, exc)
                self.stats.add_request(False)
            else:
                ok_count += 1
                self.stats.add_request(True, latency)
                final = getattr(client, "last_final", "")
                if ok_count % 10 == 1 or ok_count <= 3:
                    log.info("worker-%d lượt THÀNH CÔNG #%d (%.2fs) -> %s",
                             worker_id, ok_count, latency, final)
            round_count += 1
            if rest_every and round_count % rest_every == 0:
                nap = random.randint(min(rest_min, rest_max), max(rest_min, rest_max))
                log.info("worker-%d đủ %d vòng -> nghỉ %ds", worker_id, rest_every, nap)
                if self.stop_event.wait(nap):
                    break
            time.sleep(random.uniform(min(dmin, dmax), max(dmin, dmax)))
        log.info("worker-%d dừng (thành công %d lượt)", worker_id, ok_count)

    # -- run --
    def run(self) -> None:
        workers = self.cfg.get("workers", 10)
        dashboard_every = self.cfg.get("dashboard_interval", 5)
        max_requests = self.cfg.get("max_requests", 0)

        refill_t = None
        if self.cfg.get("stop_when_exhausted"):
            log.info("refill tắt (stop_when_exhausted: hết proxy thì dừng)")
        else:
            refill_t = threading.Thread(target=self._refill_loop, daemon=True,
                                        name="refill")
            refill_t.start()
        log.info("start %d workers (max_requests=%s)", workers, max_requests or "∞")
        print(render_dashboard(workers, self.pool.size(),
                               self.stats.snapshot()), flush=True)

        try:
            with ThreadPoolExecutor(max_workers=workers,
                                    thread_name_prefix="worker") as ex:
                futures = [ex.submit(self._worker_loop, i) for i in range(workers)]
                while True:
                    time.sleep(dashboard_every)
                    snap = self.stats.snapshot()
                    print(render_dashboard(workers, self.pool.size(), snap),
                          flush=True)
                    if max_requests and snap["requests"] >= max_requests:
                        break
                    if all(f.done() for f in futures):
                        break
        except KeyboardInterrupt:
            log.warning("nhận Ctrl+C, dừng...")
        finally:
            self.stop_event.set()
            snap = self.stats.snapshot()
            print(render_dashboard(workers, self.pool.size(), snap), flush=True)
            log.info("runner dừng. success=%d failed=%d",
                     snap["success"], snap["failed"])
