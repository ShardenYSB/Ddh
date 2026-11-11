import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import app


class BatchTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_disconnect_does_not_block_batch(self):
        client = AsyncMock()
        client.is_user_authorized.return_value = False

        async def stalled_disconnect():
            await asyncio.Event().wait()

        client.disconnect.side_effect = stalled_disconnect
        progress = AsyncMock()
        with patch.object(app, 'TelegramClient', return_value=client), \
                patch.object(app, 'DISCONNECT_TIMEOUT_SECONDS', 0.01):
            results = await asyncio.wait_for(
                app.SessionCheckerEngine().check_multiple_sessions(
                    ['first.session', 'second.session'], progress
                ), timeout=2,
            )
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r['status'] == 'Unauthorized / Revoked' for r in results))
        self.assertEqual(progress.await_count, 2)
        self.assertEqual(progress.await_args.args, (2, 2))
        self.assertEqual(client.disconnect.await_count, 2)

    async def test_stalled_progress_does_not_block_results(self):
        async def stalled_progress(done, total):
            await asyncio.Event().wait()

        expected = [{'file_name': 'first.session'}, {'file_name': 'second.session'}]
        engine = app.SessionCheckerEngine()
        with patch.object(engine, 'check_single_session', AsyncMock(side_effect=expected)), \
                patch.object(app, 'PROGRESS_TIMEOUT_SECONDS', 0.01):
            results = await asyncio.wait_for(
                engine.check_multiple_sessions(
                    ['first.session', 'second.session'], stalled_progress
                ), timeout=2,
            )
        self.assertEqual(results, expected)


if __name__ == '__main__':
    unittest.main()
