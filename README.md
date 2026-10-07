# ai-tester

ИИ-тестировщик пути пользователя на сайте: сценарий пишется обычным текстом,
браузер (Playwright) выполняет шаги, решения принимает Claude.

## Запуск

    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    playwright install chromium
    pytest

Секреты (API-ключ) хранятся в `.env` и в репозиторий не попадают.
