FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py config.yaml ./
COPY src ./src

# data/logs ghi ra volume để không mất khi container xóa
VOLUME ["/app/data", "/app/logs"]

CMD ["python", "main.py", "--crawl", "--check", "--run"]
