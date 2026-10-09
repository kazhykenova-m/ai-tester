FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends nodejs php-cli && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt && playwright install --with-deps chromium
COPY src ./src
COPY scripts ./scripts
COPY scenarios ./scenarios
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1
RUN useradd --create-home tester && mkdir /app/bot-data && chown tester:tester /app/bot-data && chmod -R a+rX /root/.cache/ms-playwright && cp -a /root/.cache/ms-playwright /opt/ms-playwright && chmod -R a+rX /opt/ms-playwright
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
USER tester
CMD ["python", "-m", "ai_tester.bot"]
