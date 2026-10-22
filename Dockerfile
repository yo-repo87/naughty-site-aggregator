FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY aggregator ./aggregator
COPY static ./static
RUN useradd -r -u 10001 app && mkdir /data /plugins && chown app /data
# Performer/channel directory cache (aggregator/directory.py); mount a volume to keep it across updates.
ENV DIRECTORY_DIR=/data
# Optional plug-ins (aggregator/plugins.py): mount a folder with sources/ and targets/ here.
ENV PLUGINS_DIR=/plugins
USER app
EXPOSE 8000
CMD ["uvicorn", "aggregator.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
