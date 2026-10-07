from pathlib import Path


def load_scenario(path):
    """Читает сценарий теста (обычный текст) из файла."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("Scenario is empty")
    return text
