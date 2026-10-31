"""Optional low-priority busywork, so the process is never completely idle.

When nothing else is happening this runs a tiny slice of pointless maths (it hunts
for large probable primes with Miller-Rabin) and then sleeps. It pauses whenever a
welcome image is being drawn, so real work always wins.

Honest limits, because they matter on a 0.1 CPU host:
- It does NOT stop Render from sleeping. Render only counts incoming HTTP requests.
  That is what the UptimeRobot monitor and the other host's heartbeat ping are for.
- It uses real CPU time. The default is 1% of one core, capped at 5%.
- Turn it off with KEEP_BUSY=false.
"""
import asyncio
import contextlib
import logging
import random
import time

import config

log = logging.getLogger(__name__)

SLICE_SECONDS = 0.01        # work in 10 ms slices so the event loop is never blocked for long
MAX_PERCENT = 5.0
_BASES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)   # deterministic below ~3.3e24

_active_jobs = 0


@contextlib.contextmanager
def busy():
    """Wrap real work in this so the busywork steps aside while it runs."""
    global _active_jobs
    _active_jobs += 1
    try:
        yield
    finally:
        _active_jobs -= 1


def _is_probable_prime(n: int) -> bool:
    if n < 2:
        return False
    for p in _BASES:
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for a in _BASES:
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


async def run(stop: asyncio.Event) -> None:
    if not config.KEEP_BUSY:
        return
    percent = min(max(config.KEEP_BUSY_PERCENT, 0.1), MAX_PERCENT)
    pause = SLICE_SECONDS * (100 - percent) / percent
    candidate = random.getrandbits(80) | 1
    found = 0
    log.info("Idle busywork on (about %.1f%% of one core, pauses during real work)", percent)
    while not stop.is_set():
        wait = pause
        if _active_jobs == 0:
            deadline = time.perf_counter() + SLICE_SECONDS
            while time.perf_counter() < deadline:
                candidate += 2
                if _is_probable_prime(candidate):
                    found += 1
        else:
            wait = 1.0
        try:
            await asyncio.wait_for(stop.wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass
    log.debug("Idle busywork stopped after finding %d probable primes", found)
