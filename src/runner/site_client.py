"""SiteClient: logic nghiệp vụ request/CSRF/response cho website của bạn.

Mỗi worker sở hữu 1 SiteClient (= 1 requests.Session riêng, KHÔNG share
giữa các thread). Endpoint, payload, header đọc từ config.yaml (mục `site`).

Flow mặc định (giữ đúng shape code requests/CSRF hiện tại của bạn):
    GET csrf_url  -> lấy CSRF token (cookie hoặc thẻ input trong HTML)
    POST/GET action_url + payload + header CSRF
    validate response (status + success_marker)

Bạn chỉ cần sửa config.yaml; nếu site có flow khác, override 3 method
get_csrf_token() / build_request() / validate_response() tại đây.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

import requests

from src.proxy.checker import build_requests_proxies

log = logging.getLogger(__name__)


class InvalidResponseError(ValueError):
    """Response không đúng kỳ vọng nghiệp vụ (sai format, thiếu marker...)."""


class SiteClient:
    def __init__(self, site_cfg: dict, timeout: float = 12):
        self.cfg = site_cfg
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": site_cfg.get("user_agent", "proxy-pool-runner/1.0"),
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._csrf: Optional[str] = None
        self._page_url: str = ""
        self.last_final: str = ""

    # -- proxy/session --
    def use_proxy(self, address: str, protocol: str) -> None:
        self.session.proxies.update(build_requests_proxies(address, protocol))

    # -- CSRF --
    def get_csrf_token(self) -> str:
        """GET csrf_url, lấy token từ cookie, <meta name=csrf-token> hoặc <input>."""
        url = self.cfg.get("csrf_url") or self.cfg.get("target_url")
        self._page_url = url
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        cookie_name = self.cfg.get("csrf_cookie", "")
        if cookie_name and cookie_name in self.session.cookies:
            self._csrf = self.session.cookies[cookie_name]
            return self._csrf
        m = re.search(r'name="csrf-token"\s+content="([^"]+)"', r.text)
        if m:
            self._csrf = m.group(1)
            return self._csrf
        m = re.search(
            r'name=["\']csrf[^"\']*["\']\s+value=["\']([^"\']+)["\']', r.text,
            re.IGNORECASE)
        if m:
            self._csrf = m.group(1)
            return self._csrf
        # Site không dùng CSRF: trả rỗng, request nghiệp vụ vẫn chạy.
        log.debug("không tìm thấy CSRF token, tiếp tục không token")
        self._csrf = ""
        return ""

    # -- request nghiệp vụ --
    def do_business_request(self) -> requests.Response:
        method = (self.cfg.get("method") or "POST").upper()
        headers = dict(self.cfg.get("headers") or {})
        if self._csrf:
            headers[self.cfg.get("csrf_header", "X-CSRFToken")] = self._csrf
        if self.cfg.get("send_referer") and getattr(self, "_page_url", ""):
            headers.setdefault("Referer", self._page_url)
        if self.cfg.get("origin"):
            headers.setdefault("Origin", self.cfg["origin"])
        url = self.cfg["action_url"]
        payload = dict(self.cfg.get("payload") or {})
        if self.cfg.get("alias_from_url"):
            target = self.cfg.get("target_url", "")
            payload["alias"] = target.rstrip("/").split("/")[-1]
        if method == "GET":
            return self.session.get(url, params=payload, headers=headers,
                                    timeout=self.timeout)
        return self.session.post(url, data=payload, headers=headers,
                                 timeout=self.timeout)

    # -- validate --
    def validate_response(self, response: requests.Response) -> bool:
        """Raise InvalidResponseError nếu response sai; True nếu success."""
        if response.status_code >= 400:
            raise InvalidResponseError(f"HTTP {response.status_code}")
        field = self.cfg.get("success_json_field", "")
        if field:  # validate kiểu vuotnhanh: JSON {"status": "success", ...}
            try:
                data = response.json()
            except Exception as exc:
                raise InvalidResponseError(f"JSON parse lỗi: {exc}") from exc
            if str(data.get(field)) != str(self.cfg.get("success_json_value", "")):
                raise InvalidResponseError(f"JSON field {field} không success")
            if self.cfg.get("follow_redirect"):
                redirect = data.get(self.cfg.get("redirect_field", "url_redirect"))
                if not redirect:
                    raise InvalidResponseError("thiếu url_redirect")
                r2 = self.session.get(redirect, allow_redirects=False,
                                      timeout=self.timeout)
                self.last_final = r2.headers.get("Location") or redirect
            return True
        marker = (self.cfg.get("success_marker") or "").strip()
        if marker:
            ctype = response.headers.get("Content-Type", "")
            body = response.text
            if "json" in ctype:
                try:
                    import json as _json
                    data = _json.loads(body)
                except Exception as exc:
                    raise InvalidResponseError(
                        f"JSON parse lỗi: {exc}") from exc
                if marker.strip('"') not in _json.dumps(data):
                    raise InvalidResponseError("thiếu success marker trong JSON")
            elif marker not in body:
                raise InvalidResponseError("thiếu success marker trong body")
        return True

    # -- entry cho worker --
    def run_once(self, proxy_addr: Optional[str], proxy_proto: str = "http") -> float:
        """Chạy 1 lượt nghiệp vụ qua proxy (None = nối thẳng, dùng cho test).
        Trả về latency (giây). URL đích lưu ở self.last_final."""
        if proxy_addr:
            self.use_proxy(proxy_addr, proxy_proto)
        start = time.perf_counter()
        self.get_csrf_token()
        resp = self.do_business_request()
        self.validate_response(resp)
        return time.perf_counter() - start
