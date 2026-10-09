"""Request guard shared by CLI and queue worker."""

from urllib.parse import urlsplit
import re
from ai_tester.security import validate_url


def install_guard(context, allowed, limitations, ignored, validator=validate_url):
    def guard(route, request=None):
        request = request or route.request
        try:
            validator(request.url)
            if re.search(
                r"/(checkout|payments?|pay|orders?|purchase|place-order)(/|$)",
                urlsplit(request.url).path,
                re.I,
            ):
                raise ValueError("Заказы и платежи исключены из проверки")
            if (
                request.method not in ("GET", "HEAD", "OPTIONS")
                and urlsplit(request.url).hostname not in allowed
            ):
                raise ValueError("Запрос изменения данных отключён для домена")
        except (ValueError, OSError):
            ignored.add(request.url)
            limitations.append(
                "Запрос заблокирован политикой безопасности; это не дефект сайта"
            )
            route.abort()
            return
        route.continue_()

    context.route("**/*", guard)

    def block_socket(ws):
        limitations.append("WebSocket отключён; realtime не проверен")
        ws.close()

    context.route_web_socket("**/*", block_socket)
    return guard
