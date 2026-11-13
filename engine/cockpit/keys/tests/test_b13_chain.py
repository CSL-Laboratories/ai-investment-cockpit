"""B13: a keyring chain that holds an OS keychain and other backends (CSL-59).

Recommendation B13, accepted by Lucas on Nov 12, 2026 ("I agree with all B10-B14 Recommendations"):
"use the OS backend, name the ignored one in the doctor line". #323's rule must still hold: "a plaintext
keyring is never treated as an OS keychain for broker or venue secrets". These tests try to break it.
OS keychains are admitted by class identity: only the keyring library's own classes count.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/keys/tests -t engine -v``.
Fakes only: genuine library classes with their probe and password methods replaced for the test, and
plain look-alikes. No real keychain, key, provider, venue or network.
"""

from __future__ import annotations

import contextlib
import io
import itertools
import unittest
from unittest import mock

from cockpit.keys import KEY_KINDS, KeyRefused, doctor, doctor_line, store_for_host
from cockpit.keys import detect as detect_module
from cockpit.keys.tests.fakes import (
    FAKE, NAMES, Keychains, forget_class, genuine_class, genuine_class_chainer, key_file, lookalike_chain, loader,
    MARKER_CALLS, MARKER_ITEMS, UNRECORDED_KINDS, recorder, requires_keyring, untouchable, with_marker,
)

PLAIN = ("keyrings.alt.file", "PlaintextKeyring")
HELPER = ("review_standin.pip_credential_helper", "CredentialHelper")


class _LyingStr(str):
    """A module name that claims to equal every string, with the hash of an allowlisted one."""

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    def __hash__(self) -> int:
        return hash("keyring.backends.SecretService")


IMITATIONS: tuple[object, ...] = (
    "keyring.backends.SecretService",  # the exact name: still not the library's class
    "keyring.backends.SecretServiceEvil", "keyring.backends.SecretService.evil",
    "keyring.backends.secretservice", "Keyring.backends.SecretService", "keyrings.backends.SecretService",
    " keyring.backends.Windows", "keyring.backends.Windows ", "keyring.backends.Windows\n",
    "keyring.backends.macOS_", "keyring.backends.macOS.evil", "keyring.backends.kwallet.file",
    "keyring.backends.libsecret2", "keyring.backends", "keyring", "",
    _LyingStr("keyrings.alt.file"), 5,
)


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self.k = Keychains(self)

    def assert_refused(self, store: object) -> None:
        for kind in ("broker", "venue"):
            with self.assertRaises(KeyRefused) as ctx:
                store.put(kind, "fixture", FAKE)  # type: ignore[attr-defined]
            self.assertEqual(ctx.exception.cause, "keychain_required")

    def round_trip_every_kind(self, store: object) -> None:
        for kind in KEY_KINDS:
            self.assertEqual(store.put(kind, "fixture", FAKE), "keyring")  # type: ignore[attr-defined]
            self.assertEqual(store.get(kind, "fixture"), FAKE)  # type: ignore[attr-defined]
            store.delete(kind, "fixture")  # type: ignore[attr-defined]
            self.assertIsNone(store.get(kind, "fixture"))  # type: ignore[attr-defined]


@requires_keyring
class MixedChainTests(_Case):
    def test_every_order_of_a_mixed_chain_uses_only_the_os_keychain(self) -> None:
        for order in itertools.permutations(range(3)):
            with self.subTest(order=order):
                ss = self.k.os()
                plain, plain_touched = untouchable(*PLAIN)
                helper, helper_touched = untouchable(*HELPER)
                members = [(ss, None), (plain, ".".join(PLAIN)), (helper, ".".join(HELPER))]
                links = [members[i] for i in order]
                ch = self.k.chain([obj for obj, _ in links])
                store, status = store_for_host(key_file(self), loader(ch))
                self.assertTrue(status.available)
                self.assertEqual(status.backend, "SecretService Keyring")
                self.assertEqual(status.ignored, tuple(name for _, name in links if name))
                self.round_trip_every_kind(store)
                self.assertEqual(len(ss.calls), 4 * len(KEY_KINDS) + MARKER_CALLS)
                self.assertEqual(ss.data, with_marker({}, "SecretService Keyring"))
                self.assertEqual((plain_touched, helper_touched, ch.calls), ([], [], []))

    def test_file_backend_first_in_the_chain_is_never_used(self) -> None:
        # A working plaintext store, first in the chain and with a higher priority than the keychain.
        for priority in (0.5, 100):
            with self.subTest(priority=priority):
                plain = recorder(priority=priority)
                ss = self.k.os()
                store, status = store_for_host(key_file(self), loader(self.k.chain([plain, ss])))
                self.assertTrue(status.available)
                self.assertEqual(status.ignored, ("keyrings.alt.file.PlaintextKeyring",))
                for kind in ("broker", "venue", "model_provider"):
                    store.put(kind, "fixture", FAKE)
                    self.assertEqual(store.get(kind, "fixture"), FAKE)
                self.assertEqual((plain.calls, plain.data), ([], {}))
                self.assertEqual(ss.data, with_marker({("csl-cockpit", f"{kind}:fixture"): FAKE for kind in KEY_KINDS},
                                                      "SecretService Keyring"))

    def test_imitation_module_names_are_never_an_os_keychain(self) -> None:
        for module in IMITATIONS:
            with self.subTest(module=repr(module)):
                fake, touched = untouchable(module, "Keyring")
                store, status = store_for_host(key_file(self), loader(fake))
                self.assertFalse(status.available)
                self.assertEqual(status.reason, "unsupported_keychain_class:keyring.backends.SecretService.Keyring"
                                 if module == "keyring.backends.SecretService" and type(module) is str
                                 else "no_keychain_backend")
                self.assert_refused(store)
                ss = self.k.os()
                store, status = store_for_host(key_file(self), loader(self.k.chain([fake, ss])))
                self.assertTrue(status.available)
                self.assertEqual(status.backend, "SecretService Keyring")
                self.assertEqual(len(status.ignored), 1)
                self.assertTrue(status.ignored[0].endswith(".Keyring"))
                self.round_trip_every_kind(store)
                self.assertEqual(touched, [])

    def test_forged_class_metadata_is_never_an_os_keychain(self) -> None:
        # Codex's P1: a working file store whose class claims the module, the class name and the display
        # name of a genuine keychain, with and without the library's base class, and an unknown KWallet
        # class renamed to KWallet 5's name. Alone: refused. Beside a genuine KWallet: KWallet is used.
        keyring_backend = __import__("importlib").import_module("keyring.backend").KeyringBackend
        forged = {
            "SecretService module and name": dict(module="keyring.backends.SecretService", qualname="Keyring",
                                                  name="SecretService Keyring"),
            "same, on the library's base class": dict(module="keyring.backends.SecretService", qualname="Keyring",
                                                      name="SecretService Keyring", bases=(keyring_backend,)),
            "KWallet 5's qualified name": dict(module="keyring.backends.kwallet", qualname="DBusKeyring",
                                               name="kwallet DBusKeyring"),
            "an unknown KWallet class": dict(module="keyring.backends.kwallet", qualname="FutureKWallet",
                                             name="kwallet Future"),
        }
        for label, metadata in forged.items():
            with self.subTest(label):
                fake = recorder(priority=9, **metadata)
                forget_class(self, type(fake))
                store, status = store_for_host(key_file(self), loader(fake))
                self.assertFalse(status.available)
                self.assert_refused(store)
                kwallet = self.k.os("KWallet5")
                store, status = store_for_host(key_file(self), loader(self.k.chain([fake, kwallet])))
                self.assertEqual(status.backend, "kwallet DBusKeyring")
                self.round_trip_every_kind(store)
                self.assertEqual((fake.calls, fake.data), ([], {}))
                self.assertEqual(len(kwallet.calls), 4 * len(KEY_KINDS) + MARKER_CALLS)

    def test_a_subclass_of_a_genuine_keychain_is_not_admitted(self) -> None:
        # isinstance() would admit it; identity does not. Its own methods would write to plaintext.
        written: dict = {}
        genuine = genuine_class("SecretService")

        class External(genuine):  # type: ignore[misc, valid-type]
            priority = 5

            def set_password(self, service: str, name: str, secret: str) -> None:
                written[(service, name)] = secret

        forget_class(self, External)
        store, status = store_for_host(key_file(self), loader(External()))
        self.assertFalse(status.available)
        self.assert_refused(store)
        kwallet = self.k.os("KWallet5")
        store, status = store_for_host(key_file(self), loader(self.k.chain([External(), kwallet])))
        self.assertEqual(status.backend, "kwallet DBusKeyring")
        store.put("broker", "fixture", FAKE)
        self.assertEqual(written, {})

    def test_a_refusal_says_why_when_a_backend_looks_meant_as_a_keychain(self) -> None:
        # The second Codex read: setups #323 accepted and identity now refuses must not collapse into
        # "no_keychain_backend". The reason names the class (sanitised) or the module and the exception's
        # type, never its message; a foreign platform's module failing to import is no error on a working host.
        genuine = genuine_class("SecretService")
        self.k.os()  # answers SecretService's probe and stores in memory for the genuine instances below

        class DistroKeyring(genuine):  # type: ignore[misc, valid-type]
            __module__ = "keyring.backends.SecretService"  # a distribution's subclass under the original module

        class Elsewhere(genuine):  # type: ignore[misc, valid-type]
            __module__ = "vendor.keyring_patch"

        class Hostile(genuine):  # type: ignore[misc, valid-type]
            __module__ = "evil\nkeys: ok; module"

        for cls in (DistroKeyring, Elsewhere, Hostile):
            forget_class(self, cls)
            type.__setattr__(cls, "__qualname__", cls.__name__)
        type.__setattr__(Hostile, "__qualname__", "Keyring,\x1b[2J")
        cases = {
            "distribution subclass": (DistroKeyring(), "unsupported_keychain_class:keyring.backends.SecretService.DistroKeyring"),
            "external subclass": (Elsewhere(), "unsupported_keychain_class:vendor.keyring_patch.Elsewhere"),
            "forged exact name": (recorder("keyring.backends.SecretService", "Keyring"),
                                  "unsupported_keychain_class:keyring.backends.SecretService.Keyring"),
            "hostile names, sanitised": (Hostile(), "unsupported_keychain_class:evil?keys??ok??module.Keyring???2J"),
            "plain file backend": (recorder(), "no_keychain_backend"),
        }
        for label, (backend, reason) in cases.items():
            with self.subTest(label):
                store, status = store_for_host(key_file(self), loader(backend))
                self.assertEqual((status.available, status.reason), (False, reason))
                line = doctor_line(doctor(status, None))
                self.assertIn(f"no OS keychain ({reason})", line)
                self.assertEqual(len(line.splitlines()), 1)
                self.assert_refused(store)
                store, status = store_for_host(key_file(self), loader(self.k.chain([recorder(), backend])))
                self.assertEqual((status.available, status.reason), (False, reason))  # the same inside a chain

        real_import = detect_module.importlib.import_module

        def failing(names: tuple[str, ...]) -> object:
            def import_module(name: str) -> object:
                if name in names:
                    raise ImportError("secret_fixture_message_never_shown")
                return real_import(name)
            return import_module

        # A genuine Secret Service object whose module will not import at verification: refused, and said.
        backend = self.k.os()
        with mock.patch.object(detect_module.importlib, "import_module", failing(("keyring.backends.SecretService",))):
            store, status = store_for_host(key_file(self), loader(backend))
        self.assertEqual((status.available, status.reason),
                         (False, "keychain_module_unavailable:keyring.backends.SecretService:ImportError"))
        self.assertNotIn("secret_fixture_message", doctor_line(doctor(status, None)))
        self.assert_refused(store)
        self.assertEqual(backend.calls, [])
        # Another platform's keychain modules failing to import change nothing on a working host, and are not
        # reported for a backend that does not claim them.
        foreign = ("keyring.backends.macOS", "keyring.backends.Windows", "keyring.backends.libsecret")
        with mock.patch.object(detect_module.importlib, "import_module", failing(foreign)):
            store, status = store_for_host(key_file(self), loader(self.k.chain([recorder(), self.k.os()])))
            self.assertEqual((status.available, status.reason), (True, "ok"))
            _, status = store_for_host(key_file(self), loader(recorder()))
            self.assertEqual(status.reason, "no_keychain_backend")

    def test_a_proxy_is_refused_a_shadowing_method_is_ignored_and_a_swapped_class_is_refused(self) -> None:
        # What pinning does, and only that (the second Codex read): a proxy with forged metadata whose target
        # can change after detection is refused outright; a top-level password method shadowed on the
        # selected instance is ignored; a __class__ reassigned after the check is refused at use. Helpers or
        # state of the selected object changed after the check are outside what pinning covers; see
        # test_b13_real_library for the genuine password methods.
        target = self.k.os()
        plain = recorder()

        class Proxy:
            __module__ = "keyring.backends.SecretService"
            __qualname__ = "Keyring"
            priority = 5
            name = "SecretService Keyring"

            def __init__(self) -> None:
                self.target = target

            def get_password(self, *args: str) -> str | None:
                return self.target.get_password(*args)

            def set_password(self, *args: str) -> None:
                self.target.set_password(*args)

            def delete_password(self, *args: str) -> None:
                self.target.delete_password(*args)

        proxy = Proxy()
        store, status = store_for_host(key_file(self), loader(proxy))
        self.assertFalse(status.available)
        proxy.target = plain
        self.assert_refused(store)
        self.assertEqual(plain.calls, [])
        # A genuine object passes. A top-level password method shadowed on the instance afterwards is
        # ignored: every call goes through the genuine class's own method.
        genuine = self.k.os()
        store, status = store_for_host(key_file(self), loader(genuine))
        self.assertTrue(status.available)
        genuine.set_password = plain.set_password
        genuine.get_password = plain.get_password
        genuine.delete_password = plain.delete_password
        self.round_trip_every_kind(store)
        self.assertEqual((plain.calls, plain.data), ([], {}))
        # A __class__ reassigned afterwards is refused at use, for every operation and every kind.
        store.put("venue", "fixture", FAKE)

        class Swapped(type(genuine)):  # type: ignore[misc]
            def set_password(self, service: str, name: str, secret: str) -> None:
                plain.set_password(service, name, secret)

        forget_class(self, Swapped)
        genuine.__class__ = Swapped
        for kind in KEY_KINDS:
            for operation in (lambda: store.put(kind, "fixture", FAKE + "-swapped"), lambda: store.get(kind, "fixture"),
                              lambda: store.delete(kind, "fixture")):
                with self.assertRaises(KeyRefused) as ctx:
                    operation()
                self.assertEqual(ctx.exception.cause, "keychain_changed")
        self.assertEqual((plain.calls, plain.data), ([], {}))
        # the earlier key is still where it was
        self.assertEqual(genuine.data, with_marker({("csl-cockpit", "venue:fixture"): FAKE}, "SecretService Keyring"))

    def test_an_imitation_chainer_is_not_read_as_a_chain(self) -> None:
        for module in ("keyring.backends.chainer", "keyring.backends.chainerX", "keyring.backends.chainer.evil"):
            with self.subTest(module=module):
                ss = self.k.os()
                fake = lookalike_chain([ss], module)
                store, status = store_for_host(key_file(self), loader(fake))
                self.assertFalse(status.available)
                self.assert_refused(store)
                self.assertEqual(ss.calls, [])
        ss = self.k.os()
        store, status = store_for_host(key_file(self), loader(self.k.chain([untouchable(*PLAIN)[0], ss])))
        self.assertTrue(status.available)  # the library's own chainer still works

    def test_a_refused_chain_says_why_and_is_never_read(self) -> None:
        # The third Codex read: a chain Cockpit does not admit says why, as a refused keychain does, without
        # its members being read and without an exception's message. Each refused chain here holds a genuine
        # OS keychain, so reading its members would have made it usable.
        ss = self.k.os()
        reads: list[str] = []

        class _Members:
            def __init__(self, label: str) -> None:
                self.label = label

            def __get__(self, obj: object, owner: object) -> list[object]:
                reads.append(self.label)
                return [ss]

        class DistroChain(genuine_class_chainer()):  # type: ignore[misc]
            backends = _Members("subclass")

        forget_class(self, DistroChain)
        type.__setattr__(DistroChain, "__qualname__", "DistroChain")
        forged = type("ChainerBackend", (), {"__module__": "keyring.backends.chainer", "backends": _Members("forged")})()
        cases = {
            "a subclass of the library's chainer": (DistroChain(), f"unsupported_keyring_chain:{__name__}.DistroChain"),
            "the chainer's module and class name": (forged, "unsupported_keyring_chain:keyring.backends.chainer.ChainerBackend"),
        }
        for label, (backend, reason) in cases.items():
            with self.subTest(label):
                store, status = store_for_host(key_file(self), loader(backend))
                self.assertEqual((status.available, status.reason), (False, reason))
                self.assertIn(f"no OS keychain ({reason})", doctor_line(doctor(status, None)))
                self.assert_refused(store)
                # A member of the library's own chain with no OS keychain beside it: the same reason.
                store, status = store_for_host(key_file(self), loader(self.k.chain([recorder(), backend])))
                self.assertEqual((status.available, status.reason), (False, reason))
        self.assertEqual(reads, [])
        # The library's own chain inside the library's chain (the fourth Codex read): a fixed reason, and the
        # inner chain is not read, although it holds a genuine OS keychain.
        inner = self.k.chain([ss])
        store, status = store_for_host(key_file(self), loader(self.k.chain([recorder(), inner])))
        self.assertEqual((status.available, status.reason), (False, "unsupported_nested_keyring_chain"))
        self.assert_refused(store)
        self.assertEqual(inner.reads, 0)
        # A refused chain whose metaclass hooks its name fields: it is named from the interpreter's own type
        # fields, so none of its code runs (the hook would also read the chain's members).
        hooked: list[str] = []
        chainer = genuine_class_chainer()

        class Meta(type(chainer)):  # type: ignore[misc]
            def __getattribute__(cls, name: str) -> object:
                if name in ("__module__", "__qualname__", "__name__", "__mro__"):
                    hooked.append(name)
                return super().__getattribute__(name)

        class HookedChain(chainer, metaclass=Meta):  # type: ignore[misc]
            backends = _Members("hooked")

        forget_class(self, HookedChain)
        type.__setattr__(HookedChain, "__qualname__", "HookedChain")
        hooked.clear()
        store, status = store_for_host(key_file(self), loader(HookedChain()))
        self.assertEqual((status.available, status.reason), (False, f"unsupported_keyring_chain:{__name__}.HookedChain"))
        self.assertEqual((hooked, reads), ([], []))

        class ErrorMeta(type):
            def __getattribute__(cls, name: str) -> object:
                if name in ("__module__", "__qualname__", "__name__"):
                    hooked.append(name)
                return super().__getattribute__(name)

        class HookedError(Exception, metaclass=ErrorMeta):
            pass

        store, status = store_for_host(key_file(self), loader(self.k.os("KWallet5", priority=HookedError("never shown"))))
        self.assertEqual((status.available, status.reason, hooked), (False, "keychain_unusable:HookedError", []))
        # The library's own chain while its module will not import at verification: refused, said, not read.
        chain, kwallet = self.k.chain([recorder(), self.k.os()]), self.k.os("KWallet5")
        real_import = detect_module.importlib.import_module

        def import_module(name: str) -> object:
            if name == "keyring.backends.chainer":
                raise ImportError("/made-up/private-folder " + FAKE)
            return real_import(name)

        with mock.patch.object(detect_module.importlib, "import_module", import_module):
            store, status = store_for_host(key_file(self), loader(chain))
            self.assertEqual((status.available, status.reason),
                             (False, "keychain_module_unavailable:keyring.backends.chainer:ImportError"))
            line = doctor_line(doctor(status, None))
            self.assertNotIn("made-up", line)
            self.assertNotIn(FAKE, line)
            self.assert_refused(store)
            self.assertEqual(chain.reads, 0)
            # Not reported when the selected backend is an OS keychain itself.
            store, status = store_for_host(key_file(self), loader(kwallet))
            self.assertEqual((status.available, status.reason), (True, "ok"))
        self.assertEqual(ss.calls, [])

    def test_a_chain_that_changes_after_the_check_cannot_redirect_a_key(self) -> None:
        ss = self.k.os()
        plain = recorder()
        ch = self.k.chain([ss, plain])
        load = loader(ch)
        store, status = store_for_host(key_file(self), load)
        self.assertTrue(status.available)
        ch.children[:] = [plain, plain]  # the library recomputes its list on every read
        load().backend = plain  # and the global keyring may be replaced (keyring.set_keyring)
        self.round_trip_every_kind(store)
        self.assertEqual((plain.calls, plain.data), ([], {}))
        self.assertEqual(len(ss.calls), 4 * len(KEY_KINDS) + MARKER_CALLS)
        self.assertEqual(ch.calls, [])

    def test_the_chain_is_read_once(self) -> None:
        # After the first read the chain would answer only the plaintext store; nothing reads it again.
        ss, plain = self.k.os(), recorder()
        ch = self.k.chain([ss, plain])
        store, status = store_for_host(key_file(self), loader(ch))
        self.assertTrue(status.available)
        ch.children = [plain]
        self.round_trip_every_kind(store)
        self.assertEqual(ch.reads, 1)
        self.assertEqual((plain.calls, plain.data), ([], {}))
        self.assertEqual(len(ss.calls), 4 * len(KEY_KINDS) + MARKER_CALLS)

    def test_a_chain_without_an_os_keychain_is_still_refused(self) -> None:
        def plain() -> tuple[object, list]:
            return untouchable(*PLAIN)

        def helper() -> tuple[object, list]:
            return untouchable(*HELPER)

        def sentinel(kind: str) -> tuple[object, list]:
            backend = self.k.os(kind, priority=0)
            return backend, backend.calls

        def nested() -> tuple[object, list]:
            inner = self.k.os()
            return self.k.chain([inner]), inner.calls

        refused = {
            "plaintext only": [plain],
            "plaintext and helper": [plain, helper],
            "sentinels only": [lambda: sentinel("fail"), lambda: sentinel("null")],
            "sentinel and plaintext": [lambda: sentinel("fail"), plain],
            "empty": [],
            "keychain only inside a nested chain": [nested],
        }
        for label, makers in refused.items():
            with self.subTest(label):
                made = [make() for make in makers]
                store, status = store_for_host(key_file(self), loader(self.k.chain([kid for kid, _ in made])))
                self.assertFalse(status.available)
                self.assertEqual(status.reason, "unsupported_nested_keyring_chain" if label == "keychain only inside a nested chain" else "no_keychain_backend")
                self.assertEqual(status.ignored, ())
                self.assert_refused(store)
                self.assertEqual([record for _, record in made], [[] for _ in made])
        store, status = store_for_host(key_file(self), loader(self.k.chain([untouchable(*PLAIN)[0], self.k.os()])))
        self.assertTrue(status.available)  # the same chain with a keychain in it is used

    def test_secret_service_is_used_before_kwallet_in_any_chain_order(self) -> None:
        for order in ((0, 1, 2), (1, 0, 2), (2, 1, 0), (1, 2, 0)):
            with self.subTest(order=order):
                kwallet, ss = self.k.os("KWallet5"), self.k.os()
                plain, touched = untouchable(*PLAIN)
                members = [kwallet, ss, plain]
                store, status = store_for_host(key_file(self), loader(self.k.chain([members[i] for i in order])))
                self.assertTrue(status.available)
                self.assertEqual(status.backend, "SecretService Keyring")
                names = {0: "keyring.backends.kwallet.DBusKeyring", 2: ".".join(PLAIN)}
                self.assertEqual(status.ignored, tuple(names[i] for i in order if i != 1))
                self.round_trip_every_kind(store)
                self.assertEqual((kwallet.calls, touched), ([], []))

    def test_two_os_keychains_follow_cockpits_order_and_name_the_other(self) -> None:
        # The two pairs keyring 25.7 can find on one desktop, in the order the library hands them over.
        for first, second, pick in (("SecretService", "libsecret", 0), ("KWallet5", "SecretService", 1)):
            with self.subTest(first=first, second=second):
                pair = [self.k.os(first), self.k.os(second)]
                used, other = pair[pick], pair[1 - pick]
                plain, touched = untouchable(*PLAIN)
                store, status = store_for_host(key_file(self), loader(self.k.chain([*pair, plain])))
                self.assertEqual(status.backend, type(used).name)
                other_name = f"{type(other).__module__}.{type(other).__qualname__}"
                self.assertEqual(status.ignored, (other_name, ".".join(PLAIN)))
                for kind in KEY_KINDS:
                    store.put(kind, "fixture", FAKE)
                    self.assertEqual(store.get(kind, "fixture"), FAKE)
                self.assertEqual(len(used.data), len(KEY_KINDS) + MARKER_ITEMS)
                self.assertEqual((other.calls, other.data, touched), ([], {}, []))
                self.assertEqual(doctor_line(doctor(status, None)), (
                    f"keys: ok - OS keychain ({type(used).name}); model-provider, broker and venue keys in the keychain; "
                    f"other keyring backends ignored: {other_name}, keyrings.alt.file.PlaintextKeyring"))

    def test_every_pair_of_os_keychains_follows_the_fixed_order(self) -> None:
        # keyring 25.7.0's priorities in a KDE desktop session and elsewhere; the library chains by priority.
        priority = {"desktop": {"SecretService": 5, "KWallet5": 5.1, "libsecret": 4.8, "KWallet4": 4.1},
                    "elsewhere": {"SecretService": 5, "KWallet5": 4.9, "libsecret": 4.8, "KWallet4": 3.9}}
        # pair: (library's pick on a KDE desktop, library's pick elsewhere, Cockpit's pick in both)
        table = {
            ("SecretService", "libsecret"): ("SecretService", "SecretService", "SecretService"),
            ("SecretService", "KWallet5"): ("KWallet5", "SecretService", "SecretService"),  # the one the library flips
            ("SecretService", "KWallet4"): ("SecretService", "SecretService", "SecretService"),
            ("KWallet5", "libsecret"): ("KWallet5", "KWallet5", "KWallet5"),
            ("KWallet5", "KWallet4"): ("KWallet5", "KWallet5", "KWallet5"),
            ("libsecret", "KWallet4"): ("libsecret", "libsecret", "libsecret"),
        }
        for pair, (lib_desktop, lib_elsewhere, cockpit) in table.items():
            for session, library_pick in (("desktop", lib_desktop), ("elsewhere", lib_elsewhere)):
                with self.subTest(pair=pair, session=session):
                    order = sorted(pair, key=lambda kind: priority[session][kind], reverse=True)
                    self.assertEqual(order[0], library_pick)  # what the library itself would use
                    rings = {kind: self.k.os(kind) for kind in pair}
                    store, status = store_for_host(key_file(self), loader(self.k.chain([rings[kind] for kind in order])))
                    self.assertEqual(status.backend, NAMES[cockpit])
                    store.put("broker", "fixture", FAKE)
                    other = next(kind for kind in pair if kind != cockpit)
                    self.assertEqual((len(rings[cockpit].data), rings[other].calls), (1 + MARKER_ITEMS, []))
                    other_type = type(rings[other])
                    self.assertIn(f"other keyring backends ignored: {other_type.__module__}.{other_type.__qualname__}",
                                  doctor_line(doctor(status, None)))

    def test_one_kde_host_picks_the_same_keychain_in_both_session_kinds(self) -> None:
        # keyring 25.7 hands over a desktop session's chain and an SSH, service or cron session's chain in
        # different orders (KWallet 5.1 against 4.9). Cockpit picks the same keychain from both, so a key
        # saved in one session is found in the other.
        cases = {
            # name: (kinds, desktop order, other order, expected keychain)
            "SecretService + KWallet 5": (["SecretService", "KWallet5"], (1, 0), (0, 1), "SecretService"),
            "SecretService + KWallet 5 + KWallet 4": (["SecretService", "KWallet5", "KWallet4"], (1, 0, 2), (0, 1, 2),
                                                      "SecretService"),
            "KWallet 5 only": (["KWallet5"], (0,), (0,), "KWallet5"),
            "KWallet 5 + KWallet 4": (["KWallet5", "KWallet4"], (0, 1), (0, 1), "KWallet5"),
            "SecretService + libsecret (GNOME)": (["SecretService", "libsecret"], (0, 1), (0, 1), "SecretService"),
        }
        for label, (kinds, desktop_order, other_order, expected) in cases.items():
            with self.subTest(label):
                rings = [self.k.os(kind) for kind in kinds]
                path = key_file(self)  # one host, two sessions
                desktop, status = store_for_host(path, loader(self.k.chain([rings[i] for i in desktop_order])))
                self.assertEqual(status.backend, NAMES[expected])
                desktop_ignored = status.ignored
                desktop.put("broker", "fixture", FAKE)
                elsewhere, status = store_for_host(path, loader(self.k.chain([rings[i] for i in other_order])))
                self.assertEqual(status.backend, NAMES[expected])
                self.assertEqual(sorted(status.ignored), sorted(desktop_ignored))
                self.assertEqual(elsewhere.get("broker", "fixture"), FAKE)
                self.assertEqual([len(ring.data) for ring in rings], [1 + MARKER_ITEMS if kind == expected else 0 for kind in kinds])

    def test_the_same_class_twice_keeps_the_chains_order(self) -> None:
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                pair = [self.k.os(), self.k.os()][::-1 if reverse else 1]
                store, _ = store_for_host(key_file(self), loader(self.k.chain([untouchable(*PLAIN)[0], *pair])))
                store.put("broker", "fixture", FAKE)
                self.assertEqual(pair[0].data, with_marker({("csl-cockpit", "broker:fixture"): FAKE}, "SecretService Keyring"))
                self.assertEqual(pair[1].calls, [])

    def test_the_librarys_sentinels_are_skipped_without_a_name(self) -> None:
        fail, null, ss = self.k.os("fail", priority=0), self.k.os("null", priority=-1), self.k.os()
        store, status = store_for_host(key_file(self), loader(self.k.chain([fail, ss, null, untouchable(*PLAIN)[0]])))
        self.assertEqual((status.backend, status.ignored), ("SecretService Keyring", (".".join(PLAIN),)))
        self.round_trip_every_kind(store)
        self.assertEqual((fail.calls, null.calls), ([], []))

    def test_a_change_of_keychain_reads_only_the_selected_keychain(self) -> None:
        # The order is fixed, but which keychains answer can differ between sessions (a daemon reachable on
        # the desktop and not over SSH). Then a later run reads whatever the newly selected keychain holds:
        # nothing if it never held the key, or an older key left there, even one "deleted" since in the
        # other keychain (a delete reaches only the selected keychain). Never anything from an unselected
        # store. Codex's "switching" case, pinned as it is. Since B24 this holds only for keys that are not
        # recorded: model-provider keys, and every kind where no record is kept (Windows). A recorded broker or
        # venue key is refused instead (test_b24_keychain_record).
        for kind in UNRECORDED_KINDS:
            with self.subTest(kind=kind):
                path = key_file(self)  # one host, three runs
                kwallet, ss = self.k.os("KWallet5"), self.k.os()
                plain = recorder()
                plain.data[("csl-cockpit", f"{kind}:fixture")] = FAKE + "-plain"  # never read
                first, status = store_for_host(path, loader(self.k.chain([kwallet, plain])))
                self.assertEqual(status.ignored, ("keyrings.alt.file.PlaintextKeyring",))
                first.put(kind, "fixture", FAKE + "-OLD")
                second, status = store_for_host(path, loader(self.k.chain([ss, plain])))
                self.assertEqual((status.backend, status.ignored),
                                 ("SecretService Keyring", ("keyrings.alt.file.PlaintextKeyring",)))
                self.assertIsNone(second.get(kind, "fixture"))  # an empty destination: "no key saved"
                second.put(kind, "fixture", FAKE + "-NEW")
                second.delete(kind, "fixture")
                self.assertEqual(ss.data, {})
                kwallet_calls = list(kwallet.calls)
                third, status = store_for_host(path, loader(self.k.chain([kwallet, plain])))
                self.assertEqual(status.backend, "kwallet DBusKeyring")
                self.assertEqual(third.get(kind, "fixture"), FAKE + "-OLD")  # the older key comes back
                self.assertEqual(kwallet.calls[len(kwallet_calls):], [("get", "csl-cockpit", f"{kind}:fixture")])
                self.assertEqual(kwallet.data, {("csl-cockpit", f"{kind}:fixture"): FAKE + "-OLD"})
                self.assertEqual(plain.calls, [])
                self.assertEqual(plain.data, {("csl-cockpit", f"{kind}:fixture"): FAKE + "-plain"})
                self.assertEqual([op for op, _, _ in ss.calls], ["get", "set", "delete"])  # nothing since

    def test_an_unusable_chosen_keychain_refuses_and_falls_through_to_nothing(self) -> None:
        for priority in (0, -1, float("nan"), float("inf"), RuntimeError("no dbus")):
            with self.subTest(priority=repr(priority)):
                dead = self.k.os(priority=priority)
                dead.__dict__["priority"] = 5  # an instance attribute never stands in for the class's probe
                plain, touched = untouchable(*PLAIN)
                spare = self.k.os("KWallet5")
                store, status = store_for_host(key_file(self), loader(self.k.chain([plain, dead, spare])))
                self.assertFalse(status.available)
                self.assertTrue(status.reason.startswith("keychain_unusable"))
                self.assert_refused(store)
                self.assertEqual((dead.calls, spare.calls, touched), ([], [], []))
        store, status = store_for_host(key_file(self), loader(self.k.chain([untouchable(*PLAIN)[0], self.k.os()])))
        self.assertTrue(status.available)


@requires_keyring
class DoctorLineTests(_Case):
    def test_doctor_line_names_each_ignored_backend(self) -> None:
        ch = self.k.chain([untouchable(*HELPER)[0], self.k.os(), untouchable(*PLAIN)[0]])
        _, status = store_for_host(key_file(self), loader(ch))
        result = doctor(status, None)
        self.assertEqual(result["level"], "ok")
        self.assertEqual(doctor_line(result), (
            "keys: ok - OS keychain (SecretService Keyring); model-provider, broker and venue keys in the keychain; "
            "other keyring backends ignored: review_standin.pip_credential_helper.CredentialHelper, "
            "keyrings.alt.file.PlaintextKeyring"))

    def test_hostile_backend_names_stay_on_one_line(self) -> None:
        hostile = untouchable("evil\nkeys: ok; broker in keychain\r", "Plain;text,Keyring\x1b[2J")[0]
        _, status = store_for_host(key_file(self), loader(self.k.chain([hostile, self.k.os()])))
        line = doctor_line(doctor(status, None))
        self.assertEqual(len(line.splitlines()), 1)
        self.assertNotIn("\x1b", line)
        ignored = line.split("other keyring backends ignored: ", 1)[1]
        self.assertNotIn(";", ignored)
        self.assertNotIn(",", ignored)

    def test_the_selected_keychain_is_named_by_cockpit_not_by_its_class(self) -> None:
        # The fourth Codex read: the selected keychain's name came from the class itself, uncleaned. Now it is
        # Cockpit's own name for the admitted class, so a changed class name or module shows nowhere.
        for kind in ("SecretService", "KWallet5", "libsecret", "KWallet4", "macOS", "Windows"):
            with self.subTest(kind):
                backend = self.k.os(kind)
                cls = type(backend)
                saved = (cls.__name__, cls.__dict__["__module__"])
                self.addCleanup(setattr, cls, "__module__", saved[1])
                self.addCleanup(setattr, cls, "__name__", saved[0])
                cls.__name__ = cls.__module__ = FAKE + " /made-up/folder\n\x1b[31m" + "Z" * 140
                _, status = store_for_host(key_file(self), loader(self.k.chain([backend])))
                line = doctor_line(doctor(status, None))
                cls.__name__, cls.__module__ = saved
                self.assertEqual(status.backend, NAMES[kind])
                self.assertEqual(len(line.splitlines()), 1)
                self.assertNotIn(FAKE, line)
                self.assertIn(f"OS keychain ({NAMES[kind]})", line)

    def test_a_long_list_of_ignored_backends_is_capped(self) -> None:
        extras = [untouchable(f"extra.backend{i}", "Keyring")[0] for i in range(11)]
        _, status = store_for_host(key_file(self), loader(self.k.chain([self.k.os(), *extras])))
        self.assertEqual(len(status.ignored), 11)
        line = doctor_line(doctor(status, None))
        self.assertIn("extra.backend7.Keyring, and 3 more", line)
        self.assertNotIn("extra.backend8", line)

    def test_cli_prints_the_ignored_backends(self) -> None:
        from cockpit.keys import __main__ as cli
        _, status = store_for_host(key_file(self), loader(self.k.chain([untouchable(*PLAIN)[0], self.k.os()])))
        orig = cli.detect_keychain
        cli.detect_keychain = lambda: status  # type: ignore[assignment]
        self.addCleanup(setattr, cli, "detect_keychain", orig)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["doctor", "--key-file", key_file(self)])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip().splitlines(), [
            "keys: ok - OS keychain (SecretService Keyring); model-provider, broker and venue keys in the keychain; "
            "other keyring backends ignored: keyrings.alt.file.PlaintextKeyring"])


if __name__ == "__main__":
    unittest.main()
