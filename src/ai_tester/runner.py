"""Deterministic browser checks of visited pages."""

import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from playwright.sync_api import Error, TimeoutError, expect
from ai_tester.structured import locator, validate_scenario
from ai_tester.report import write_reports
from ai_tester.visual import compare, inspect_png

AUDIT = r"""() => {
 const visible = e => {const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility==='visible'&&s.display!=='none'&&s.opacity!=='0'};
 const clipped = e => {for(let p=e.parentElement;p;p=p.parentElement){if(['hidden','clip','auto','scroll'].includes(getComputedStyle(p).overflowX))return true}return false};
 const rows=[]; const width=document.documentElement.clientWidth;
 for(const e of document.querySelectorAll('img'))if(visible(e)&&e.complete&&e.naturalWidth===0)rows.push({category:'image',title:'Изображение не загрузилось',evidence:e.currentSrc});
 for(const e of document.querySelectorAll('body *')){if(!visible(e)||clipped(e)||e.matches('[aria-hidden="true"],svg,svg *')||e.closest('[aria-hidden="true"],[data-audit-ignore]'))continue;const r=e.getBoundingClientRect();if(r.right>width+2||r.left< -2)rows.push({category:'layout',title:'Элемент выходит за ширину страницы',evidence:e.tagName+' '+(e.id||'')+' '+Math.round(r.left)+'..'+Math.round(r.right)});}
 if(document.documentElement.scrollWidth>width+2)rows.push({category:'layout',title:'Горизонтальная прокрутка страницы',evidence:document.documentElement.scrollWidth+' > '+width});
 return rows.slice(0,50);
}"""


def stabilize(page):
    page.add_style_tag(
        content="*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}"
    )
    return page.evaluate(
        """async () => {await Promise.race([document.fonts.ready,new Promise(r=>setTimeout(r,5000))]); await Promise.race([new Promise(r=>setTimeout(r,5000)),Promise.all([...document.images].map(i=>i.complete?Promise.resolve():new Promise(r=>{i.addEventListener('load',r,{once:true});i.addEventListener('error',r,{once:true});})))]);return document.fonts.status==='loaded' && [...document.images].every(i=>i.complete);}"""
    )


def run(
    context,
    scenario,
    url,
    out_dir,
    device="desktop",
    allowed=(),
    ignored=None,
    limitations=None,
    cancelled=lambda: False,
    references=(),
    validator=None,
):
    scenario = validate_scenario(scenario)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ignored = ignored if ignored is not None else set()
    limits = limitations if limitations is not None else []
    from ai_tester.security import validate_url

    validator = validator or validate_url
    start = time.monotonic()
    result = dict(
        title=scenario.get("title", "Аудит сайта"),
        url=url,
        device=device,
        started=datetime.now(timezone.utc).isoformat(),
        duration=0,
        success=False,
        completed=False,
        reason="Не завершено",
        findings=[],
        visual=[],
        third_party=[],
        tester_errors=[],
        checks=[],
        issues=[],
        steps=[],
        limitations=limits,
        coverage={"total": 0, "passed": 0, "missing": [], "visited": []},
    )
    pending = []
    page = context.new_page()
    page.set_default_timeout(3000)
    context.on("page", lambda p: p.close() if p != page else None)

    def event(category, title, evidence, address=None):
        pending.append(
            dict(
                category=category,
                title=title,
                evidence=str(evidence)[:1500],
                url=address or page.url,
            )
        )

    page.on(
        "pageerror",
        lambda error: event("javascript", "Необработанная JS-ошибка", error),
    )
    page.on(
        "console",
        lambda msg: event("console", "console.error", msg.text)
        if msg.type == "error"
        else None,
    )
    page.on(
        "response",
        lambda response: event(
            "network", "HTTP %s" % response.status, response.url, response.url
        )
        if response.status >= 400
        else None,
    )
    page.on(
        "requestfailed",
        lambda request: event(
            "network", "Неудачный запрос", request.failure, request.url
        ),
    )

    def prepare():
        ready = stabilize(page)
        if not ready:
            limits.append("Не дождались всех шрифтов/изображений за ограниченное время")
        return ready

    reproduction = []

    def finding(row):
        row.update(
            id=str(len(result["findings"]) + 1),
            device=device,
            severity="high" if row["category"] == "javascript" else "medium",
            actual=row.get("evidence", row["title"]),
            expected=row.get("expected", "Элемент работает без наблюдаемой ошибки"),
            reproduction=list(reproduction),
        )
        name = "defect_%03d.png" % (len(result["findings"]) + 1)
        try:
            page.screenshot(path=str(out / name), animations="disabled")
            row["screenshot"] = name
        except Error:
            pass
        result["findings"].append(row)

    try:
        for config in scenario["pages"]:
            if cancelled():
                raise InterruptedError("Отменено")
            target = urljoin(url, config.get("url", ""))
            validator(target)
            reproduction = ["Открыть " + target]
            page.goto(target, wait_until="domcontentloaded")
            prepare()
            result["coverage"].setdefault("visited", []).append(page.url)
            for step in config.get("steps", []):
                if cancelled():
                    raise InterruptedError("Отменено")
                title = step.get("name", step["action"])
                result["coverage"]["total"] += 1
                if not step.get("locator") and step["action"] != "url":
                    result["coverage"]["missing"].append(
                        title + ": локатор не настроен"
                    )
                    continue
                if (
                    step.get("effect", "read") != "read"
                    and urlsplit(page.url).hostname not in allowed
                ):
                    result["coverage"]["missing"].append(
                        title + ": требуется явно разрешённый тестовый домен"
                    )
                    continue
                element = (
                    locator(page, step["locator"]) if step.get("locator") else None
                )
                if (
                    element is not None
                    and not element.count()
                    and step.get("optional", True)
                ):
                    result["coverage"]["missing"].append(
                        title + ": элемент отсутствует"
                    )
                    continue
                reproduction.append(title)
                try:
                    action = step["action"]
                    value = step.get("value")
                    if action == "click":
                        form = element.evaluate(
                            "(e)=>{const f=e.form||e.closest('form');const submit=e.matches('button:not([type=button]),input[type=submit],input[type=image]');return {submit:!!(f&&submit),invalid:!!(f&&!f.checkValidity())}}"
                        )
                        if form["submit"] and not (
                            step.get("effect") == "invalid_form"
                            and form["invalid"]
                            and urlsplit(page.url).hostname in allowed
                        ):
                            result["coverage"]["missing"].append(
                                title + ": отправка реальной формы запрещена"
                            )
                            continue
                        element.click()
                    elif action == "fill":
                        element.fill(value)
                    elif action == "select":
                        element.select_option(value)
                    elif action == "check":
                        element.check()
                    elif action == "press":
                        if value not in ("Escape", "Tab", "ArrowLeft", "ArrowRight"):
                            raise ValueError(
                                "Клавиша не разрешена; Enter может отправить форму"
                            )
                        element.press(value)
                    elif action == "visible":
                        expect(element).to_be_visible()
                        if device == "mobile":
                            box = element.bounding_box()
                            width = page.viewport_size["width"]
                            assert (
                                box
                                and box["x"] >= 0
                                and box["x"] + box["width"] <= width + 2
                            ), "Основной элемент обрезан на мобильном экране"
                            assert element.is_enabled(), "Основной элемент недоступен"
                    elif action == "hidden":
                        expect(element).to_be_hidden()
                    elif action == "text":
                        expect(element).to_contain_text(value)
                    elif action == "count":
                        expect(element).to_have_count(value)
                    elif action == "url":
                        expect(page).to_have_url(urljoin(url, value))
                    elif action == "invalid":
                        assert element.evaluate("(e)=>!e.checkValidity()"), (
                            "Форма принимает невалидные данные"
                        )
                    result["coverage"]["passed"] += 1
                except (AssertionError, TimeoutError) as exc:
                    finding(
                        dict(
                            category="functional",
                            title=title,
                            url=page.url,
                            evidence=str(exc)[:1500],
                            expected=step.get("expected", title),
                        )
                    )
                prepare()
                for row in page.evaluate(AUDIT):
                    row["url"] = page.url
                    if not any(
                        f["title"] == row["title"]
                        and f.get("evidence") == row.get("evidence")
                        and f["url"] == page.url
                        for f in result["findings"]
                    ):
                        finding(row)
            for row in page.evaluate(AUDIT):
                row["url"] = page.url
                if not any(
                    f["title"] == row["title"]
                    and f.get("evidence") == row.get("evidence")
                    and f["url"] == page.url
                    for f in result["findings"]
                ):
                    finding(row)
        for index, ref in enumerate(references):
            if ref["device"] != device:
                continue
            target = urljoin(url, ref.get("url", "/"))
            validator(target)
            width, height = inspect_png(ref["path"])
            page.set_viewport_size({"width": width, "height": min(height, 4000)})
            page.goto(target, wait_until="domcontentloaded")
            if not prepare():
                limits.append(
                    "Визуальное сравнение пропущено: ресурсы страницы не готовы"
                )
                continue
            name = "visual_%d" % index
            shutil.copyfile(ref["path"], out / (name + "_reference.png"))
            shot = out / (name + "_site.png")
            if ref.get("locator"):
                locator(page, ref["locator"]).screenshot(
                    path=str(shot), animations="disabled"
                )
            else:
                page.screenshot(path=str(shot), full_page=True, animations="disabled")
            # A second capture verifies that the page stopped changing.
            stable = out / (name + "_stability.png")
            if ref.get("locator"):
                locator(page, ref["locator"]).screenshot(
                    path=str(stable), animations="disabled"
                )
            else:
                page.screenshot(path=str(stable), full_page=True, animations="disabled")
            try:
                stability = compare(
                    shot,
                    stable,
                    out / (name + "_stability_diff.png"),
                    0,
                    1,
                    ref.get("exclude", []),
                )
                identical = not stability["changed_pixels"]
            except ValueError:
                identical = False
            stable.unlink()
            (out / (name + "_stability_diff.png")).unlink(missing_ok=True)
            if not identical:
                limits.append(
                    "Визуальное сравнение пропущено: скриншот нестабилен, настройте исключения или остановите динамический контент"
                )
                continue
            try:
                metrics = compare(
                    out / (name + "_reference.png"),
                    shot,
                    out / (name + "_diff.png"),
                    ref.get("threshold", 30),
                    ref.get("min_area", 100),
                    ref.get("exclude", []),
                )
                result["visual"].append(
                    dict(
                        url=target,
                        device=device,
                        area=ref.get("locator", "Весь фрейм"),
                        metrics=metrics,
                        status="визуальное расхождение — требуется подтверждение"
                        if metrics["changed_pixels"]
                        else "Совпадает",
                        reference=name + "_reference.png",
                        screenshot=name + "_site.png",
                        diff=name + "_diff.png",
                    )
                )
            except ValueError as exc:
                limits.append(str(exc))
        result.update(completed=True, reason="Проверка посещённых страниц завершена")
    except InterruptedError:
        result["reason"] = "Отменено"
    except Exception as exc:
        result["tester_errors"].append(type(exc).__name__ + ": " + str(exc)[:1000])
        result["reason"] = "Сбой тестировщика; запуск не завершён"
    for row in pending:
        if (
            row["category"] == "console"
            and ignored
            and "net::ERR_FAILED" in row["evidence"]
        ):
            continue
        if row["url"] in ignored:
            continue
        if (
            row["category"] == "network"
            and urlsplit(row["url"]).hostname != urlsplit(url).hostname
        ):
            result["third_party"].append(row)
        elif not any(
            f["title"] == row["title"] and f.get("evidence") == row.get("evidence")
            for f in result["findings"]
        ):
            finding(row)
    result["success"] = (
        result["completed"] and not result["findings"] and not result["tester_errors"]
    )
    result["duration"] = round(time.monotonic() - start, 2)
    limits.append(
        "Проверены только посещённые состояния. Не обнаруживаются автоматически все ошибки стилей и бизнес-логики."
    )
    result["limitations"] = list(dict.fromkeys(limits))
    write_reports(out, result)
    return result
