from ai_tester.report import render_html
from ai_tester.scenario import parse_checks, scenario_title

TEXT = """# Покупка
## Шаги
1. Сделай раз
## Проверки
- Первая проверка
- Вторая проверка
"""


def test_parse_checks():
    assert parse_checks(TEXT) == ["Первая проверка", "Вторая проверка"]


def test_no_checks_section():
    assert parse_checks("# Тест\nпросто текст") == []


def test_title():
    assert scenario_title(TEXT) == "Покупка"


def test_report_escapes_html():
    summary = {
        "title": "<script>alert(1)</script>",
        "url": "https://example.com",
        "model": "m",
        "started": "2026-01-01 00:00:00",
        "duration": 1.0,
        "success": False,
        "reason": "<b>bad</b>",
        "checks": [{"text": "t", "status": "fail", "details": "<i>x</i>"}],
        "steps": [],
        "issues": ["<img src=x>"],
    }
    page = render_html(summary)
    assert "<script>alert" not in page
    assert "<img src=x>" not in page
