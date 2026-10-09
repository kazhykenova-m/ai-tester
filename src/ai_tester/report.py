import base64
import html
import json
from pathlib import Path

from ai_tester.findings import build_findings, developer_markdown

CSS = '''
body{margin:0;background:#f5f6fa;color:#172033;font:16px/1.5 system-ui,sans-serif}
main{max-width:900px;margin:auto;padding:24px}h1{font-size:26px}
.card{background:white;border:1px solid #dce1ea;border-radius:12px;padding:20px;margin:16px 0}
small,.meta{color:#596579}.badge{font-weight:700;color:#a13b24}img{max-width:100%;border-radius:8px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}h2{font-size:19px}
'''


def render_html(summary, images=None):
    esc = lambda value: html.escape(str(value), quote=True)
    images = images or {}
    findings = summary.get('findings', build_findings(summary))
    parts = ['<!doctype html><html lang="ru"><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             f'<title>{esc(summary["title"])}</title><style>{CSS}</style><body><main>',
             f'<h1>{esc(summary["title"])}</h1>',
             f'<p class="meta">{esc(summary["url"])} · {esc(summary.get("device", "desktop"))}</p>',
             f'<div class="card"><b>Найдено проблем: {len(findings)}</b><p>{esc(summary["reason"])}</p>',
             f'<small>{esc(summary["started"])} · {esc(summary["duration"])} с</small></div>']
    if not findings:
        parts.append('<p>В посещённых состояниях подтверждённых проблем не найдено. Это не подтверждает исправность всего сайта.</p>')
    for i, row in enumerate(findings, 1):
        parts.append(f'<article class="card"><span class="badge">{esc(row.get("severity", "medium"))} · {esc(row["category"])}</span><h2>{i}. {esc(row["title"])}</h2>')
        for key, label in [('url', 'Страница'), ('actual', 'Фактически'),
                           ('expected', 'Ожидается'), ('reproduction', 'Как повторить'),
                           ('evidence', 'Доказательство')]:
            if row.get(key):
                parts.append(f'<p><b>{label}</b><br>{esc(row[key])}</p>')
        src = images.get(row.get('screenshot'))
        if src:
            parts.append(f'<img src="{esc(src)}" alt="Доказательство дефекта">')
        parts.append('</article>')
    missing = summary.get('coverage', {}).get('missing', [])
    if missing or summary.get('limitations'):
        parts.append('<div class="card"><h2>Ограничения проверки</h2>')
        if missing:
            parts.append(f'<p>Не проверены пункты: {esc(missing)}</p>')
        for limit in summary.get('limitations', []):
            parts.append(f'<p>{esc(limit)}</p>')
        parts.append('</div>')
    parts.append('<p class="meta">Наблюдения ИИ требуют подтверждения. Качество исходного кода не проверялось.</p></main></body></html>')
    return ''.join(parts)


def write_reports(out_dir, summary):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    images = {}
    for row in summary.get('findings', build_findings(summary)):
        shot = row.get('screenshot')
        if shot and Path(shot).name == shot and (out / shot).is_file():
            images[shot] = 'data:image/png;base64,' + base64.b64encode((out / shot).read_bytes()).decode('ascii')
    (out / 'report.html').write_text(render_html(summary, images), encoding='utf-8')
    (out / 'report.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'report.md').write_text(developer_markdown(summary), encoding='utf-8')
