# Publications Triage dashboard
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY pyproject.toml README.md ./
COPY triage ./triage
RUN pip install . && rm -rf build *.egg-info

# Example config; config/, cache/ and output/ are normally mounted as volumes.
COPY config/triage.example.yaml ./config/triage.example.yaml
RUN mkdir -p cache output && chown -R app:app /app
USER app

EXPOSE 8000
# Inside the container the app must listen on all interfaces; docker-compose publishes it on 127.0.0.1 only.
CMD ["pubtriage", "serve", "--config", "config/triage.yaml", "--host", "0.0.0.0", "--allow-remote"]
