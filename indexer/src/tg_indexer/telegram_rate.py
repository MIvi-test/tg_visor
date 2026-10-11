"""Rate limiting for pinned Telethon 1.40 RPC dispatch, including retries."""
import asyncio
import math
import time

from .observability import LOGGER


class RateLimiter:
    def __init__(self, requests_per_second=2, *, clock=time.monotonic, sleep=asyncio.sleep):
        if isinstance(requests_per_second, bool) or not isinstance(requests_per_second, (int, float)) or not math.isfinite(requests_per_second) or not 0.1 <= requests_per_second <= 20:
            raise ValueError("requests_per_second must be a finite number in 0.1..20")
        self.interval = 1 / requests_per_second
        self.clock = clock
        self.sleep = sleep
        self.next_allowed = 0.0
        self.lock = asyncio.Lock()
        self.requests = 0

    async def acquire(self):
        async with self.lock:
            delay = self.next_allowed - self.clock()
            if delay > 0:
                await self.sleep(delay)
            self.next_allowed = self.clock() + self.interval
            self.requests += 1


class PacedSender:
    def __init__(self, sender, limiter):
        self.sender = sender
        self.limiter = limiter

    def send(self, request, ordered=False):
        from telethon import utils

        async def send_one(item):
            await self.limiter.acquire()
            return await self.sender.send(item, ordered=ordered)

        if utils.is_list_like(request):
            return [asyncio.create_task(send_one(item)) for item in request]
        return asyncio.create_task(send_one(request))


async def invoke_with_limits(call, sender, request, limiter, ordered=False):
    from telethon import errors
    while True:
        try:
            return await call(PacedSender(sender, limiter), request, ordered=ordered, flood_sleep_threshold=0)
        except (errors.FloodWaitError, errors.FloodPremiumWaitError, errors.FloodTestPhoneWaitError, errors.SlowModeWaitError) as error:
            seconds = max(1, error.seconds)
            LOGGER.warning("Telegram FloodWait: пауза %d с, затем повтор запроса", seconds)
            await limiter.sleep(seconds)


def client_class():
    from telethon import TelegramClient

    class LimitedTelegramClient(TelegramClient):
        def __init__(self, *args, limiter=None, **kwargs):
            # Telethon constructs CDN clients via self.__class__ without custom kwargs.
            self.rate_limiter = limiter if limiter is not None else RateLimiter()
            super().__init__(*args, **kwargs)

        async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
            return await invoke_with_limits(super()._call, sender, request, self.rate_limiter, ordered)

        async def _get_cdn_client(self, cdn_redirect):
            client = await super()._get_cdn_client(cdn_redirect)
            client.rate_limiter = self.rate_limiter
            return client

    return LimitedTelegramClient
