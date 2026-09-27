"""CLI pipeline: Crawl -> Check -> Pool -> Workers (+ refill khi pool thấp).

    python main.py                    # full pipeline: crawl + check + run
    python main.py --crawl            # chỉ crawl -> data/proxylive.txt
    python main.py --check            # check proxylive.txt -> healthy/dead
    python main.py --run              # build pool từ healthy -> chạy workers
    python main.py --crawl --check --run
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

# Console Windows mặc định cp1252 không in được tiếng Việt -> ép UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from src.proxy.checker import ProxyChecker  # noqa: E402
from src.proxy.crawler import ProxyCrawler  # noqa: E402
from src.proxy.pipeline import CrawlCheckPipeline  # noqa: E402
from src.proxy.pool import ProxyPool  # noqa: E402
from src.runner.runner import Runner  # noqa: E402
from src.utils.autotune import apply_auto_tune  # noqa: E402
from src.utils.logging_config import setup_logging  # noqa: E402
from src.utils.stats import Stats  # noqa: E402


def load_config(path: str = "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def do_crawl(cfg: dict) -> list:
    crawler = ProxyCrawler(workers=cfg.get("crawl_workers", 10))
    items = crawler.crawl()
    crawler.save(items, cfg["paths"]["raw"])
    print(f"crawl: {len(items)} unique -> {cfg['paths']['raw']}")
    return items


def do_crawl_check(cfg: dict, stats: Stats):
    """Pipeline streaming: vừa crawl vừa check (dùng khi --crawl --check)."""
    crawler = ProxyCrawler(workers=cfg.get("crawl_workers", 10))
    checker = ProxyChecker(cfg["check_http_url"], cfg["check_https_url"],
                           timeout=cfg.get("proxy_check_timeout", 8),
                           workers=cfg.get("checker_workers", 30))
    pipe = CrawlCheckPipeline(crawler, checker)
    raw, healthy, dead = pipe.run(on_result=lambda r: stats.add_check(r.alive))
    ProxyCrawler.save(raw, cfg["paths"]["raw"])
    ProxyChecker.save_lines([a for a, _ in healthy], cfg["paths"]["healthy"])
    ProxyChecker.save_lines(dead, cfg["paths"]["dead"])
    print(f"crawl+check: raw={len(raw)} alive={len(healthy)} dead={len(dead)}")
    return healthy


def do_streaming_crawl_check_run(cfg: dict, stats: Stats) -> int:
    """Crawl -> check -> worker chạy SONG SONG.

    Checker vừa xác nhận 1 proxy alive là đẩy ngay vào Pool cho worker dùng,
    không đợi check hết. Worker khởi động khi pool có ~10 proxy live đầu tiên.
    """
    log = logging.getLogger("app")
    crawler = ProxyCrawler(workers=cfg.get("crawl_workers", 10))
    checker = ProxyChecker(cfg["check_http_url"], cfg["check_https_url"],
                           timeout=cfg.get("proxy_check_timeout", 8),
                           workers=cfg.get("checker_workers", 30))
    pipe = CrawlCheckPipeline(crawler, checker)
    pool = ProxyPool(max_failures=cfg.get("max_proxy_failures", 3))
    runner = Runner(cfg, pool, stats)
    pipeline_done = threading.Event()

    def _on_result(res) -> None:
        stats.add_check(res.alive)
        if res.alive:
            pool.add(res.proxy, res.protocol)

    def _pipeline_job() -> None:
        try:
            raw, healthy, dead = pipe.run(on_result=_on_result)
            ProxyCrawler.save(raw, cfg["paths"]["raw"])
            ProxyChecker.save_lines([a for a, _ in healthy], cfg["paths"]["healthy"])
            ProxyChecker.save_lines(dead, cfg["paths"]["dead"])
        except Exception as exc:  # noqa: BLE001
            log.error("pipeline lỗi: %s", exc)
        finally:
            pipeline_done.set()
            runner.feed_done.set()  # báo workers: không còn proxy mới nữa

    t = threading.Thread(target=_pipeline_job, daemon=True, name="pipeline")
    t.start()

    need = min(10, cfg.get("min_pool_size", 20))
    log.info("đợi %d proxy live đầu tiên để khởi động workers...", need)
    while not pipeline_done.is_set() and pool.size() < need:
        time.sleep(1)
    if pool.size() == 0:
        print("không có proxy live nào, dừng.")
        return 1
    log.info("===== workers khởi động với %d proxy (check vẫn chạy nền) =====",
             pool.size())
    log.info("===== PHASE 3/3: RUN workers -> site %s =====",
             cfg["site"].get("action_url"))
    runner.run()
    t.join(timeout=120)  # pipeline đã xong (feed_done) nên join nhanh
    # Lưu snapshot pool để lần --run sau dùng ngay.
    ProxyChecker.save_lines([a for a, _ in pool.snapshot()], cfg["paths"]["healthy"])
    log.info("hết proxy, runner đã dừng.")
    return 0


def do_check(cfg: dict, stats: Stats) -> list:
    items = ProxyCrawler.load(cfg["paths"]["raw"])
    print(f"check: {len(items)} proxy từ {cfg['paths']['raw']}")
    checker = ProxyChecker(cfg["check_http_url"], cfg["check_https_url"],
                           timeout=cfg.get("proxy_check_timeout", 8),
                           workers=cfg.get("checker_workers", 30))
    healthy, dead = checker.check_all(
        items, on_result=lambda r: stats.add_check(r.alive))
    ProxyChecker.save_lines([a for a, _ in healthy], cfg["paths"]["healthy"])
    ProxyChecker.save_lines(dead, cfg["paths"]["dead"])
    print(f"check: alive={len(healthy)} dead={len(dead)}")
    return healthy


def build_pool(cfg: dict) -> ProxyPool:
    pool = ProxyPool(max_failures=cfg.get("max_proxy_failures", 3))
    items = ProxyCrawler.load(cfg["paths"]["healthy"], protocol="http")
    # healthy file lưu IP:PORT; protocol mặc định http (đủ cho requests).
    n = pool.add_many(items)
    print(f"pool: nạp {n} proxy từ {cfg['paths']['healthy']}")
    return pool


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Proxy pool runner cho website của bạn")
    ap.add_argument("--crawl", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--url", default=None,
                    help="Link vuotnhanh cần cày (ghi đè site.target_url trong config)")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--auto-tune", dest="auto_tune", action="store_true",
                    default=None, help="Ép bật tự tune workers theo CPU+RAM")
    ap.add_argument("--no-auto-tune", dest="auto_tune", action="store_false",
                    help="Tắt tự tune, dùng số tay trong config")
    ap.add_argument("--ram-target", type=float, default=None,
                    help="Mục tiêu %% RAM dùng (10-95), ghi đè target_ram_percent")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.auto_tune is True:
        cfg["auto_tune"] = True
    elif args.auto_tune is False:
        cfg["auto_tune"] = False
    if args.ram_target is not None:
        cfg["target_ram_percent"] = args.ram_target
    if args.url:
        cfg["site"]["target_url"] = args.url
        cfg["site"]["csrf_url"] = args.url
        log_msg = f"dùng link: {args.url}"
    else:
        log_msg = f"dùng link mặc định: {cfg['site']['target_url']}"
    log = setup_logging(cfg["paths"]["log"])
    log.info(log_msg)
    if cfg.get("auto_tune", True):
        info = apply_auto_tune(cfg)
        print(f"autotune: CPU={info['cpu']} RAM={info['total_ram_mb']}MB "
              f"-> workers={info['workers']} checker={info['checker_workers']} "
              f"crawl={info['crawl_workers']}")
    stats = Stats()

    # Không flag nào = full pipeline.
    full = not (args.crawl or args.check or args.run)
    want_crawl = args.crawl or full
    want_check = args.check or full
    want_run = args.run or full

    healthy: list = []
    try:
        if want_crawl and want_check and want_run:
            # Chế độ streaming full: vừa check vừa chạy, không đợi check hết.
            log.info("===== CRAWL + CHECK + RUN song song =====")
            return do_streaming_crawl_check_run(cfg, stats)
        if want_crawl and want_check:
            log.info("===== PHASE 1+2/3: CRAWL + CHECK proxy =====")
            healthy = do_crawl_check(cfg, stats)  # streaming, tối ưu
        elif want_crawl:
            log.info("===== PHASE 1/3: CRAWL proxy =====")
            do_crawl(cfg)
        elif want_check:
            log.info("===== PHASE 2/3: CHECK proxy =====")
            healthy = do_check(cfg, stats)
        if want_run:
            if not healthy:
                # --run đơn lẻ: dùng healthy file có sẵn; trống thì crawl+check.
                pool_probe = ProxyCrawler.load(cfg["paths"]["healthy"])
                if not pool_probe and not want_crawl and not want_check:
                    log.info("healthy trống, tự crawl+check trước khi run")
                    healthy = do_crawl_check(cfg, stats)
            pool = build_pool(cfg)
            if pool.size() == 0:
                print("pool rỗng, không thể run. Hãy --crawl --check trước.")
                return 1
            log.info("===== PHASE 3/3: RUN workers -> site %s =====",
                     cfg["site"].get("action_url"))
            # Seed stats alive/dead từ healthy/dead file để dashboard đầy đủ.
            try:
                h = sum(1 for _ in open(cfg["paths"]["healthy"], encoding="utf-8"))
                d = sum(1 for _ in open(cfg["paths"]["dead"], encoding="utf-8"))
                for _ in range(h):
                    stats.add_check(True)
                for _ in range(d):
                    stats.add_check(False)
            except FileNotFoundError:
                pass
            Runner(cfg, pool, stats).run()
    except KeyboardInterrupt:
        print("dừng bởi người dùng.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
