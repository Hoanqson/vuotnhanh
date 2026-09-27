# proxy-pool-runner

Crawl proxy public → check sống/chết → giữ trong Pool thread-safe → dàn
worker (`ThreadPoolExecutor`) request website của bạn, mỗi worker 1
`requests.Session` riêng. Pool cạn thì background thread tự refill.
Chỉ dùng cho website bạn sở hữu; không bypass CAPTCHA/auth/rate-limit.

> Lưu ý: file `vuotnhanh.py` bạn gửi chưa có nội dung code nên logic nghiệp vụ
> được tách thành module `src/runner/site_client.py` với đầy đủ flow
> request → CSRF → validate response. Bạn điền endpoint/payload vào
> `config.yaml` (mục `site`), không cần sửa source.

## Cài đặt & chạy


```
cd vuotnhanh
```
```
pip install -r requirements.txt
```

```
python main.py --crawl
```             # crawl -> data/proxylive.txt
```
python main.py --check   
```          # check proxylive -> healthy/dead
```
python main.py --run
```                # pool + workers (tự crawl+check nếu healthy trống)
```
python main.py --crawl --check --run
```
```
python main.py --crawl --check
```       # chỉ crawl+check, chưa run
```
python main.py --crawl --check --run --url https://vuotnhanh.com/abcd
```


## Cấu hình (`config.yaml`)

| Key | Ý nghĩa |
|-----|---------|
| `workers` | số worker request (`ThreadPoolExecutor`) |
| `checker_workers` / `crawl_workers` | concurrency checker / crawler |
| `request_timeout` / `proxy_check_timeout` | timeout request site / check proxy |
| `max_retries` | đổi proxy và thử lại tối đa |
| `max_proxy_failures` | proxy lỗi bấy nhiêu lần thì loại khỏi pool |
| `delay_min` / `delay_max` | nghỉ random giữa các lượt (lịch sự, không né rate-limit) |
| `min_pool_size` / `refill_interval` | pool dưới ngưỡng thì refill sau mỗi N giây |
| `max_requests` | tổng request rồi dừng; `0` = chạy đến Ctrl+C |
| `site.*` | **website của bạn**: target/csrf/action URL, payload, header, CSRF, success marker |

## Flow crawl → check → pool → worker

```text
Crawl + Check streaming (song song)   # source nào xong là check ngay batch đó
  -> data/proxylive.txt + healthy/dead_proxies.txt
ProxyPool                               # get/release/mark_dead,Lock+Condition,
                                        # không giao trùng proxy đang dùng
Runner (ThreadPoolExecutor)             # worker: get -> SiteClient.run_once()
                                        #  (GET CSRF -> request -> validate)
                                        #  ok: release | lỗi mạng: mark_dead+retry
Refill thread                           # pool < min_pool_size -> crawl/check/add
Dashboard                               # in bảng monitoring mỗi dashboard_interval
```

## Nguồn proxy & license

| Nguồn | Loại | License |
|-------|------|---------|
| https://github.com/TheSpeedX/PROXY-List | raw txt | MIT |
| https://github.com/monosans/proxy-list | raw txt | MIT |
| https://github.com/proxifly/free-proxy-list | jsDelivr mirror | xem LICENSE repo |
| https://github.com/ProxyScrape/free-proxy-list + `api.proxyscrape.com/v4/free-proxy-list/get` | mirror + public API | MIT (code), data as-is |
| https://api.openproxylist.xyz/*.txt | plain text | xem điều khoản site |
| https://proxylist.geonode.com/api/proxy-list | JSON API | xem điều khoản site |

Chỉ ý tưởng được tham khảo, không copy code. Tôn trọng `429` (bỏ source, không
né). Dữ liệu proxy các nguồn đều "as-is", không bảo đảm.

## Error handling & log

Bắt riêng `Timeout`, `ConnectionError`, `HTTPError`, proxy invalid,
response invalid, JSON parse lỗi — 1 proxy/1 lượt lỗi không crash run.
Log ra console + `logs/app.log` (xoay file 2MB × 3).

## Giới hạn

- Proxy free chết nhanh, tỉ lệ sống thường <10%; luôn `--check` trước `--run`.
- Không truyền credential/dữ liệu nhạy cảm qua proxy free.
- Đừng tăng `workers`/`checker_workers` quá cao gây quá tải site của chính bạn.
