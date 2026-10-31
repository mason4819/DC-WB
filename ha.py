"""Two-host failover.

Both hosts run the full bot, but only the host that owns the lease in the shared
Turso database connects to Discord. The other host stays on standby, serves image
requests for the active one, and takes over automatically when the lease expires.

Safety rules that prevent two hosts from answering at the same time:
- The active host renews the lease every CHECK_INTERVAL seconds (LEASE_TTL = 45 s).
- If it cannot confirm the lease for FENCE_AFTER seconds (30 s), it disconnects from
  Discord by itself, which is always before the other host is allowed to take over.
- On a normal shutdown (Render redeploy, SIGTERM) the lease is released at once,
  so the other host takes over in about CHECK_INTERVAL seconds instead of 45.
Members who join during a hard failure (up to about a minute) are not welcomed.
"""
import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

import config
import database
from utils import remote

log = logging.getLogger(__name__)

active_supervisor: Optional["Supervisor"] = None   # set by bot.py so /後臺 can ask for a handover

LEASE_TTL = 45
CHECK_INTERVAL = 10
FENCE_AFTER = 30
CRASH_COOLDOWN = 30
HEARTBEAT_SECONDS = 240      # well under Render's 15 minute idle limit


class Supervisor:
    """Starts and stops the Discord bot depending on who owns the lease."""

    def __init__(self, holder: str, make_bot: Callable[[], object],
                 acquire: Callable[[str, int], Awaitable[bool]],
                 release: Callable[[str], Awaitable[None]], *, fatal: tuple = (),
                 on_activate: Optional[Callable[[], None]] = None,
                 ttl: float = LEASE_TTL, interval: float = CHECK_INTERVAL, fence: float = FENCE_AFTER,
                 crash_cooldown: float = CRASH_COOLDOWN, call_timeout: float = 12.0) -> None:
        self.holder = holder
        self.role = "standby"
        self.fatal_error: Optional[BaseException] = None
        self._make_bot, self._acquire, self._release = make_bot, acquire, release
        self._fatal = fatal
        self._on_activate = on_activate
        self._ttl, self._interval, self._fence = ttl, interval, fence
        self._crash_cooldown, self._call_timeout = crash_cooldown, call_timeout
        self._wake = asyncio.Event()
        self._handover_for = 0.0
        self._bot: Optional[object] = None
        self._task: Optional[asyncio.Task] = None

    async def _check(self) -> Optional[bool]:
        """True = we own the lease, False = someone else does, None = could not find out."""
        try:
            return await asyncio.wait_for(self._acquire(self.holder, self._ttl), self._call_timeout)
        except (database.DatabaseError, asyncio.TimeoutError) as exc:
            log.warning("Could not check the failover lease: %s", exc or type(exc).__name__)
            return None

    def _start_bot(self) -> None:
        if self._on_activate is not None:
            self._on_activate()  # e.g. drop caches that may be stale after a stint on standby
        self._bot = self._make_bot()
        self._task = asyncio.create_task(self._bot.run_until_closed())
        self.role = "leader"
        log.info("This host is now ACTIVE and connects to Discord (%s)", self.holder)

    async def _stop_bot(self, reason: str) -> None:
        bot, task = self._bot, self._task
        self._bot = self._task = None
        if bot is None:
            return
        self.role = "standby"
        log.warning("Disconnecting from Discord: %s", reason)
        try:
            await asyncio.wait_for(bot.close(), 20)
        except Exception:
            log.exception("Error while closing the bot")
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    def request_handover(self, seconds: float) -> None:
        """Step down now and stay on standby for `seconds`, so the other host takes over."""
        self._handover_for = max(float(seconds), self._interval * 2)
        self._wake.set()

    async def _sleep(self, stop: asyncio.Event) -> None:
        waiters = [asyncio.ensure_future(self._wake.wait()), asyncio.ensure_future(stop.wait())]
        try:
            await asyncio.wait(waiters, timeout=self._interval, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
        self._wake.clear()

    async def run(self, stop: asyncio.Event) -> None:
        last_confirmed = time.monotonic()
        cooldown_until = 0.0
        try:
            while not stop.is_set():
                if self._handover_for:
                    seconds, self._handover_for = self._handover_for, 0.0
                    await self._stop_bot("a handover to the other host was requested")
                    await self._release_quietly()
                    cooldown_until = time.monotonic() + seconds
                    await self._sleep(stop)
                    continue
                if self._task is None and time.monotonic() < cooldown_until:
                    await self._sleep(stop)   # cooling down: do not renew the lease, let the other host take it
                    continue

                held = await self._check()
                now = time.monotonic()
                if held:
                    last_confirmed = now

                if self._task is not None:
                    if self._task.done():
                        error = None if self._task.cancelled() else self._task.exception()
                        await self._stop_bot("the bot stopped on its own")
                        if error is not None and isinstance(error, self._fatal):
                            self.fatal_error = error
                            return
                        log.error("The bot stopped unexpectedly: %r. Handing over to the other host.", error)
                        await self._release_quietly()
                        cooldown_until = now + self._crash_cooldown
                    elif held is False:
                        await self._stop_bot("the other host owns the lease now")
                    elif held is None and now - last_confirmed > self._fence:
                        await self._stop_bot("could not confirm the lease in time (safety stop)")
                elif held and now >= cooldown_until:
                    self._start_bot()

                await self._sleep(stop)
        finally:
            await self._stop_bot("shutting down")
            await self._release_quietly()

    async def _release_quietly(self) -> None:
        try:
            await asyncio.wait_for(self._release(self.holder), 5)
        except (database.DatabaseError, asyncio.TimeoutError):
            log.warning("Could not release the lease; it will expire on its own")


async def heartbeat(stop: asyncio.Event) -> None:
    """Ping the other host regularly. Keeps a sleeping Render service awake and logs outages."""
    if not config.PEER_URL:
        return
    was_up: Optional[bool] = None
    while not stop.is_set():
        is_up = await remote.ping_peer()
        if is_up != was_up:
            if is_up:
                log.info("The other host (%s) is reachable", config.PEER_URL)
            else:
                log.warning("The other host (%s) is not reachable", config.PEER_URL)
            was_up = is_up
        try:
            await asyncio.wait_for(stop.wait(), HEARTBEAT_SECONDS)
        except asyncio.TimeoutError:
            pass
