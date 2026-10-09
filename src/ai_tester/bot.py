"""Private Telegram bot with a persistent SQLite queue and isolated test worker."""

import contextlib
import json
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import requests
from dotenv import load_dotenv

from ai_tester.presets import PRESETS, make_scenario
from ai_tester.security import validate_url

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


class Bot:
    def __init__(self):
        self.token = os.environ["TELEGRAM_BOT_TOKEN"]
        self.allowed = {
            int(x.strip())
            for x in os.environ["TELEGRAM_ALLOWED_USERS"].split(",")
            if x.strip()
        }
        if not self.allowed:
            raise ValueError("TELEGRAM_ALLOWED_USERS должен содержать ID пользователей")
        self.data = Path(os.getenv("BOT_DATA_DIR", "bot-data")).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.db = self.data / "queue.sqlite"
        self.states = {}
        from ai_tester.structured import load_project

        self.projects = {}
        for path in Path(os.getenv("PROJECT_SCENARIO_DIR", "scenarios/projects")).glob(
            "*.json"
        ):
            self.projects[path.stem] = load_project(path)
        self.retention_days = int(os.getenv("REPORT_RETENTION_DAYS", "7"))
        self.last_cleanup = 0
        self.stop = threading.Event()
        with self.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, user INTEGER, chat INTEGER, status TEXT, path TEXT, created REAL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER)"
            )
            db.execute("UPDATE jobs SET status='interrupted' WHERE status='running'")

    def connection(self):
        return sqlite3.connect(self.db, timeout=15)

    def api(self, method, payload=None, files=None):
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.token}/{method}",
                data=payload or {},
                files=files,
                timeout=(10, 40),
            )
            response.raise_for_status()
            body = response.json()
            if not body.get("ok"):
                raise RuntimeError("Telegram API rejected request")
            return body.get("result")
        except requests.RequestException:
            # Do not expose a token-bearing URL in logs or user messages.
            raise RuntimeError("Telegram API unavailable") from None

    def send(self, chat, text, buttons=None):
        payload = {"chat_id": chat, "text": text[:3900]}
        if buttons:
            payload["reply_markup"] = json.dumps(
                {"inline_keyboard": buttons}, ensure_ascii=False
            )
        return self.api("sendMessage", payload)

    def document(self, chat, path):
        with path.open("rb") as handle:
            self.api(
                "sendDocument",
                {"chat_id": chat},
                files={"document": (path.name, handle)},
            )

    def handle(self, update):
        callback = update.get("callback_query")
        message = callback.get("message", {}) if callback else update.get("message", {})
        user = (callback or message).get("from", {}).get("id")
        chat = message.get("chat", {}).get("id")
        if user not in self.allowed or message.get("chat", {}).get("type") != "private":
            return
        if callback:
            self.api("answerCallbackQuery", {"callback_query_id": callback["id"]})
            value = callback.get("data", "")
            state = self.states.get(user)
            if not state:
                self.send(chat, "Начни новый тест: /test")
                return
            if value.startswith("preset:") and state["stage"] == "preset":
                preset = value.split(":", 1)[1]
                if preset not in PRESETS:
                    return
                state["preset"] = preset
                if preset == "custom":
                    state["stage"] = "scenario"
                    self.send(
                        chat,
                        "Выбери настроенный сценарий или отправь JSON как документ. Свободный текст без ИИ не интерпретируется.",
                        [
                            [
                                {
                                    "text": v.get("title", k),
                                    "callback_data": "project:" + k,
                                }
                            ]
                            for k, v in self.projects.items()
                        ],
                    )
                else:
                    self.ask_device(chat, state)
            elif value.startswith("project:") and state["stage"] == "scenario":
                key = value.split(":", 1)[1]
                if key in self.projects:
                    state["text"] = self.projects[key]
                    self.ask_device(chat, state)
            elif value.startswith("device:") and state["stage"] == "device":
                device = value.split(":", 1)[1]
                if device not in ("desktop", "mobile", "both"):
                    return
                state["devices"] = (
                    ["desktop", "mobile"] if device == "both" else [device]
                )
                state["stage"] = "figma"
                state["references"] = []
                state["next_reference"] = 0
                self.send(
                    chat,
                    "Необязательно: отправь экспорт Figma PNG 1× как документ. Для каждого выбранного устройства нужен отдельный файл; порядок: "
                    + ", ".join(state["devices"])
                    + ". По умолчанию сравнивается весь фрейм с введённым URL. Блок, другую страницу и исключения задаёт разработчик в visual сценария. Фото не используется как эталон.",
                    [
                        [
                            {
                                "text": "Продолжить без дополнительных макетов",
                                "callback_data": "figma:done",
                            }
                        ]
                    ],
                )
            elif value == "figma:done" and state["stage"] == "figma":
                state["stage"] = "confirm"
                self.send(
                    chat,
                    "Сайт: "
                    + state["url"]
                    + "\nУстройства: "
                    + ", ".join(state["devices"])
                    + "\nМакетов: "
                    + str(len(state["references"]))
                    + "\nЗаказы, платежи и реальные заявки исключены. Запустить?",
                    [[{"text": "Запустить проверку", "callback_data": "run"}]],
                )
            elif value == "run" and state["stage"] == "confirm":
                self.enqueue(user, chat, state)
            return
        if message.get("document"):
            if self.states.get(user, {}).get("stage") in ("scenario", "figma"):
                self.receive_config(user, chat, message["document"])
            else:
                self.receive_source(user, chat, message["document"])
            return
        if message.get("photo"):
            self.send(
                chat,
                "Для точного сравнения нужен PNG как документ, без сжатия Telegram.",
            )
            return
        text = message.get("text", "").strip()
        command = text.split()[0] if text else ""
        if command in ("/start", "/help"):
            self.send(
                chat,
                "Тестировщик интернет-магазинов\n/test — новый тест\n/code — проверить синтаксис исходников ZIP\n/status — последние задачи\n/cancel — сбросить ввод и отменить задачи\n\nОтчёт содержит только проблемы и ограничения проверки. Исходники сайта не анализируются.",
            )
        elif command == "/code":
            self.states[user] = {"stage": "source"}
            self.send(
                chat,
                "Пришли ZIP с исходниками до 10 МБ, без .env, ключей, vendor, node_modules и ядра CMS. Проверяется синтаксис Python/JS/PHP/JSON; это не полная оценка качества кода.",
            )
        elif command == "/test":
            self.states[user] = {"stage": "url"}
            self.send(chat, "Пришли публичный URL сайта для проверки.")
        elif command == "/cancel":
            self.states.pop(user, None)
            with self.connection() as db:
                db.execute(
                    "UPDATE jobs SET status='cancelled' WHERE user=? AND status IN ('queued', 'running')",
                    (user,),
                )
            self.send(chat, "Ввод сброшен. Активные задачи отменены.")
        elif command == "/status":
            with self.connection() as db:
                rows = db.execute(
                    "SELECT id,status FROM jobs WHERE user=? ORDER BY created DESC LIMIT 5",
                    (user,),
                ).fetchall()
            labels = {
                "queued": "В очереди",
                "running": "Проверяется",
                "completed": "Завершено",
                "failed": "Сбой проверки",
                "cancelled": "Отменено",
                "interrupted": "Прервано перезапуском",
            }
            self.send(
                chat,
                "\n".join(f"{i}: {labels.get(s, s)}" for i, s in rows)
                or "Задач пока нет.",
            )
        else:
            state = self.states.get(user)
            if not state:
                self.send(chat, "Для новой проверки нажми /test")
            elif state["stage"] == "url":
                try:
                    validate_url(text)
                except (ValueError, OSError):
                    self.send(
                        chat,
                        "Нужен доступный публичный URL http(s), без логина и пароля.",
                    )
                    return
                state.update(url=text, stage="preset")
                self.send(
                    chat,
                    "Что проверить?",
                    [
                        [{"text": title, "callback_data": "preset:" + key}]
                        for key, (title, _) in PRESETS.items()
                    ],
                )
            elif state["stage"] == "scenario":
                self.send(
                    chat,
                    "Выбери готовый сценарий или отправь структурированный JSON как документ.",
                )

    def receive_config(self, user, chat, document):
        state = self.states[user]
        suffix = ".json" if state["stage"] == "scenario" else ".png"
        if (
            not document.get("file_name", "").lower().endswith(suffix)
            or document.get("file_size", 0) > 10 * 1024 * 1024
        ):
            self.send(chat, "Нужен документ " + suffix + " до 10 МБ.")
            return
        metadata = self.api("getFile", {"file_id": document["file_id"]})
        remote = metadata["file_path"]
        if not remote.startswith("documents/") or ".." in remote or "?" in remote:
            raise ValueError("Unexpected Telegram file path")
        target = self.data / ("upload_" + uuid.uuid4().hex + suffix)
        try:
            with requests.get(
                f"https://api.telegram.org/file/bot{self.token}/{remote}",
                stream=True,
                timeout=(10, 40),
            ) as response:
                response.raise_for_status()
                with target.open("wb") as handle:
                    size = 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 10 * 1024 * 1024:
                            raise ValueError("Файл слишком большой")
                        handle.write(chunk)
            if suffix == ".json":
                from ai_tester.structured import load_project

                state["text"] = load_project(target)
                self.ask_device(chat, state)
            else:
                from ai_tester.visual import inspect_png

                inspect_png(target)
                index = state["next_reference"]
                if index >= len(state["devices"]):
                    self.send(chat, "Макеты для всех выбранных устройств уже получены.")
                    return
                device = state["devices"][index]
                config = state.get("text", {}).get("visual", {}).get(device, {})
                ref = dict(config, path=str(target), device=device)
                ref.setdefault("url", state["url"])
                state["references"].append(ref)
                state["next_reference"] += 1
                self.send(
                    chat,
                    "PNG сопоставлен: "
                    + device
                    + ", страница "
                    + ref["url"]
                    + ", область "
                    + str(ref.get("locator", "весь фрейм"))
                    + ". Продолжить кнопкой или отправить следующий PNG.",
                )
        except requests.RequestException:
            target.unlink(missing_ok=True)
            raise RuntimeError("Telegram file download failed") from None
        except (ValueError, OSError) as exc:
            self.send(chat, "Не удалось прочитать документ: " + str(exc)[:400])
        finally:
            if suffix == ".json" or not any(
                r["path"] == str(target) for r in state.get("references", [])
            ):
                target.unlink(missing_ok=True)

    def receive_source(self, user, chat, document):
        if self.states.get(user, {}).get("stage") != "source":
            self.send(chat, "Для проверки исходников сначала нажми /code")
            return
        if (
            not document.get("file_name", "").lower().endswith(".zip")
            or document.get("file_size", 0) > 10 * 1024 * 1024
        ):
            self.send(chat, "Нужен ZIP до 10 МБ.")
            return
        with self.connection() as db:
            active = db.execute(
                "SELECT count(*) FROM jobs WHERE user=? AND status IN ('queued','running')",
                (user,),
            ).fetchone()[0]
            pending = db.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
        if active or pending >= 20:
            self.send(chat, "У тебя уже есть активная задача или очередь заполнена.")
            return
        metadata = self.api("getFile", {"file_id": document["file_id"]})
        remote = metadata["file_path"]
        if not remote.startswith("documents/") or ".." in remote or "?" in remote:
            raise ValueError("Unexpected Telegram file path")
        ident = uuid.uuid4().hex[:12]
        folder = self.data / ident
        folder.mkdir()
        try:
            with requests.get(
                f"https://api.telegram.org/file/bot{self.token}/{remote}",
                stream=True,
                timeout=(10, 40),
            ) as response:
                response.raise_for_status()
                size = 0
                with (folder / "source.zip").open("wb") as handle:
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 10 * 1024 * 1024:
                            raise ValueError("Archive too large")
                        handle.write(chunk)
        except requests.RequestException:
            raise RuntimeError("Telegram file download failed") from None
        (folder / "job.json").write_text(json.dumps({"kind": "source"}))
        with self.connection() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?)",
                (ident, user, chat, "queued", str(folder), time.time()),
            )
        self.states.pop(user, None)
        self.send(chat, f"Проверка исходников {ident} в очереди.")

    def ask_device(self, chat, state):
        state["stage"] = "device"
        self.send(
            chat,
            "На каком экране?",
            [
                [{"text": title, "callback_data": "device:" + key}]
                for key, title in [
                    ("desktop", "Компьютер"),
                    ("mobile", "Телефон"),
                    ("both", "Оба"),
                ]
            ],
        )

    def enqueue(self, user, chat, state):
        with self.connection() as db:
            active = db.execute(
                "SELECT count(*) FROM jobs WHERE user=? AND status IN ('queued','running')",
                (user,),
            ).fetchone()[0]
            pending = db.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            if active or pending >= 20:
                self.send(
                    chat,
                    "У тебя уже есть активная задача или очередь заполнена. Проверь /status.",
                )
                return
            ident = uuid.uuid4().hex[:12]
            folder = self.data / ident
            folder.mkdir()
            job = {
                "url": state["url"],
                "devices": state["devices"],
                "scenario": make_scenario(state["preset"], state.get("text", "")),
                "references": [dict(r) for r in state.get("references", [])],
            }
            for i, ref in enumerate(job["references"]):
                original = Path(ref["path"])
                destination = folder / ("reference_%d.png" % i)
                shutil.copyfile(original, destination)
                original.unlink(missing_ok=True)
                ref["path"] = str(destination)
            (folder / "job.json").write_text(
                json.dumps(job, ensure_ascii=False), encoding="utf-8"
            )
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?)",
                (ident, user, chat, "queued", str(folder), time.time()),
            )
        self.states.pop(user, None)
        self.send(
            chat,
            f"Задача {ident} в очереди. Проверка займёт несколько минут. /cancel — отменить.",
        )

    def cleanup(self):
        if time.time() - self.last_cleanup < 3600:
            return
        self.last_cleanup = time.time()
        cutoff = time.time() - max(1, self.retention_days) * 86400
        with self.connection() as db:
            rows = db.execute(
                "SELECT id,path FROM jobs WHERE created<? AND status NOT IN ('queued','running')",
                (cutoff,),
            ).fetchall()
            for ident, path in rows:
                target = Path(path).resolve()
                if target.parent == self.data:
                    shutil.rmtree(target, ignore_errors=True)
                db.execute("DELETE FROM jobs WHERE id=?", (ident,))

    def worker(self):
        while not self.stop.wait(1):
            self.cleanup()
            with self.connection() as db:
                row = db.execute(
                    "SELECT id,chat,path FROM jobs WHERE status='queued' ORDER BY created LIMIT 1"
                ).fetchone()
                if not row:
                    continue
                ident, chat, path = row
                db.execute(
                    "UPDATE jobs SET status='running' WHERE id=? AND status='queued'",
                    (ident,),
                )
            try:
                self.send(chat, f"Проверка {ident} началась.")
                folder = Path(path)
                env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
                with (folder / "worker.log").open("w") as log:
                    process = subprocess.Popen(
                        [sys.executable, str(ROOT / "scripts/run_job.py"), path],
                        cwd=ROOT,
                        env=env,
                        stdout=log,
                        stderr=log,
                        start_new_session=True,
                    )
                    status = "running"
                    deadline = time.monotonic() + 1800
                    last_progress = ""
                    while process.poll() is None:
                        with self.connection() as db:
                            status = db.execute(
                                "SELECT status FROM jobs WHERE id=?", (ident,)
                            ).fetchone()[0]
                        if status == "cancelled" or time.monotonic() > deadline:
                            os.killpg(process.pid, signal.SIGTERM)
                            try:
                                process.wait(10)
                            except subprocess.TimeoutExpired:
                                os.killpg(process.pid, signal.SIGKILL)
                                process.wait()
                            if status == "cancelled":
                                break
                            raise RuntimeError("Истекло время проверки")
                        progress = folder / "progress.txt"
                        if progress.exists():
                            current = progress.read_text()
                            if current != last_progress:
                                self.send(chat, current)
                                last_progress = current
                        self.stop.wait(2)
                    if status == "cancelled":
                        continue
                if process.returncode or not (folder / "result.json").exists():
                    raise RuntimeError(
                        "Проверка не завершилась; проверь доступность сайта и журнал worker.log"
                    )
                with self.connection() as db:
                    if (
                        db.execute(
                            "SELECT status FROM jobs WHERE id=?", (ident,)
                        ).fetchone()[0]
                        == "cancelled"
                    ):
                        continue
                results = json.loads((folder / "result.json").read_text())
                incomplete = any(r.get("completed") is False for r in results)
                lines = [
                    f"Проверка {ident}: "
                    + ("запуск не завершён." if incomplete else "завершена.")
                ]
                for result in results:
                    missing = result.get("coverage", {}).get("missing", [])
                    visual_count = sum(
                        v.get("metrics", {}).get("changed_pixels", 0) > 0
                        for v in result.get("visual", [])
                    )
                    lines.append(
                        f"Пропущено: {len(missing)}; визуальных расхождений на подтверждение: {visual_count}; ошибок тестировщика: {len(result.get('tester_errors', []))}"
                    )
                    findings = result["findings"]
                    lines.append(
                        f"\n{result['device']}: {len(findings)} подтверждённых дефектов; визуальных сравнений: {len(result.get('visual', []))}. {result['reason'][:250]}"
                    )
                    for f in findings[:5]:
                        lines.append(
                            f"• [{f.get('severity', 'medium')}] {f['title'][:180]}"
                        )
                    if result.get("limitations"):
                        lines.append("Есть ограничения проверки — см. отчёт.")
                archive = folder / "developer-report.zip"
                with ZipFile(archive, "w", ZIP_DEFLATED) as z:
                    for device in ("desktop", "mobile", "source"):
                        target = folder / device
                        if target.exists():
                            for f in target.iterdir():
                                if f.suffix in (".html", ".json", ".md", ".png"):
                                    z.write(f, f"{device}/{f.name}")
                self.send(
                    chat,
                    "\n".join(lines)[:3800]
                    + "\n\nПолный список правок и доказательства — в архиве.",
                )
                if archive.stat().st_size > 45 * 1024 * 1024:
                    self.send(
                        chat,
                        "Архив слишком большой для отправки. Отправляю текстовые списки правок.",
                    )
                    for result in results:
                        self.document(chat, folder / result["device"] / "report.md")
                else:
                    self.document(chat, archive)
                with self.connection() as db:
                    db.execute(
                        "UPDATE jobs SET status=? WHERE id=? AND status!='cancelled'",
                        ("failed" if incomplete else "completed", ident),
                    )
            except Exception:  # noqa: BLE001 - isolate jobs/updates and keep service alive
                LOG.error("Job %s failed", ident)
                with self.connection() as db:
                    db.execute(
                        "UPDATE jobs SET status='failed' WHERE id=? AND status!='cancelled'",
                        (ident,),
                    )
                with contextlib.suppress(Exception):
                    self.send(
                        chat,
                        f"Задача {ident} не завершилась. Администратору нужно проверить доступность сайта, настройку сценария и журнал worker.log. Это не означает, что сайт исправен.",
                    )

    def run(self):
        threading.Thread(target=self.worker, daemon=True).start()
        with self.connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key='offset'").fetchone()
        offset = row[0] if row else 0
        while not self.stop.is_set():
            try:
                updates = self.api(
                    "getUpdates",
                    {
                        "offset": offset,
                        "timeout": 25,
                        "allowed_updates": json.dumps(["message", "callback_query"]),
                    },
                )
                for update in updates:
                    try:
                        self.handle(update)
                    except Exception:  # noqa: BLE001 - isolate jobs/updates and keep service alive
                        LOG.error("Update processing failed")
                    offset = update["update_id"] + 1
                    with self.connection() as db:
                        db.execute(
                            "INSERT OR REPLACE INTO settings VALUES ('offset',?)",
                            (offset,),
                        )
            except RuntimeError:
                LOG.warning("Telegram polling unavailable; retrying")
                self.stop.wait(5)


def main():
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    Bot().run()


if __name__ == "__main__":
    main()
