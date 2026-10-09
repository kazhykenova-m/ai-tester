import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock
import pytest
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright
from ai_tester.network import install_guard
from ai_tester.runner import run
from ai_tester.visual import compare


def test_real_callback():
    context = Mock()
    blocked = []
    ignored = set()
    guard = install_guard(context, set(), blocked, ignored, validator=lambda _: None)
    request = Mock(url="https://example.com/cart", method="POST")
    route = Mock(request=request)
    guard(route, request)
    assert blocked and request.url in ignored
    route.abort.assert_called_once()
    route.continue_.assert_not_called()


@pytest.mark.parametrize("mode", ["same", "difference", "excluded", "size"])
def test_visual(tmp_path, mode):
    a = Image.new("RGB", (50, 50), "white")
    b = a.copy()
    if mode in ("difference", "excluded"):
        ImageDraw.Draw(b).rectangle((5, 5, 20, 20), fill="black")
    if mode == "size":
        b = Image.new("RGB", (51, 50), "white")
    a.save(tmp_path / "a.png")
    b.save(tmp_path / "b.png")
    if mode == "size":
        with pytest.raises(ValueError, match="Несовместимые"):
            compare(tmp_path / "a.png", tmp_path / "b.png", tmp_path / "diff.png")
    else:
        r = compare(
            tmp_path / "a.png",
            tmp_path / "b.png",
            tmp_path / "diff.png",
            min_area=10,
            exclude=[(5, 5, 20, 20)] if mode == "excluded" else [],
        )
        assert bool(r["changed_pixels"]) == (mode == "difference")


class Demo(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/missing":
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/shop":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"""<h1>Catalog</h1><select aria-label="Size"><option>41</option><option>42</option></select><button onclick="document.querySelector('#cart').hidden=false">Add</button><div id="cart" hidden><input aria-label="Quantity" value="1"><button onclick="document.querySelector('#cart').remove()">Remove</button></div>"""
            )
            return
        if self.path == "/form":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b'<form action="/real"><button>Submit</button></form>')
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"""<!doctype html><html><body><h1>Demo</h1><button id="open" onclick="document.querySelector('#dialog').hidden=false">Open</button><div id="dialog" hidden>Modal</div><button id="post" onclick="fetch('/cart',{method:'POST'}).catch(()=>{})">Cart</button><input aria-label="Email" type="email" required><script>if(location.pathname=='/errors'){setTimeout(()=>{throw Error('demo-js')},0);console.error('demo-console');fetch('/missing');document.body.insertAdjacentHTML('beforeend','<img width=40 height=40 src=/missing><div style=width:2000px>Overflow</div>')}</script></body></html>"""
        )

    def do_POST(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_):
        pass


@pytest.fixture(scope="module")
def demo():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Demo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % server.server_port
    server.shutdown()
    server.server_close()


@pytest.fixture
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


def execute(browser, demo, tmp_path, steps=(), **kwargs):
    context = browser.new_context()
    ignored = set()
    limits = []
    install_guard(context, set(), limits, ignored, validator=lambda _: None)
    result = run(
        context,
        {
            "title": "Demo",
            "pages": [{"url": kwargs.pop("page", "/"), "steps": list(steps)}],
        },
        demo,
        tmp_path,
        ignored=ignored,
        limitations=limits,
        validator=lambda _: None,
        **kwargs,
    )
    context.close()
    return result


def test_real_scenario_success(browser, demo, tmp_path):
    r = execute(
        browser,
        demo,
        tmp_path,
        [
            {"action": "click", "locator": {"css": "#open"}},
            {"action": "visible", "locator": {"css": "#dialog"}},
        ],
    )
    assert r["success"] and r["completed"] and r["coverage"]["passed"] == 2
    assert not list(tmp_path.glob("defect*"))


def test_real_defect(browser, demo, tmp_path):
    r = execute(
        browser,
        demo,
        tmp_path,
        [
            {
                "name": "Wrong heading",
                "action": "text",
                "value": "Other",
                "locator": {"role": "heading"},
            }
        ],
    )
    assert (
        r["completed"]
        and not r["success"]
        and r["findings"][0]["category"] == "functional"
    )
    assert (tmp_path / r["findings"][0]["screenshot"]).exists()


def test_real_missing(browser, demo, tmp_path):
    r = execute(
        browser,
        demo,
        tmp_path,
        [
            {"action": "visible", "locator": {"test_id": "absent"}},
            {"action": "visible"},
        ],
    )
    assert r["success"] and len(r["coverage"]["missing"]) == 2 and not r["findings"]


def test_real_blocked_not_defect(browser, demo, tmp_path):
    r = execute(
        browser, demo, tmp_path, [{"action": "click", "locator": {"css": "#post"}}]
    )
    assert r["limitations"] and not any(
        f["category"] == "network" for f in r["findings"]
    )


def test_cancelled(browser, demo, tmp_path):
    r = execute(browser, demo, tmp_path, cancelled=lambda: True)
    assert not r["completed"] and not r["success"] and r["reason"] == "Отменено"


def test_incomplete(browser, demo, tmp_path):
    r = execute(
        browser,
        demo,
        tmp_path,
        [{"action": "press", "value": "Enter", "locator": {"css": "#open"}}],
    )
    assert not r["completed"] and not r["success"] and r["tester_errors"]
    assert (
        "подтверждённых проблем не найдено"
        not in (tmp_path / "report.html").read_text()
    )


def test_real_errors(browser, demo, tmp_path):
    r = execute(
        browser,
        demo,
        tmp_path,
        [{"action": "text", "value": "Demo", "locator": {"role": "heading"}}],
        page="/errors",
    )
    assert any(f["category"] == "console" for f in r["findings"])
    assert any(f["category"] == "javascript" for f in r["findings"])
    assert any(f["category"] == "image" for f in r["findings"])
    assert any(f["category"] == "layout" for f in r["findings"])
    assert any(f["category"] == "network" for f in r["findings"])


def test_zip_path(tmp_path):
    from zipfile import ZipFile
    from ai_tester.source_audit import audit_zip

    path = tmp_path / "bad.zip"
    with ZipFile(path, "w") as z:
        z.writestr("../bad.py", "pass")
    with pytest.raises(ValueError, match="Опасный"):
        audit_zip(path)


def test_real_visual_integration(browser, demo, tmp_path):
    from ai_tester.runner import stabilize

    context = browser.new_context(viewport={"width": 320, "height": 240})
    page = context.new_page()
    page.goto(demo)
    stabilize(page)
    reference = tmp_path / "reference.png"
    page.screenshot(path=str(reference), full_page=True, animations="disabled")
    context.close()
    result = execute(
        browser,
        demo,
        tmp_path / "report",
        references=[{"device": "desktop", "url": demo, "path": str(reference)}],
    )
    assert result["completed"] and result["visual"][0]["metrics"]["changed_pixels"] == 0
    assert (tmp_path / "report" / result["visual"][0]["diff"]).exists()
    assert "data:image/png;base64" in (tmp_path / "report" / "report.html").read_text()


def test_real_form_submit_skipped(browser, demo, tmp_path):
    r = execute(
        browser,
        demo,
        tmp_path,
        page="/form",
        steps=[
            {
                "name": "Submit",
                "action": "click",
                "locator": {"role": "button", "name": "Submit"},
            }
        ],
    )
    assert (
        r["completed"]
        and not r["findings"]
        and any("реальной формы" in m for m in r["coverage"]["missing"])
    )


def test_bot_structured_workflow(tmp_path, monkeypatch):
    from ai_tester.bot import Bot

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "42")
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("ai_tester.bot.validate_url", lambda _: None)
    bot = Bot()
    bot.send = Mock()
    bot.api = Mock()

    def message(text):
        bot.handle(
            {
                "message": {
                    "from": {"id": 42},
                    "chat": {"id": 42, "type": "private"},
                    "text": text,
                }
            }
        )

    def callback(data):
        bot.handle(
            {
                "callback_query": {
                    "id": "cb",
                    "from": {"id": 42},
                    "data": data,
                    "message": {"chat": {"id": 42, "type": "private"}},
                }
            }
        )

    message("/test")
    message("https://example.com/path")
    callback("preset:custom")
    callback("project:landing")
    callback("device:both")
    assert bot.states[42]["stage"] == "figma"
    callback("figma:done")
    assert bot.states[42]["stage"] == "confirm"
    callback("run")
    with bot.connection() as db:
        folder = db.execute("SELECT path FROM jobs").fetchone()[0]
    import json
    from pathlib import Path

    job = json.loads((Path(folder) / "job.json").read_text())
    assert isinstance(job["scenario"], dict) and job["devices"] == ["desktop", "mobile"]


def test_real_cart_lifecycle(browser, demo, tmp_path):
    context = browser.new_context()
    steps = [
        {"action": "select", "locator": {"label": "Size"}, "value": "42"},
        {
            "action": "click",
            "effect": "cart",
            "locator": {"role": "button", "name": "Add"},
        },
        {
            "action": "visible",
            "effect": "cart",
            "optional": False,
            "locator": {"css": "#cart"},
        },
        {
            "action": "fill",
            "effect": "cart",
            "locator": {"label": "Quantity"},
            "value": "2",
        },
        {
            "action": "click",
            "effect": "cart",
            "locator": {"role": "button", "name": "Remove"},
        },
        {
            "action": "count",
            "effect": "cart",
            "optional": False,
            "locator": {"css": "#cart"},
            "value": 0,
        },
    ]
    result = run(
        context,
        {
            "title": "Shop",
            "test_data": ["42", "2"],
            "pages": [{"url": "/shop", "steps": steps}],
        },
        demo,
        tmp_path,
        allowed={"127.0.0.1"},
        validator=lambda _: None,
    )
    context.close()
    assert result["success"] and result["coverage"]["passed"] == 6


def test_bot_png_document(tmp_path, monkeypatch):
    import io
    from ai_tester.bot import Bot

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "42")
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    bot = Bot()
    bot.send = Mock()
    bot.api = Mock(return_value={"file_path": "documents/reference.png"})
    stream = io.BytesIO()
    Image.new("RGB", (390, 844), "white").save(stream, format="PNG")
    response = Mock()
    response.iter_content.return_value = [stream.getvalue()]
    manager = Mock()
    manager.__enter__ = Mock(return_value=response)
    manager.__exit__ = Mock(return_value=False)
    monkeypatch.setattr("ai_tester.bot.requests.get", Mock(return_value=manager))
    bot.states[42] = {
        "stage": "figma",
        "url": "https://example.com/page",
        "devices": ["mobile"],
        "next_reference": 0,
        "references": [],
    }
    bot.receive_config(
        42,
        42,
        {
            "file_id": "file",
            "file_name": "frame.png",
            "file_size": len(stream.getvalue()),
        },
    )
    ref = bot.states[42]["references"][0]
    assert ref["device"] == "mobile" and ref["url"] == "https://example.com/page"
    from ai_tester.visual import inspect_png

    assert inspect_png(ref["path"]) == (390, 844)


def test_payment_blocked_even_on_test_domain():
    context = Mock()
    blocked = []
    ignored = set()
    guard = install_guard(
        context, {"test.example"}, blocked, ignored, validator=lambda _: None
    )
    request = Mock(url="https://test.example/api/orders", method="POST")
    route = Mock(request=request)
    guard(route, request)
    route.abort.assert_called_once()
    assert request.url in ignored
