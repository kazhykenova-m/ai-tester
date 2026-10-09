import json
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZipFile

import pytest

from ai_tester.agent import parse_action, run_scenario
from ai_tester.bot import Bot
from ai_tester.findings import build_findings
from ai_tester.report import write_reports
from ai_tester.security import validate_url
from ai_tester.source_audit import audit_zip


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://localhost', 'http://127.0.0.1', 'http://169.254.169.254', 'http://[::1]', 'https://user:pass@example.com', 'https://example.com:8080'])
def test_private_urls_rejected(url):
    with pytest.raises(ValueError):
        validate_url(url, resolve=False)


def test_public_url():
    assert validate_url('https://example.com', resolve=False) == 'https://example.com'


def test_issue_needs_proof():
    with pytest.raises(ValueError):
        parse_action(json.dumps({'action': 'issue', 'category': 'styles', 'details': 'bad', 'evidence': ''}))


def test_reports_only_findings(tmp_path):
    summary = {'title': '<script>', 'url': 'https://example.com', 'device': 'mobile',
               'started': 'today', 'duration': 1, 'success': False, 'reason': 'done',
               'checks': [{'status': 'pass', 'text': 'PASS_SENTINEL'},
                          {'status': 'fail', 'text': 'broken form', 'details': '<b>bad</b>'}],
               'issues': ['Ошибка в консоли: x', 'Ошибка в консоли: x'], 'steps': []}
    summary['findings'] = build_findings(summary)
    write_reports(tmp_path, summary)
    html = (tmp_path / 'report.html').read_text()
    assert 'PASS_SENTINEL' not in html
    assert '<script>' not in html
    assert 'broken form' in html
    assert len(summary['findings']) == 2
    assert (tmp_path / 'report.md').exists()


def test_source_audit_never_executes(tmp_path):
    path = tmp_path / 'source.zip'
    sentinel = tmp_path / 'executed'
    with ZipFile(path, 'w') as z:
        z.writestr('good.py', f"open({str(sentinel)!r}, 'w').write('oops')")
        z.writestr('bad.py', 'def broken(:')
        z.writestr('node_modules/ignored.py', 'def broken(:')
        z.writestr('data.json', '{broken}')
        z.writestr('src/component.tsx', 'bad')
    result = audit_zip(path)
    assert not sentinel.exists()
    assert len(result['findings']) == 2
    assert result['coverage']['total'] == 3
    assert result['limitations']


def test_bot_access_and_queue(tmp_path, monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'dummy')
    monkeypatch.setenv('TELEGRAM_ALLOWED_USERS', '42')
    monkeypatch.setenv('BOT_DATA_DIR', str(tmp_path))
    bot = Bot()
    bot.send = Mock()
    bot.handle({'message': {'from': {'id': 99}, 'chat': {'id': 99, 'type': 'private'}, 'text': '/test'}})
    bot.send.assert_not_called()
    state = {'url': 'https://example.com', 'devices': ['mobile'], 'preset': 'shop'}
    bot.enqueue(42, 42, state)
    bot.enqueue(42, 42, state)
    with bot.connection() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1
    bot.handle({'message': {'from': {'id': 42}, 'chat': {'id': 42, 'type': 'private'}, 'text': '/cancel'}})
    with bot.connection() as db:
        assert db.execute('SELECT status FROM jobs').fetchone()[0] == 'cancelled'


class FakePage:
    url = 'https://example.com'

    def on(self, *_):
        pass

    def goto(self, *_args, **_kwargs):
        pass

    def is_closed(self):
        return False

    def evaluate(self, *_):
        return []

    def inner_text(self, *_):
        return 'Store'

    def title(self):
        return 'Store'

    def screenshot(self, path=None):
        if path:
            Path(path).write_bytes(b'image')
        return b'image'


class FakeContext:
    def __init__(self):
        self.pages = []
        self.listener = None

    def on(self, _, fn):
        self.listener = fn

    def new_page(self):
        page = FakePage()
        self.pages.append(page)
        self.listener(page)
        return page


class FakeLLM:
    def __init__(self, actions):
        self.actions = iter(actions)

    def generate(self, *_args, **_kwargs):
        return json.dumps(next(self.actions))


def test_scenario_only_saves_defect_evidence(tmp_path):
    actions = [
        {'action': 'check', 'index': 1, 'passed': True, 'details': 'fine'},
        {'action': 'issue', 'category': 'mobile', 'details': 'Кнопка обрезана',
         'evidence': 'Видно на скриншоте', 'expected': 'Кнопка видна целиком',
         'reproduction': 'Открыть главную на телефоне'},
        {'action': 'done', 'success': True, 'reason': 'Завершено'}]
    summary = run_scenario(FakeContext(), '# Test\n## Проверки\n- Works', FakeLLM(actions),
                           'https://example.com', out_dir=tmp_path, vision=True, device='mobile')
    assert len(summary['steps']) == 1
    assert len(list(tmp_path.glob('step_*.png'))) == 1
    assert summary['checks'] == []
    assert len(summary['findings']) == 1
    assert summary['success'] is False
    assert summary['findings'][0]['screenshot'] == 'step_02.png'


def test_partial_run_does_not_claim_success(tmp_path):
    actions = [{'action': 'done', 'success': False, 'reason': 'Не удалось пройти путь'}]
    result = run_scenario(FakeContext(), '# Test\n## Проверки\n- Form', FakeLLM(actions),
                          'https://example.com', out_dir=tmp_path)
    assert not result['success']
    assert result['coverage']['missing'] == [1]
    assert result['findings'] == []
