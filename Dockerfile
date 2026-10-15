FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY aggregator ./aggregator
COPY static ./static
RUN useradd -r -u 10001 app
USER app
EXPOSE 8000
CMD ["uvicorn", "aggregator.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
