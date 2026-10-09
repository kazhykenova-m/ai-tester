"""Public-web-only URL validation for a shared test service."""
import ipaddress
import socket
from urllib.parse import urlsplit


def validate_url(value, resolve=True):
    u = urlsplit(value)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
        raise ValueError('Нужна публичная ссылка http(s) без логина и пароля')
    if u.port not in (None, 80, 443):
        raise ValueError('Разрешены только порты 80 и 443')
    host = u.hostname.lower()
    if host == 'localhost' or host.endswith(('.local', '.internal', '.localhost')):
        raise ValueError('Локальные адреса запрещены')
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = ([ipaddress.ip_address(a[4][0]) for a in
                      socket.getaddrinfo(host, u.port or 443, type=socket.SOCK_STREAM)]
                     if resolve else [])
    if any(not a.is_global for a in addresses):
        raise ValueError('Внутренние и служебные адреса запрещены')
    return value
