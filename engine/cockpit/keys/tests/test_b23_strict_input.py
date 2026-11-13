"""B23: a key name ending in a line break, and a key file whose version is ``true``, are refused (CSL-59).

R6 finding 10(d), left open on #319 and #323: the name rule was ``^[a-z0-9][a-z0-9._-]{0,63}$`` used with
``re.match``, and ``$`` also matches just before a final line break, so ``"openai\\n"`` passed; the key file
check was ``data.get("version") != 1``, and ``True == 1`` (and ``1.0 == 1``), so ``"version": true``
passed. Lucas, Nov 13, 2026, asked "Fix them before the key store reaches main?": "Fix before landing
(Recommended)" (decision sheet B23, relayed by the Liaison). The first two tests are R6's own
(``test_r6_finding_10_keys.py``), with the refusal's cause and what was left behind checked as well.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/keys/tests -t engine -v``.
Temp folders and made-up keys only; no real keyring, provider, venue or network.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from cockpit.keys import KEY_KINDS, KeyRefused, KeyStore
from cockpit.keys.tests.fakes import FAKE

POSIX = os.name == "posix" and hasattr(os, "O_NOFOLLOW")
# Names that differ from a valid one only by a line break somewhere; ``$`` let the first one through.
BROKEN_NAMES = ("openai\n", "openai\r\n", "openai\n\n", "\nopenai", "open\nai", "openai\r")


class _Keyring:
    """A keychain stand-in that records every call; it is never asked for anything in these refusals."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def get(self, service: str, name: str) -> str | None:
        self.calls.append(("get", service, name))
        return None

    def set(self, service: str, name: str, secret: str) -> None:
        self.calls.append(("set", service, name))

    def delete(self, service: str, name: str) -> None:
        self.calls.append(("delete", service, name))


def _refused(test: unittest.TestCase, cause: str, fn, *args) -> KeyRefused:  # type: ignore[no-untyped-def]
    with test.assertRaises(KeyRefused) as ctx:
        fn(*args)
    test.assertEqual(ctx.exception.cause, cause)
    test.assertNotIn(FAKE, str(ctx.exception))
    return ctx.exception


class KeyNameTests(unittest.TestCase):
    def test_every_operation_refuses_a_name_with_a_line_break_and_asks_the_keychain_nothing(self) -> None:
        ring = _Keyring()
        store = KeyStore(keyring=ring)
        for name in BROKEN_NAMES:
            for kind in KEY_KINDS:
                with self.subTest(name=name, kind=kind):
                    _refused(self, "key_name_malformed", store.put, kind, name, FAKE)
                    _refused(self, "key_name_malformed", store.get, kind, name)
                    _refused(self, "key_name_malformed", store.delete, kind, name)
        self.assertEqual(ring.calls, [])

    def test_the_valid_names_are_unchanged(self) -> None:
        ring = _Keyring()
        store = KeyStore(keyring=ring)
        for name in ("a", "0", "openai", "a.b_c-d", "x" * 64):
            with self.subTest(name=name):
                self.assertEqual(store.put("broker", name, FAKE), "keyring")
        for name in ("", "x" * 65, "Openai", "-openai", ".openai", "open ai", "openai/x", "openai\x00"):
            with self.subTest(name=name):
                _refused(self, "key_name_malformed", store.put, "broker", name, FAKE)
        self.assertEqual(len(ring.calls), 5)


@unittest.skipUnless(POSIX, "owner-only key file requires POSIX")
class KeyFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="cockpit-b23-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "model-keys.json")
        self.store = KeyStore(keyring=None, key_file=self.path)

    def write(self, payload: object) -> bytes:
        body = json.dumps(payload).encode("utf-8")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        return body

    def read(self) -> bytes:
        with open(self.path, "rb") as fh:
            return fh.read()

    def test_newline_key_name_is_refused(self) -> None:  # R6's test
        _refused(self, "key_name_malformed", self.store.put, "model_provider", "openai\n", FAKE)
        self.assertEqual(os.listdir(self.dir), [])  # nothing written, not even the lock

    def test_boolean_file_version_is_refused(self) -> None:  # R6's test
        self.store.put("model_provider", "openai", FAKE)
        data = json.loads(self.read())
        data["version"] = True
        body = self.write(data)
        _refused(self, "key_file_malformed", self.store.get, "model_provider", "openai")
        _refused(self, "key_file_malformed", self.store.put, "model_provider", "other", FAKE)
        _refused(self, "key_file_malformed", self.store.delete, "model_provider", "openai")
        self.assertEqual(self.read(), body)  # never rewritten or repaired

    def test_a_version_equal_to_one_but_not_the_integer_one_is_refused(self) -> None:
        for version in (True, 1.0, "1", 2, 0, None, [1], {"v": 1}):
            with self.subTest(version=version):
                self.write({"version": version, "kind": "model_provider", "keys": {"openai": FAKE}})
                _refused(self, "key_file_malformed", self.store.get, "model_provider", "openai")
        self.write({"version": 1, "kind": "model_provider", "keys": {"openai": FAKE}})
        self.assertEqual(self.store.get("model_provider", "openai"), FAKE)

    def test_a_stored_name_with_a_line_break_makes_the_file_malformed(self) -> None:
        for name in BROKEN_NAMES:
            with self.subTest(name=name):
                body = self.write({"version": 1, "kind": "model_provider", "keys": {"openai": FAKE, name: FAKE}})
                _refused(self, "key_file_malformed", self.store.get, "model_provider", "openai")
                _refused(self, "key_file_malformed", self.store.put, "model_provider", "other", FAKE)
                self.assertEqual(self.read(), body)


if __name__ == "__main__":
    unittest.main()
