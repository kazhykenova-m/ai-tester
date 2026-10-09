"""Validated, explicit browser scenarios; no free-text interpretation."""

import json
from pathlib import Path

ACTIONS = {
    "click",
    "fill",
    "select",
    "check",
    "press",
    "visible",
    "hidden",
    "text",
    "count",
    "url",
    "invalid",
}


def validate_scenario(data):
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
        raise ValueError("Нужен JSON-объект с массивом pages")
    if not data["pages"] or len(data["pages"]) > 30:
        raise ValueError("Не более 30 страниц")
    if not isinstance(data.get("test_data", []), list):
        raise ValueError("test_data должен быть массивом")
    for page in data["pages"]:
        if not isinstance(page, dict) or not isinstance(page.get("url", "/"), str):
            raise ValueError("Некорректная страница")
        if (
            not isinstance(page.get("steps", []), list)
            or len(page.get("steps", [])) > 100
        ):
            raise ValueError("Не более 100 шагов на страницу")
        for step in page.get("steps", []):
            if not isinstance(step, dict) or step.get("action") not in ACTIONS:
                raise ValueError("Неизвестное действие")
            loc = step.get("locator")
            if loc is not None and (
                not isinstance(loc, dict)
                or len(set(loc) & {"role", "label", "test_id", "css"}) != 1
            ):
                raise ValueError("Локатор: role, label, test_id или css")
            if loc is not None and any(
                not isinstance(v, str) for k, v in loc.items() if k != "exact"
            ):
                raise ValueError("Значения локатора должны быть строками")
            if step.get("action") in {
                "fill",
                "select",
                "text",
                "url",
                "press",
            } and not isinstance(step.get("value"), str):
                raise ValueError("value должен быть строкой")
            if step.get("action") == "count" and (
                not isinstance(step.get("value"), int) or step["value"] < 0
            ):
                raise ValueError("count требует неотрицательное число")
            if step.get("action") in {"fill", "select"} and step.get(
                "value"
            ) not in data.get("test_data", []):
                raise ValueError("Значение должно быть явно разрешено в test_data")
            if step.get("effect", "read") not in {"read", "cart", "invalid_form"}:
                raise ValueError("Заказы, платежи и реальные заявки не поддерживаются")
    return data


def load_project(path):
    return validate_scenario(json.loads(Path(path).read_text(encoding="utf-8")))


def locator(page, spec):
    if "role" in spec:
        return page.get_by_role(
            spec["role"], name=spec.get("name"), exact=spec.get("exact", True)
        )
    if "label" in spec:
        return page.get_by_label(spec["label"], exact=True)
    if "test_id" in spec:
        return page.get_by_test_id(spec["test_id"])
    return page.locator(spec["css"])
