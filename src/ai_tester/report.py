import base64
import html
import json
from pathlib import Path

CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f4f5f7;color:#1f2933}
main{max-width:980px;margin:0 auto;padding:24px}
h1{margin:0 0 16px}h2{margin-top:32px}
.verdict{padding:16px 20px;border-radius:10px;font-size:18px;margin-bottom:16px}
.verdict.ok{background:#e3f6e8;border:1px solid #7bc891}
.verdict.bad{background:#fde8e8;border:1px solid #e08a8a}
table{border-collapse:collapse;width:100%;background:#fff;border-radius:8px;overflow:hidden}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #e5e7eb;vertical-align:top}
th{background:#eef0f3}
table.meta td:first-child{width:160px;color:#6b7280}
td.ok{color:#15803d;font-weight:600}td.bad{color:#b91c1c;font-weight:600}
td.warn{color:#b45309;font-weight:600}
.step{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:14px;margin:14px 0}
.step h3{margin:0 0 6px;font-size:16px}
.step code{background:#f1f3f5;padding:2px 6px;border-radius:4px;word-break:break-all}
.step .why{color:#4b5563;margin:6px 0}
.step .outcome.err{color:#b45309}
.step img{max-width:100%;width:640px;border:1px solid #d1d5db;border-radius:6px;margin-top:8px}
ul.issues li{margin:4px 0;word-break:break-all}
"""

STATUS = {
    "pass": ("ok", "✔ Пройдена"),
    "fail": ("bad", "✘ Не пройдена"),
    "missing": ("warn", "? Не проверена"),
}


def render_html(summary, images=None):
    images = images or {}
    esc = html.escape
    ok = summary["success"]
    css_class = "ok" if ok else "bad"
    verdict = "ТЕСТ ПРОЙДЕН" if ok else "ТЕСТ НЕ ПРОЙДЕН"
    checks = summary["checks"]
    passed = sum(1 for c in checks if c["status"] == "pass")
    parts = [
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>",
        f"<title>Отчёт: {esc(summary['title'])}</title>",
        f"<style>{CSS}</style></head><body><main>",
        f"<h1>{esc(summary['title'])}</h1>",
        f"<div class='verdict {css_class}'><b>{verdict}</b><br>{esc(summary['reason'])}</div>",
        "<table class='meta'>",
        f"<tr><td>Сайт</td><td>{esc(summary['url'])}</td></tr>",
        f"<tr><td>Модель</td><td>{esc(summary['model'])}</td></tr>",
        f"<tr><td>Запуск</td><td>{esc(summary['started'])}, {summary['duration']} с</td></tr>",
        f"<tr><td>Проверки</td><td>{passed} из {len(checks)} пройдено</td></tr>",
        f"<tr><td>Шагов</td><td>{len(summary['steps'])}</td></tr>",
        "</table>",
    ]
    if checks:
        parts.append(
            "<h2>Проверки</h2><table><tr><th>#</th><th>Что проверяли</th>"
            "<th>Результат</th><th>Что увидел тестировщик</th></tr>"
        )
        for i, check in enumerate(checks, 1):
            cls, label = STATUS[check["status"]]
            parts.append(
                f"<tr><td>{i}</td><td>{esc(check['text'])}</td>"
                f"<td class='{cls}'>{label}</td><td>{esc(check['details'])}</td></tr>"
            )
        parts.append("</table>")
    if summary["issues"]:
        parts.append("<h2>Замечания по странице</h2><ul class='issues'>")
        parts += [f"<li>{esc(item)}</li>" for item in summary["issues"]]
        parts.append("</ul>")
    parts.append("<h2>Ход теста</h2>")
    for step in summary["steps"]:
        shot = step.get("screenshot", "")
        src = images.get(shot, shot) if shot else ""
        img = f"<img src='{src}' alt='шаг {step['n']}'>" if src else ""
        failed = step["outcome"].startswith(("ошибка", "отклонено"))
        outcome_class = "outcome err" if failed else "outcome"
        parts.append(
            f"<div class='step'><h3>Шаг {step['n']}: <code>{esc(step['what'])}</code></h3>"
            f"<div class='why'>{esc(step['why'])}</div>"
            f"<div class='{outcome_class}'>Результат: {esc(step['outcome'])}</div>"
            f"<div class='why'>Страница: {esc(step['url'])}</div>{img}</div>"
        )
    parts.append("</main></body></html>")
    return "".join(parts)


def write_reports(out_dir, summary):
    out = Path(out_dir)
    images = {}
    for step in summary["steps"]:
        shot = step.get("screenshot")
        if shot and (out / shot).exists():
            data = base64.b64encode((out / shot).read_bytes()).decode("ascii")
            images[shot] = f"data:image/png;base64,{data}"
    (out / "report.html").write_text(render_html(summary, images), encoding="utf-8")
    (out / "report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
