from pathlib import Path


def load_scenario(path):
    """Читает сценарий теста (обычный текст) из файла."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("Scenario is empty")
    return text


def parse_checks(text):
    """Достаёт пункты списка из раздела '## Проверки' (или '## Checks')."""
    checks = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip().lower()
            in_section = title in ("проверки", "checks")
            continue
        if in_section and stripped.startswith(("-", "*")):
            item = stripped.lstrip("-* ").strip()
            if item:
                checks.append(item)
    return checks


def scenario_title(text):
    """Название сценария: первый заголовок или первая непустая строка."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        if stripped:
            return stripped[:80]
    return "Сценарий"
