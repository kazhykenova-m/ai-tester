"""Syntax-only source audit. Never imports or executes uploaded source code."""

import ast
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import PurePosixPath, Path
from zipfile import BadZipFile, ZipFile

MAX_FILES = 1500
MAX_TOTAL = 25 * 1024 * 1024
MAX_FILE = 1024 * 1024
SKIP = {"node_modules", "vendor", ".git", ".venv", "__pycache__", "bitrix"}


def audit_zip(path):
    if Path(path).stat().st_size > 10 * 1024 * 1024:
        raise ValueError("ZIP превышает 10 МБ")
    findings, limitations, checked, total = [], [], 0, 0
    try:
        archive = ZipFile(path)
    except BadZipFile:
        raise ValueError("Некорректный ZIP-архив") from None
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_FILES:
            raise ValueError(
                "В архиве больше 1500 файлов. Пришли только исходники проекта."
            )
        if sum(e.file_size for e in entries) > MAX_TOTAL:
            raise ValueError("Распакованный объём превышает 25 МБ")
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            if (
                name.startswith("/")
                or ".." in PurePosixPath(name).parts
                or ":" in name
                or (entry.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError("Опасный путь или ссылка в ZIP")
            parts = PurePosixPath(entry.filename).parts
            if entry.is_dir() or any(p in SKIP for p in parts):
                continue
            if entry.file_size > MAX_FILE:
                limitations.append("Файлы больше 1 МБ пропущены")
                continue
            total += entry.file_size
            if total > MAX_TOTAL:
                raise ValueError("Распакованный объём превышает 25 МБ")
            suffix = PurePosixPath(entry.filename).suffix.lower()
            if suffix not in (".py", ".js", ".mjs", ".cjs", ".php", ".json"):
                if suffix in (".ts", ".tsx", ".jsx", ".css", ".scss", ".html"):
                    limitations.append(
                        "TS/JSX/CSS/HTML требуют линтеров проекта; не проверены"
                    )
                continue
            try:
                source = archive.read(entry).decode("utf-8-sig")
            except (UnicodeError, RuntimeError, BadZipFile):
                limitations.append(
                    "Некоторые файлы недоступны или имеют другую кодировку"
                )
                continue
            error = ""
            if suffix == ".py":
                try:
                    ast.parse(source)
                except (SyntaxError, ValueError) as exc:
                    error = f"Синтаксическая ошибка Python, строка {getattr(exc, 'lineno', '?')}"
            elif suffix == ".json":
                import json

                try:
                    json.loads(source)
                except json.JSONDecodeError as exc:
                    error = f"Некорректный JSON, строка {exc.lineno}"
            else:
                executable = shutil.which("php" if suffix == ".php" else "node")
                if not executable:
                    limitations.append("PHP/Node недоступен: часть файлов не проверена")
                    continue
                command = (
                    [executable, "-n", "-d", "short_open_tag=1", "-l"]
                    if suffix == ".php"
                    else [executable, "--check"]
                )
                if suffix in (".js", ".mjs"):
                    command += ["--input-type=module"]
                try:
                    proc = subprocess.run(
                        command,
                        input=source,
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    limitations.append("Превышено время проверки отдельного файла")
                    continue
                if proc.returncode:
                    # Parser output can contain source snippets/secrets; omit them.
                    error = (
                        "Синтаксическая ошибка PHP"
                        if suffix == ".php"
                        else "Синтаксическая ошибка JavaScript"
                    )
            checked += 1
            if error:
                findings.append(
                    {
                        "category": "source",
                        "severity": "high",
                        "title": error,
                        "actual": error,
                        "url": entry.filename,
                        "expected": "Файл проходит синтаксическую проверку",
                        "evidence": "Ошибка встроенного синтаксического анализатора",
                        "reproduction": "Запустить синтаксическую проверку указанного файла",
                    }
                )
    limitations.append(
        "Это проверка синтаксиса, не полная оценка чистоты кода, безопасности и бизнес-логики"
    )
    return {
        "title": "Проверка исходников",
        "url": "Загруженный ZIP",
        "device": "source",
        "started": datetime.now(timezone.utc).isoformat(),
        "duration": 0,
        "success": not findings,
        "reason": f"Синтаксис: проверено файлов {checked}; ошибок {len(findings)}",
        "checks": [],
        "steps": [],
        "issues": [],
        "findings": findings,
        "coverage": {"total": checked, "missing": []},
        "limitations": list(dict.fromkeys(limitations)),
    }
