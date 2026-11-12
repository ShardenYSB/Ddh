import argparse
import asyncio
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import app


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_file_does_not_create_session(self):
        with patch.object(app, 'TelegramClient') as factory:
            result = await app.SessionCheckerEngine().check_single_session('/missing/account.session')
        factory.assert_not_called()
        self.assertFalse(result['is_valid'])
        self.assertEqual(result['status'], 'Error')

    async def test_authorization_timeout_is_not_reported_as_valid(self):
        client = AsyncMock()
        client.get_me.return_value = SimpleNamespace(id=1, phone=None, username=None, first_name='Test')
        client.side_effect = asyncio.TimeoutError
        with tempfile.TemporaryDirectory() as folder:
            session = Path(folder) / 'test.session.session'
            session.touch()
            with patch.object(app, 'TelegramClient', return_value=client) as factory:
                result = await app.SessionCheckerEngine().check_single_session(str(session))
        self.assertEqual(factory.call_args.args[0], str(session))
        self.assertFalse(result['is_valid'])
        self.assertEqual(result['status'], 'Timeout')
        client.disconnect.assert_awaited_once()

    async def test_successful_account_is_valid(self):
        client = AsyncMock()
        client.get_me.return_value = SimpleNamespace(id=1, phone=None, username=None, first_name='Test')
        client.return_value = SimpleNamespace(authorizations=[])
        with patch.object(app.Path, 'is_file', return_value=True), patch.object(app, 'TelegramClient', return_value=client):
            result = await app.SessionCheckerEngine().check_single_session('test.session')
        self.assertTrue(result['is_valid'])
        self.assertEqual(result['status'], 'Active / Valid')

    async def test_cli_cleans_extracted_files_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / 'sessions.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr('account.session', b'test')
            extracted = []

            async def fail(files):
                extracted.extend(files)
                self.assertTrue(Path(files[0]).exists())
                raise RuntimeError('test failure')

            args = argparse.Namespace(zip=str(archive), threads=1, output=None)
            with patch.object(app.SessionCheckerEngine, 'check_multiple_sessions', side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, 'test failure'):
                    await app.run_cli(args)
            self.assertTrue(extracted)
            self.assertFalse(Path(extracted[0]).exists())


class ConfigurationTests(unittest.TestCase):
    def test_zero_concurrency_rejected(self):
        with self.assertRaises(ValueError):
            app.SessionCheckerEngine(concurrency=0)

    def test_missing_bot_token_has_clear_error(self):
        with patch.object(app, 'BOT_TOKEN', ''):
            with self.assertRaisesRegex(RuntimeError, 'BOT_TOKEN'):
                app.create_bot_app()

    def test_report_escapes_html(self):
        result = dict(file_name='a<b>.session', is_valid=False, status='Error',
                      error_message='bad <tag> & value', authorizations=[])
        report = app.format_single_result_text(result)
        self.assertIn('a&lt;b&gt;.session', report)
        self.assertIn('bad &lt;tag&gt; &amp; value', report)
        self.assertEqual(result['file_name'], 'a<b>.session')
