import asyncio
import logging
from pathlib import Path
import tempfile
import unittest

from tg_indexer.observability import LOGGER, Progress, PrivateRotatingFileHandler
from tg_indexer.telegram_rate import RateLimiter, PacedSender, invoke_with_limits, client_class


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def clock(self):
        return self.now

    async def sleep(self, delay):
        self.waits.append(delay)
        self.now += delay


class RateTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_requests_are_spaced_without_bursts(self):
        clock = FakeTime()
        limiter = RateLimiter(2, clock=clock.clock, sleep=clock.sleep)
        sent = []

        class Sender:
            def send(self, request, ordered=False):
                sent.append(clock.now)
                result = asyncio.get_running_loop().create_future()
                result.set_result(request)
                return result

        sender = PacedSender(Sender(), limiter)
        self.assertEqual(await asyncio.gather(*[sender.send(i) for i in range(4)]), list(range(4)))
        self.assertEqual(sent, [0, 0.5, 1, 1.5])
        self.assertEqual(limiter.requests, 4)

    async def test_batch_requests_have_individual_slots(self):
        clock = FakeTime()
        limiter = RateLimiter(1, clock=clock.clock, sleep=clock.sleep)
        sent = []

        class Sender:
            def send(self, request, ordered=False):
                sent.append((request, clock.now, ordered))
                future = asyncio.get_running_loop().create_future()
                future.set_result(request)
                return future

        result = await asyncio.gather(*PacedSender(Sender(), limiter).send([1, 2, 3], ordered=True))
        self.assertEqual(result, [1, 2, 3])
        self.assertEqual(sent, [(1, 0, True), (2, 1, True), (3, 2, True)])

    async def test_long_idle_does_not_accumulate_tokens(self):
        clock = FakeTime()
        limiter = RateLimiter(2, clock=clock.clock, sleep=clock.sleep)
        await limiter.acquire()
        clock.now = 100
        await limiter.acquire()
        await limiter.acquire()
        self.assertEqual(clock.now, 100.5)

    async def test_floodwait_is_logged_waited_and_retried(self):
        from telethon.errors import FloodWaitError
        clock = FakeTime()
        limiter = RateLimiter(2, clock=clock.clock, sleep=clock.sleep)
        attempts = 0

        async def call(sender, request, **kwargs):
            nonlocal attempts
            attempts += 1
            self.assertEqual(kwargs['flood_sleep_threshold'], 0)
            if attempts == 1:
                raise FloodWaitError(request=None, capture=7)
            return 'done'

        with self.assertLogs(LOGGER, level='WARNING') as logs:
            self.assertEqual(await invoke_with_limits(call, None, None, limiter), 'done')
        self.assertEqual(clock.waits, [7])
        self.assertIn('7', logs.output[0])

    async def test_telethon_internal_retries_are_also_paced(self):
        from telethon import errors, functions, types
        clock = FakeTime()
        limiter = RateLimiter(0.1, clock=clock.clock, sleep=clock.sleep)
        client = client_class()(None, 12345, 'synthetic', limiter=limiter, request_retries=1)
        sent = []

        class Sender:
            def send(self, request, ordered=False):
                sent.append(clock.now)
                future = asyncio.get_running_loop().create_future()
                if len(sent) == 1:
                    future.set_exception(errors.ServerError(request=request, message='synthetic'))
                else:
                    future.set_result(types.User(id=1))
                return future

        await client._call(Sender(), functions.users.GetUsersRequest([types.InputUserSelf()]))
        self.assertEqual(sent, [0, 10])

    async def test_cancel_during_pacing_never_sends_request(self):
        entered = asyncio.Event()

        async def wait(delay):
            entered.set()
            await asyncio.Event().wait()

        limiter = RateLimiter(1, clock=lambda: 0, sleep=wait)
        await limiter.acquire()

        class Sender:
            def send(self, *args, **kwargs):
                raise AssertionError('cancelled request was sent')

        task = PacedSender(Sender(), limiter).send('synthetic')
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(limiter.requests, 1)

    async def test_cdn_clients_share_limiter(self):
        from unittest.mock import AsyncMock, patch
        from telethon import TelegramClient
        limiter = RateLimiter(1)
        cls = client_class()
        parent = cls(None, 12345, 'synthetic', limiter=limiter)
        child = cls(None, 12345, 'synthetic')
        with patch.object(TelegramClient, '_get_cdn_client', AsyncMock(return_value=child)):
            self.assertIs(await parent._get_cdn_client(None), child)
        self.assertIs(child.rate_limiter, limiter)

    async def test_collection_logs_progress_without_private_content_and_resumes(self):
        import json
        from types import SimpleNamespace
        from unittest.mock import patch
        from datetime import datetime, timezone
        from tg_indexer.collect import collect
        sender = SimpleNamespace(id=42, username='private_sender')
        message = SimpleNamespace(id=1, date=datetime.now(timezone.utc), file=None,
                                  photo=SimpleNamespace(sizes=[SimpleNamespace(w=32,h=32,type="s"),SimpleNamespace(w=1280,h=720,type="x")],video_sizes=[SimpleNamespace(w=1920,h=1080,type="u")]), document=None, reply_to=None, message='private_caption')

        async def get_sender():
            return sender

        message.get_sender = get_sender

        class Client:
            downloads = 0
            async def start(self):
                pass
            async def disconnect(self):
                pass
            async def get_me(self):
                return SimpleNamespace(id=7)
            async def get_entity(self, chat):
                return SimpleNamespace(id=-100123)
            async def iter_messages(self, *args, **kwargs):
                yield message
            async def download_media(self, message, *, file, progress_callback, thumb=None):
                if thumb != "x":
                    raise AssertionError("collector must request still photo, not animated video")
                Client.downloads += 1
                path = Path(file + '.jpg')
                path.write_bytes(b'synthetic image')
                progress_callback(15, 15)
                return str(path)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.json'
            config.write_text(json.dumps({'chats': ['@private_chat'], 'requests_per_second': 1, 'limit_per_chat': 10}))
            with patch.dict('os.environ', {'TG_API_ID': '12345', 'TG_API_HASH': 'private_hash'}), \
                 patch('tg_indexer.collect.client_class', return_value=lambda *a, **kw: Client()), \
                 patch('telethon.utils.get_peer_id', side_effect=lambda x: x.id), \
                 patch('telethon.utils.get_display_name', return_value='private_name'), \
                 self.assertLogs(LOGGER, level='INFO') as logs:
                await collect(config, root / 'archive')
                await collect(config, root / 'archive')
            self.assertEqual(Client.downloads, 1)
            result = json.loads((root / 'archive/selected-catalog.json').read_text())
            self.assertEqual(len(result), 1)
            self.assertEqual(len(result[0]['sources']), 1)
            output = '\n'.join(logs.output)
            self.assertIn('1/10', output)
            self.assertIn('ранее сохранено=1', output)
            for secret in ['private_chat', 'private_sender', 'private_caption', 'private_name', 'private_hash']:
                self.assertNotIn(secret, output)

    def test_invalid_rates_are_rejected(self):
        for rate in (0, -1, 21, 0.01, True, '2', float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                RateLimiter(rate)


class ProgressTests(unittest.TestCase):
    def test_finished_short_history_keeps_actual_message_count(self):
        with self.assertLogs(LOGGER, level='INFO') as logs:
            progress = Progress('Сбор', 1000)
            progress.update(12, 'synthetic', force=True)
            progress.finish('done')
        self.assertIn('12/1000', logs.output[-1])
        self.assertIn('завершено', logs.output[-1])

    def test_rotated_logs_are_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'collect.log'
            handler = PrivateRotatingFileHandler(path, maxBytes=20, backupCount=2)
            try:
                for i in range(4):
                    handler.emit(logging.LogRecord('test', logging.INFO, '', 0, 'synthetic progress %d', (i,), None))
            finally:
                handler.close()
            logs = list(Path(directory).glob('collect.log*'))
            self.assertGreater(len(logs), 1)
            for log in logs:
                self.assertEqual(log.stat().st_mode & 0o777, 0o600)
