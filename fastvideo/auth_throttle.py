"""Slows down guessing of stream keys on the public WHIP receiver.

After LIMIT wrong keys from one address within WINDOW seconds, that address gets 429 for BLOCK
seconds. A correct key clears the address. Loopback is never throttled (local tools, and requests the
Bridge forwards itself, which are throttled where the real address is known).
"""
import ipaddress
import time

from aiohttp import web

LIMIT, WINDOW, BLOCK, MAX_ADDRESSES = 10, 60.0, 60.0, 1000


class AuthThrottle:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.failures = {}      # address -> [timestamps within WINDOW]
        self.blocked = {}       # address -> unblock time

    @staticmethod
    def exempt(address):
        try:
            return not address or ipaddress.ip_address(address).is_loopback
        except ValueError:
            return False

    def check(self, address):
        if self.exempt(address):
            return
        until = self.blocked.get(address)
        if until is None:
            return
        if self.clock() < until:
            raise web.HTTPTooManyRequests(text='Too many wrong stream keys. Try again in a minute.',
                                          headers={'Retry-After': str(int(BLOCK))})
        del self.blocked[address]

    def failed(self, address):
        if self.exempt(address):
            return
        now = self.clock()
        recent = [t for t in self.failures.get(address, []) if now - t < WINDOW] + [now]
        self.failures[address] = recent
        if len(recent) >= LIMIT:
            self.blocked[address] = now + BLOCK
            self.failures.pop(address, None)
        if len(self.failures) > MAX_ADDRESSES:  # bounded memory under a spread-out scan
            oldest = min(self.failures, key=lambda key: self.failures[key][-1])
            self.failures.pop(oldest, None)
        if len(self.blocked) > MAX_ADDRESSES:
            self.blocked = {key: until for key, until in self.blocked.items() if until > now}

    def succeeded(self, address):
        self.failures.pop(address, None)
