"""Isolated browser process for one Telegram job."""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from ai_tester.agent import run_scenario
from ai_tester.llm import GeminiClient
from ai_tester.security import validate_url


def main():
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument('job')
    args = parser.parse_args()
    root = Path(args.job)
    job = json.loads((root / 'job.json').read_text())
    if job.get('kind') == 'source':
        from ai_tester.report import write_reports
        from ai_tester.source_audit import audit_zip
        summary = audit_zip(root / 'source.zip')
        write_reports(root / 'source', summary)
        (root / 'source.zip').unlink()
        (root / 'result.json').write_text(json.dumps([summary], ensure_ascii=False), encoding='utf-8')
        return
    validate_url(job['url'])
    allowed = {v.strip().lower() for v in os.getenv('TEST_WRITE_HOSTS', '').split(',') if v.strip()}
    host = urlsplit(job['url']).hostname.lower()
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for device in job['devices']:
            mobile = device == 'mobile'
            context = browser.new_context(
                viewport={'width': 390, 'height': 844} if mobile else {'width': 1366, 'height': 850},
                is_mobile=mobile, has_touch=mobile, service_workers='block', accept_downloads=False)
            blocked = []
            ignored_requests = set()

            def guard(route, blocked=blocked, ignored_requests=ignored_requests):
                request = route.request
                try:
                    validate_url(request.url)
                except (ValueError, OSError):
                    blocked.append('Заблокирован непубличный или недоступный адрес')
                    ignored_requests.add(request.url)
                    route.abort()
                    return
                if request.method not in ('GET', 'HEAD', 'OPTIONS') and urlsplit(request.url).hostname.lower() not in allowed:
                    blocked.append('Запросы изменения данных отключены для этого домена')
                    ignored_requests.add(request.url)
                    route.abort()
                else:
                    route.continue_()

            context.route('**/*', guard)
            def block_socket(ws, blocked=blocked):
                blocked.append('WebSocket-соединение отключено: состояние realtime не проверено')
                ws.close()

            context.route_web_socket('**/*', block_socket)
            scenario = job['scenario']
            if host in allowed:
                scenario += '\nНа этом тестовом домене разрешена попытка отправки ПУСТОЙ или заведомо невалидной формы для проверки валидации. Не отправляй заполненные заявки и заказы.'
            else:
                scenario += '\nРежим read-only: POST/PUT/PATCH/DELETE заблокированы. Не считай\nнеработающую отправку или корзину дефектом; укажи ограничение проверки.'
            summary = run_scenario(context, scenario, GeminiClient(), job['url'],
                                   max_steps=60, out_dir=str(root / device), vision=True,
                                   device=device, limitations=blocked, ignored_requests=ignored_requests)
            results.append(summary)
            context.close()
        browser.close()
    (root / 'result.json').write_text(json.dumps(results, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
