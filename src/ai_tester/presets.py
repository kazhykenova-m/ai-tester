PRESETS = {
    "landing": ("Лендинг", ""),
    "shop": ("Интернет-магазин", ""),
    "custom": ("Настроенный сценарий проекта", ""),
}


def make_scenario(preset, text=""):
    if preset == "custom":
        from ai_tester.structured import validate_scenario

        return validate_scenario(text)
    names = (
        ["Меню и якоря", "Кнопка", "Модалка", "Аккордеон", "Слайдер", "Валидация формы"]
        if preset == "landing"
        else [
            "Каталог",
            "Поиск",
            "Фильтр",
            "Карточка товара",
            "Вариант",
            "Добавление в корзину",
            "Количество",
            "Удаление из корзины",
        ]
    )
    return {
        "title": PRESETS[preset][0],
        "pages": [
            {"url": "", "steps": [{"name": n, "action": "visible"} for n in names]}
        ],
    }
