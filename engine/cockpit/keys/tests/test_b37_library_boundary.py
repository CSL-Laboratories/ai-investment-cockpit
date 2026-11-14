"""B37 at the keyring library's boundary: what the doctor's two paths make the library itself do (CSL-59).

Codex's third read of #330 (C1, C2): the dict keychains of the other doctor tests replace a class's probe and
password methods, so they cannot see what the library's own code does under Cockpit's calls. Here each test
keeps the genuine class's own availability probe (``priority``) and its own password methods, and replaces
only the transport under them: secretstorage's D-Bus connection and collection calls for Secret Service, the
``dbus`` module for KWallet, the ``Secret`` namespace for libsecret. Lucas, Nov 13, 2026, told what these paths
really do, chose "State what is true (Recommended)"; these tests hold that statement:

* the plain doctor reads no keychain item and calls no password method; detection's probe asks the keychain
  service whether it is there (Secret Service and KWallet ask D-Bus, libsecret opens a session);
* ``doctor --check-keychain`` asks for Cockpit's marker and nothing else, through the library's ordinary
  read, which can show the unlock prompt, moves old-format KWallet entries, and creates Secret Service's
  default collection when there is none.

Detection is run through ``loader`` here, so the library's own discovery (``keyring.get_keyring()``, which also
reads every installed backend's priority) is not. Written against keyring 25.7.0 and SecretStorage 3.5.0; a
test is skipped where its module is missing. Fake transport and made-up values: nothing reaches a real
keychain, D-Bus or key.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import os
import types
import unittest
from typing import Any
from unittest import mock

from cockpit.keys import __main__ as cli
from cockpit.keys import detect
from cockpit.keys.tests.fakes import FAKE, KEYRING, MARKER, RECORDS, key_file, loader, no_record_note


def _module(name: str) -> Any:
    if not KEYRING:
        return None
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


SS = _module("keyring.backends.SecretService")
KW = _module("keyring.backends.kwallet")
LS = _module("keyring.backends.libsecret")
HAS_SECRETSTORAGE = SS is not None and hasattr(SS, "secretstorage")
requires_secretstorage = unittest.skipUnless(HAS_SECRETSTORAGE, "needs keyring and SecretStorage")
requires_kwallet = unittest.skipUnless(KW is not None, "needs keyring")
requires_libsecret = unittest.skipUnless(LS is not None, "needs keyring")
requires_records = unittest.skipUnless(RECORDS, "the opt-in check reads only where a record is kept (not Windows)")
MARKER_QUERY = {"service": MARKER[0], "username": MARKER[1]}  # keyring's default scheme for the marker
LEGACY = "made-up-legacy-value-not-a-key"


def plain(name: str) -> str:
    return f"keys: ok - OS keychain ({name}); model-provider, broker and venue keys in the keychain"


@contextlib.contextmanager
def secret_service_wire(*, default_exists: bool = True, locked: bool = False):  # type: ignore[no-untyped-def]
    """secretstorage's transport under keyring's own Secret Service class; every call is recorded."""

    calls: list[tuple] = []
    storage = SS.secretstorage
    collection_mod = importlib.import_module("secretstorage.collection")
    missing = importlib.import_module("secretstorage.exceptions").ItemNotFoundException

    class Connection:
        def close(self) -> None:
            calls.append(("close",))

    class Collection:
        def __init__(self, connection: Connection) -> None:
            self.connection = connection

        def is_locked(self) -> bool:
            calls.append(("is_locked",))
            return locked

        def unlock(self) -> None:
            calls.append(("unlock",))  # where the real one shows the prompt; this one stays locked

        def search_items(self, query: dict) -> Any:
            calls.append(("search_items", dict(query)))
            return iter(())

        def create_item(self, *args: object, **kwargs: object) -> None:
            calls.append(("create_item",))

    def dbus_init() -> Connection:
        calls.append(("dbus_init",))
        return Connection()

    def check_service_availability(connection: Connection) -> bool:
        calls.append(("check_service_availability",))
        return True

    def open_default(connection: Connection, path: object = None, session: object = None) -> Collection:
        if not default_exists:
            calls.append(("no_default_collection",))
            raise missing("made-up: no default collection")
        calls.append(("open_default_collection",))
        return Collection(connection)

    def create_collection(connection: Connection, label: str, alias: str = "", session: object = None) -> Collection:
        calls.append(("create_collection", label, alias))
        return Collection(connection)

    with mock.patch.object(storage, "dbus_init", dbus_init), \
            mock.patch.object(storage, "check_service_availability", check_service_availability), \
            mock.patch.object(collection_mod, "Collection", open_default), \
            mock.patch.object(collection_mod, "create_collection", create_collection):
        yield calls


@contextlib.contextmanager
def kwallet_wire(legacy: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    """The ``dbus`` module under keyring's own KWallet 5 class; ``legacy`` is the wallet's old "Python" folder."""

    calls: list[tuple] = []
    folder = dict(legacy or {})

    class Iface:
        def networkWallet(self) -> str:  # noqa: N802 - the D-Bus method's name
            return "made-up-wallet"

        def open(self, wallet: str, wid: int, appid: str) -> int:
            calls.append(("open",))
            return 7

        def isOpen(self, handle: int) -> bool:  # noqa: N802
            return True

        def hasFolder(self, handle: int, name: str, appid: str) -> bool:  # noqa: N802
            calls.append(("hasFolder", name))
            return name == "Python" and bool(legacy)

        def readPasswordList(self, handle: int, name: str, pattern: str, appid: str) -> dict:  # noqa: N802
            calls.append(("readPasswordList", name, pattern))
            return dict(folder) if name == "Python" else {}

        def writePassword(self, handle: int, name: str, key: str, value: str, appid: str) -> int:  # noqa: N802
            calls.append(("writePassword", name, key))
            return 0

        def removeEntry(self, handle: int, name: str, key: str, appid: str) -> int:  # noqa: N802
            calls.append(("removeEntry", name, key))
            folder.pop(key, None)
            return 0

        def removeFolder(self, handle: int, name: str, appid: str) -> bool:  # noqa: N802
            calls.append(("removeFolder", name))
            return True

        def hasEntry(self, handle: int, name: str, key: str, appid: str) -> bool:  # noqa: N802
            calls.append(("hasEntry", name, key))
            return False

        def readPassword(self, handle: int, name: str, key: str, appid: str) -> str:  # noqa: N802
            calls.append(("readPassword", name, key))
            return ""

    iface = Iface()

    class Bus:
        def name_has_owner(self, name: str) -> bool:
            calls.append(("name_has_owner", name))
            return True

        def list_activatable_names(self) -> list[str]:
            calls.append(("list_activatable_names",))
            return ["org.kde.kwalletd5"]

        def get_object(self, name: str, path: str) -> object:
            calls.append(("get_object", name))
            return object()

    def session_bus(mainloop: object = None) -> Bus:
        calls.append(("SessionBus",))
        return Bus()

    fake = types.SimpleNamespace(SessionBus=session_bus, Interface=lambda obj, name: iface,
                                 DBusException=type("DBusException", (Exception,), {}))
    with mock.patch.object(KW, "dbus", fake, create=True), \
            mock.patch.object(KW, "DBusGMainLoop", lambda: None, create=True):
        yield calls


@contextlib.contextmanager
def libsecret_wire():  # type: ignore[no-untyped-def]
    """The ``Secret`` namespace under keyring's own libsecret class."""

    calls: list[tuple] = []

    def get_sync(flags: object, cancellable: object) -> object:
        calls.append(("Service.get_sync", flags))
        return object()

    def password_search_sync(schema: object, attributes: dict, flags: object, cancellable: object) -> list:
        calls.append(("password_search_sync", dict(attributes), flags))
        return []

    secret = types.SimpleNamespace(
        Service=types.SimpleNamespace(get_sync=get_sync), ServiceFlags=types.SimpleNamespace(OPEN_SESSION="OPEN_SESSION"),
        Schema=types.SimpleNamespace(new=lambda *args: "schema"), SchemaFlags=types.SimpleNamespace(NONE="NONE"),
        SchemaAttributeType=types.SimpleNamespace(STRING="STRING"), SearchFlags=types.SimpleNamespace(UNLOCK="UNLOCK"),
        password_search_sync=password_search_sync, COLLECTION_DEFAULT="default")
    glib = types.SimpleNamespace(Error=type("Error", (Exception,), {}))
    with mock.patch.object(LS, "available", True), mock.patch.object(LS, "Secret", secret, create=True), \
            mock.patch.object(LS, "GLib", glib, create=True):
        yield calls


class _Doctor(unittest.TestCase):
    def setUp(self) -> None:
        self.path = key_file(self)
        self.root = os.path.dirname(self.path)

    def plain_doctor(self, backend: Any) -> tuple[int, str]:
        out = io.StringIO()

        def no_store(path: str) -> None:
            raise AssertionError("the plain doctor built a store")

        with mock.patch.object(cli, "detect_keychain", lambda: detect.detect_keychain(loader(backend))), \
                mock.patch.object(cli, "store_for_host", no_store), contextlib.redirect_stdout(out):
            rc = cli.main(["doctor", "--key-file", self.path])
        return rc, out.getvalue().strip()

    def checked_doctor(self, backend: Any) -> tuple[int, str]:
        # The command's own path: store_for_host detects (the class's own probe) and pins the backend.
        out = io.StringIO()
        with mock.patch.object(cli, "store_for_host", lambda path: detect.store_for_host(path, loader(backend))), \
                contextlib.redirect_stdout(out):
            rc = cli.main(["doctor", "--key-file", self.path, "--check-keychain"])
        return rc, out.getvalue().strip()


class LibraryBoundaryTests(_Doctor):
    @requires_secretstorage
    def test_the_plain_doctor_on_secret_service_asks_d_bus_and_reads_no_item(self) -> None:
        name = "SecretService Keyring"
        with secret_service_wire() as calls:
            self.assertEqual(self.plain_doctor(SS.Keyring()), (0, plain(name) + no_record_note(name)))
        # The probe only: a D-Bus connection, asked whether the service is there, then closed. No collection
        # is opened or searched, so no item is read.
        self.assertEqual(calls, [("dbus_init",), ("check_service_availability",), ("close",)])

    @requires_kwallet
    def test_the_plain_doctor_on_kwallet_asks_d_bus_and_opens_no_wallet(self) -> None:
        name = "kwallet DBusKeyring"
        with kwallet_wire({"broker:acct@csl-cockpit": LEGACY}) as calls:
            self.assertEqual(self.plain_doctor(KW.DBusKeyring()), (0, plain(name) + no_record_note(name)))
        self.assertEqual(calls, [("SessionBus",), ("name_has_owner", "org.kde.kwalletd5"), ("list_activatable_names",)])

    @requires_libsecret
    def test_the_plain_doctor_on_libsecret_opens_a_session_and_searches_nothing(self) -> None:
        name = "libsecret Keyring"
        with libsecret_wire() as calls:
            self.assertEqual(self.plain_doctor(LS.Keyring()), (0, plain(name) + no_record_note(name)))
        self.assertEqual(calls, [("Service.get_sync", "OPEN_SESSION")])  # a session with the service; no search

    @requires_records
    @requires_secretstorage
    def test_the_check_on_secret_service_creates_a_missing_default_collection_and_asks_for_the_marker_only(self) -> None:
        name = "SecretService Keyring"
        with secret_service_wire(default_exists=False) as calls:
            rc, line = self.checked_doctor(SS.Keyring())
        self.assertEqual((rc, line), (0, f"{plain(name)}; no keychain record yet, and no Cockpit marker in the keychain: "
                                         "the next broker or venue save records it"))
        self.assertEqual(calls, [("dbus_init",), ("check_service_availability",), ("close",),  # detection's probe
                                 ("dbus_init",), ("no_default_collection",),
                                 ("create_collection", "Default", "default"),  # the library's own get_default_collection
                                 ("is_locked",), ("search_items", MARKER_QUERY), ("close",)])
        self.assertEqual(os.listdir(self.root), [])  # Cockpit wrote nothing

    @requires_records
    @requires_secretstorage
    def test_the_check_on_a_locked_secret_service_asks_to_unlock_and_shows_the_type_only(self) -> None:
        name = "SecretService Keyring"
        with secret_service_wire(locked=True) as calls:
            rc, line = self.checked_doctor(SS.Keyring())
        self.assertEqual(rc, 0)  # warn: nothing found wrong, the keychain could not be checked
        self.assertTrue(line.startswith(f"keys: warn - OS keychain ({name})"), line)
        self.assertTrue(line.endswith("; keychain check did not finish (KeyringLocked)"), line)
        self.assertIn(("unlock",), calls)  # where the real collection shows its unlock prompt
        self.assertNotIn("search_items", [call[0] for call in calls])  # dismissed: nothing was searched

    @requires_records
    @requires_kwallet
    def test_the_check_on_kwallet_moves_old_format_entries_and_asks_for_the_marker_only(self) -> None:
        name = "kwallet DBusKeyring"
        with kwallet_wire({"broker:acct@csl-cockpit": LEGACY}) as calls:
            rc, line = self.checked_doctor(KW.DBusKeyring())
        self.assertEqual((rc, line), (0, f"{plain(name)}; no keychain record yet, and no Cockpit marker in the keychain: "
                                         "the next broker or venue save records it"))
        probe = [("SessionBus",), ("name_has_owner", "org.kde.kwalletd5"), ("list_activatable_names",)]
        self.assertEqual(calls, probe + [
            ("SessionBus",), ("get_object", "org.kde.kwalletd5"), ("open",),
            # the library's own migration, run by the read that opened the wallet: an old-format entry is
            # read, written under its service and removed from the old folder, which is then removed
            ("hasFolder", "Python"), ("readPasswordList", "Python", "*@*"), ("writePassword", "csl-cockpit", "broker:acct"),
            ("removeEntry", "Python", "broker:acct@csl-cockpit"), ("readPasswordList", "Python", "*"),
            ("removeFolder", "Python"),
            ("hasEntry", MARKER[0], MARKER[1]),  # Cockpit's request: the marker, nothing else
        ])
        self.assertNotIn(LEGACY, line)
        self.assertNotIn(FAKE, line)

    @requires_records
    @requires_libsecret
    def test_the_check_on_libsecret_searches_for_the_marker_only_and_may_unlock(self) -> None:
        name = "libsecret Keyring"
        with libsecret_wire() as calls:
            rc, line = self.checked_doctor(LS.Keyring())
        self.assertEqual((rc, line), (0, f"{plain(name)}; no keychain record yet, and no Cockpit marker in the keychain: "
                                         "the next broker or venue save records it"))
        self.assertEqual(calls, [("Service.get_sync", "OPEN_SESSION"),
                                 ("password_search_sync", {**MARKER_QUERY, "application": "Python keyring library"},
                                  "UNLOCK")])  # the search may unlock, which can prompt

    @requires_records
    @requires_secretstorage
    def test_the_check_reads_nothing_when_the_record_answers(self) -> None:
        name = "SecretService Keyring"
        fd = os.open(os.path.join(self.root, ".keychain.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write('{"version": 1, "keychain": "SecretService Keyring"}')
        with secret_service_wire() as calls:
            self.assertEqual(self.checked_doctor(SS.Keyring()), (0, plain(name)))
        self.assertEqual(calls, [("dbus_init",), ("check_service_availability",), ("close",)])  # the probe only


if __name__ == "__main__":
    unittest.main()
