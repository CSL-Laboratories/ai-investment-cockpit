"""A TEST-ONLY signer and verifier: HMAC-SHA256 under a made-up key.

It stands in for the installer key's real scheme, which is not decided (lane questions).
It is never a production verifier: HMAC is symmetric, so whoever can verify can also sign.
"""

import hashlib
import hmac

from cockpit.limits import signing_input

KEY = b"made-up installer key; tests only"
OTHER_KEY = b"another made-up key; tests only"


def key_id(key: bytes = KEY) -> str:
    return "test-hmac:" + hashlib.sha256(key).hexdigest()[:16]


class FakeVerifier:
    def __init__(self, key: bytes = KEY, kid: str | None = None):
        self._key = key
        self.key_id = kid if kid is not None else key_id(key)

    def verify(self, message: bytes, signature: bytes) -> bool:
        return hmac.compare_digest(hmac.new(self._key, message, hashlib.sha256).digest(), signature)


def sign(document: dict, key: bytes = KEY) -> str:
    return hmac.new(key, signing_input(document), hashlib.sha256).hexdigest()
