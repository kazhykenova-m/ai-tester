"""Developer-facing findings, without navigation logs."""

import hashlib


def build_findings(summary):
    rows = []
    for c in summary.get("checks", []):
        if c["status"] == "fail":
            rows.append(
                {
                    "category": "functional",
                    "severity": "medium",
                    "title": c["text"],
                    "actual": c.get("details", ""),
                    "expected": c["text"],
                    "url": c.get("url", summary["url"]),
                    "screenshot": c.get("screenshot", ""),
                }
            )
    for item in summary.get("issues", []):
        if isinstance(item, dict):
            rows.append(dict(item))
        else:
            category = "javascript" if "JS" in item or "консоли" in item else "network"
            rows.append(
                {
                    "category": category,
                    "severity": "medium",
                    "title": item,
                    "actual": item,
                    "url": summary["url"],
                }
            )
    seen, result = set(), []
    for row in rows:
        key = (
            row.get("category"),
            row.get("title"),
            row.get("url"),
            row.get("evidence", row.get("actual", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        row["id"] = hashlib.sha256(repr(key).encode()).hexdigest()[:8]
        row["device"] = summary.get("device", "desktop")
        result.append(row)
    return result


def developer_markdown(summary):
    findings = summary.get("findings", build_findings(summary))
    lines = [
        f"# {summary['title']}",
        f"Сайт: {summary['url']}",
        f"Устройство: {summary.get('device', 'desktop')}",
        f"Статус: {summary['reason']}",
        f"Найдено проблем: {len(findings)}",
        "",
    ]
    for i, row in enumerate(findings, 1):
        lines += [f"## {i}. [{row.get('severity', 'medium')}] {row['title']}"]
        for key, label in [
            ("category", "Категория"),
            ("url", "Страница"),
            ("actual", "Фактически"),
            ("expected", "Ожидается"),
            ("evidence", "Доказательство"),
            ("reproduction", "Как повторить"),
            ("screenshot", "Скриншот"),
        ]:
            if row.get(key):
                lines.append(f"{label}: {row[key]}")
        lines.append("")
    for key, label in [
        ("visual", "Визуальные сравнения — требуют подтверждения"),
        ("third_party", "Сторонние ресурсы"),
        ("tester_errors", "Ошибки тестировщика"),
    ]:
        if summary.get(key):
            import json

            lines += [
                "## " + label,
                json.dumps(summary[key], ensure_ascii=False, indent=2),
                "",
            ]
    if summary.get("coverage"):
        lines += ["## Покрытие", str(summary["coverage"]), ""]
    if summary.get("limitations"):
        lines += ["## Ограничения проверки", *summary["limitations"], ""]
    if summary.get("coverage", {}).get("missing"):
        lines.append("Не проверены пункты: " + str(summary["coverage"]["missing"]))
    lines.append(
        "Проверены только посещённые состояния. Пиксельные расхождения требуют подтверждения."
    )
    return "\n".join(lines)
