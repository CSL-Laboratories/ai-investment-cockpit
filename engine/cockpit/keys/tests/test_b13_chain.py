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

from cockpit.keys import KEY_KINDS, KeyRefused, doctor, doctor_line, store_for_host
from cockpit.keys.tests.fakes import (
    FAKE, NAMES, Keychains, forget_class, genuine_class, lookalike_chain, loader, recorder, requires_keyring,
    untouchable,
)

PATH = "/nonexistent-cockpit-b13/model-keys.json"  # a keychain host never touches the file
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
                store, status = store_for_host(PATH, loader(ch))
                self.assertTrue(status.available)
                self.assertEqual(status.backend, "SecretService Keyring")
                self.assertEqual(status.ignored, tuple(name for _, name in links if name))
                self.round_trip_every_kind(store)
                self.assertEqual(len(ss.calls), 4 * len(KEY_KINDS))
                self.assertEqual(ss.data, {})
                self.assertEqual((plain_touched, helper_touched, ch.calls), ([], [], []))

    def test_file_backend_first_in_the_chain_is_never_used(self) -> None:
        # A working plaintext store, first in the chain and with a higher priority than the keychain.
        for priority in (0.5, 100):
            with self.subTest(priority=priority):
                plain = recorder(priority=priority)
                ss = self.k.os()
                store, status = store_for_host(PATH, loader(self.k.chain([plain, ss])))
                self.assertTrue(status.available)
                self.assertEqual(status.ignored, ("keyrings.alt.file.PlaintextKeyring",))
                for kind in ("broker", "venue", "model_provider"):
                    store.put(kind, "fixture", FAKE)
                    self.assertEqual(store.get(kind, "fixture"), FAKE)
                self.assertEqual((plain.calls, plain.data), ([], {}))
                self.assertEqual(sorted(name for _, name in ss.data),
                                 ["broker:fixture", "model_provider:fixture", "venue:fixture"])

    def test_imitation_module_names_are_never_an_os_keychain(self) -> None:
        for module in IMITATIONS:
            with self.subTest(module=repr(module)):
                fake, touched = untouchable(module, "Keyring")
                store, status = store_for_host(PATH, loader(fake))
                self.assertFalse(status.available)
                self.assertEqual(status.reason, "no_keychain_backend")
                self.assert_refused(store)
                ss = self.k.os()
                store, status = store_for_host(PATH, loader(self.k.chain([fake, ss])))
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
                store, status = store_for_host(PATH, loader(fake))
                self.assertFalse(status.available)
                self.assert_refused(store)
                kwallet = self.k.os("KWallet5")
                store, status = store_for_host(PATH, loader(self.k.chain([fake, kwallet])))
                self.assertEqual(status.backend, "kwallet DBusKeyring")
                self.round_trip_every_kind(store)
                self.assertEqual((fake.calls, fake.data), ([], {}))
                self.assertEqual(len(kwallet.calls), 4 * len(KEY_KINDS))

    def test_a_subclass_of_a_genuine_keychain_is_not_admitted(self) -> None:
        # isinstance() would admit it; identity does not. Its own methods would write to plaintext.
        written: dict = {}
        genuine = genuine_class("SecretService")

        class External(genuine):  # type: ignore[misc, valid-type]
            priority = 5

            def set_password(self, service: str, name: str, secret: str) -> None:
                written[(service, name)] = secret

        forget_class(self, External)
        store, status = store_for_host(PATH, loader(External()))
        self.assertFalse(status.available)
        self.assert_refused(store)
        kwallet = self.k.os("KWallet5")
        store, status = store_for_host(PATH, loader(self.k.chain([External(), kwallet])))
        self.assertEqual(status.backend, "kwallet DBusKeyring")
        store.put("broker", "fixture", FAKE)
        self.assertEqual(written, {})

    def test_a_proxy_is_refused_and_no_later_swap_redirects_a_key(self) -> None:
        # A proxy with forged metadata whose target can change after detection: refused outright.
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
        store, status = store_for_host(PATH, loader(proxy))
        self.assertFalse(status.available)
        proxy.target = plain
        self.assert_refused(store)
        self.assertEqual(plain.calls, [])
        # A genuine object passes; changing its instance attributes or its __class__ afterwards does not
        # redirect anything: every call goes through the genuine class's own method.
        genuine = self.k.os()
        store, status = store_for_host(PATH, loader(genuine))
        self.assertTrue(status.available)
        genuine.set_password = plain.set_password
        genuine.get_password = plain.get_password

        class Swapped(type(genuine)):  # type: ignore[misc]
            def set_password(self, service: str, name: str, secret: str) -> None:
                plain.set_password(service, name, secret)

        forget_class(self, Swapped)
        genuine.__class__ = Swapped
        store.put("venue", "fixture", FAKE)
        self.assertEqual(store.get("venue", "fixture"), FAKE)
        self.assertEqual((plain.calls, plain.data), ([], {}))
        self.assertEqual(list(genuine.data.values()), [FAKE])

    def test_an_imitation_chainer_is_not_read_as_a_chain(self) -> None:
        for module in ("keyring.backends.chainer", "keyring.backends.chainerX", "keyring.backends.chainer.evil"):
            with self.subTest(module=module):
                ss = self.k.os()
                fake = lookalike_chain([ss], module)
                store, status = store_for_host(PATH, loader(fake))
                self.assertFalse(status.available)
                self.assert_refused(store)
                self.assertEqual(ss.calls, [])
        ss = self.k.os()
        store, status = store_for_host(PATH, loader(self.k.chain([untouchable(*PLAIN)[0], ss])))
        self.assertTrue(status.available)  # the library's own chainer still works

    def test_a_chain_that_changes_after_the_check_cannot_redirect_a_key(self) -> None:
        ss = self.k.os()
        plain = recorder()
        ch = self.k.chain([ss, plain])
        load = loader(ch)
        store, status = store_for_host(PATH, load)
        self.assertTrue(status.available)
        ch.children[:] = [plain, plain]  # the library recomputes its list on every read
        load().backend = plain  # and the global keyring may be replaced (keyring.set_keyring)
        self.round_trip_every_kind(store)
        self.assertEqual((plain.calls, plain.data), ([], {}))
        self.assertEqual(len(ss.calls), 4 * len(KEY_KINDS))
        self.assertEqual(ch.calls, [])

    def test_the_chain_is_read_once(self) -> None:
        ss = self.k.os()
        ch = self.k.chain([ss, recorder()])
        store, status = store_for_host(PATH, loader(ch))
        self.assertTrue(status.available)
        self.round_trip_every_kind(store)
        self.assertEqual(ch.reads, 1)

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
                store, status = store_for_host(PATH, loader(self.k.chain([kid for kid, _ in made])))
                self.assertFalse(status.available)
                self.assertEqual(status.reason, "no_keychain_backend")
                self.assertEqual(status.ignored, ())
                self.assert_refused(store)
                self.assertEqual([record for _, record in made], [[] for _ in made])
        store, status = store_for_host(PATH, loader(self.k.chain([untouchable(*PLAIN)[0], self.k.os()])))
        self.assertTrue(status.available)  # the same chain with a keychain in it is used

    def test_secret_service_is_used_before_kwallet_in_any_chain_order(self) -> None:
        for order in ((0, 1, 2), (1, 0, 2), (2, 1, 0), (1, 2, 0)):
            with self.subTest(order=order):
                kwallet, ss = self.k.os("KWallet5"), self.k.os()
                plain, touched = untouchable(*PLAIN)
                members = [kwallet, ss, plain]
                store, status = store_for_host(PATH, loader(self.k.chain([members[i] for i in order])))
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
                store, status = store_for_host(PATH, loader(self.k.chain([*pair, plain])))
                self.assertEqual(status.backend, type(used).name)
                other_name = f"{type(other).__module__}.{type(other).__qualname__}"
                self.assertEqual(status.ignored, (other_name, ".".join(PLAIN)))
                for kind in KEY_KINDS:
                    store.put(kind, "fixture", FAKE)
                    self.assertEqual(store.get(kind, "fixture"), FAKE)
                self.assertEqual(len(used.data), len(KEY_KINDS))
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
                    store, status = store_for_host(PATH, loader(self.k.chain([rings[kind] for kind in order])))
                    self.assertEqual(status.backend, NAMES[cockpit])
                    store.put("broker", "fixture", FAKE)
                    other = next(kind for kind in pair if kind != cockpit)
                    self.assertEqual((len(rings[cockpit].data), rings[other].calls), (1, []))
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
                desktop, status = store_for_host(PATH, loader(self.k.chain([rings[i] for i in desktop_order])))
                self.assertEqual(status.backend, NAMES[expected])
                desktop_ignored = status.ignored
                desktop.put("broker", "fixture", FAKE)
                elsewhere, status = store_for_host(PATH, loader(self.k.chain([rings[i] for i in other_order])))
                self.assertEqual(status.backend, NAMES[expected])
                self.assertEqual(sorted(status.ignored), sorted(desktop_ignored))
                self.assertEqual(elsewhere.get("broker", "fixture"), FAKE)
                self.assertEqual([len(ring.data) for ring in rings], [1 if kind == expected else 0 for kind in kinds])

    def test_the_same_class_twice_keeps_the_chains_order(self) -> None:
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                pair = [self.k.os(), self.k.os()][::-1 if reverse else 1]
                store, _ = store_for_host(PATH, loader(self.k.chain([untouchable(*PLAIN)[0], *pair])))
                store.put("broker", "fixture", FAKE)
                self.assertEqual(pair[0].data, {("csl-cockpit", "broker:fixture"): FAKE})
                self.assertEqual(pair[1].calls, [])

    def test_the_librarys_sentinels_are_skipped_without_a_name(self) -> None:
        fail, null, ss = self.k.os("fail", priority=0), self.k.os("null", priority=-1), self.k.os()
        store, status = store_for_host(PATH, loader(self.k.chain([fail, ss, null, untouchable(*PLAIN)[0]])))
        self.assertEqual((status.backend, status.ignored), ("SecretService Keyring", (".".join(PLAIN),)))
        self.round_trip_every_kind(store)
        self.assertEqual((fail.calls, null.calls), ([], []))

    def test_a_change_of_keychain_reads_only_the_selected_keychain(self) -> None:
        # The order is fixed, but which keychains answer can differ between sessions (a daemon reachable on
        # the desktop and not over SSH). Then a later run reads whatever the newly selected keychain holds:
        # nothing if it never held the key, or an older key left there, even one "deleted" since in the
        # other keychain (a delete reaches only the selected keychain). Never anything from an unselected
        # store. Codex's "switching" case, pinned as it is.
        kwallet, ss = self.k.os("KWallet5"), self.k.os()
        plain = recorder()
        plain.data[("csl-cockpit", "broker:fixture")] = FAKE + "-plain"  # never read
        first, _ = store_for_host(PATH, loader(self.k.chain([kwallet, plain])))
        first.put("broker", "fixture", FAKE + "-OLD")
        second, status = store_for_host(PATH, loader(self.k.chain([ss, plain])))
        self.assertEqual(status.backend, "SecretService Keyring")
        self.assertIsNone(second.get("broker", "fixture"))  # an empty destination: "no key saved"
        second.put("broker", "fixture", FAKE + "-NEW")
        second.delete("broker", "fixture")
        kwallet_calls = list(kwallet.calls)
        third, status = store_for_host(PATH, loader(self.k.chain([kwallet, plain])))
        self.assertEqual(status.backend, "kwallet DBusKeyring")
        self.assertEqual(third.get("broker", "fixture"), FAKE + "-OLD")  # the older key comes back
        self.assertEqual(kwallet.calls[len(kwallet_calls):], [("get", "csl-cockpit", "broker:fixture")])
        self.assertEqual(plain.calls, [])
        self.assertEqual([op for op, _, _ in ss.calls], ["get", "set", "delete"])  # nothing since

    def test_an_unusable_chosen_keychain_refuses_and_falls_through_to_nothing(self) -> None:
        for priority in (0, -1, float("nan"), float("inf"), RuntimeError("no dbus")):
            with self.subTest(priority=repr(priority)):
                dead = self.k.os(priority=priority)
                dead.__dict__["priority"] = 5  # an instance attribute never stands in for the class's probe
                plain, touched = untouchable(*PLAIN)
                spare = self.k.os("KWallet5")
                store, status = store_for_host(PATH, loader(self.k.chain([plain, dead, spare])))
                self.assertFalse(status.available)
                self.assertTrue(status.reason.startswith("keychain_unusable"))
                self.assert_refused(store)
                self.assertEqual((dead.calls, spare.calls, touched), ([], [], []))
        store, status = store_for_host(PATH, loader(self.k.chain([untouchable(*PLAIN)[0], self.k.os()])))
        self.assertTrue(status.available)


@requires_keyring
class DoctorLineTests(_Case):
    def test_doctor_line_names_each_ignored_backend(self) -> None:
        ch = self.k.chain([untouchable(*HELPER)[0], self.k.os(), untouchable(*PLAIN)[0]])
        _, status = store_for_host(PATH, loader(ch))
        result = doctor(status, None)
        self.assertEqual(result["level"], "ok")
        self.assertEqual(doctor_line(result), (
            "keys: ok - OS keychain (SecretService Keyring); model-provider, broker and venue keys in the keychain; "
            "other keyring backends ignored: review_standin.pip_credential_helper.CredentialHelper, "
            "keyrings.alt.file.PlaintextKeyring"))

    def test_hostile_backend_names_stay_on_one_line(self) -> None:
        hostile = untouchable("evil\nkeys: ok; broker in keychain\r", "Plain;text,Keyring\x1b[2J")[0]
        _, status = store_for_host(PATH, loader(self.k.chain([hostile, self.k.os()])))
        line = doctor_line(doctor(status, None))
        self.assertEqual(len(line.splitlines()), 1)
        self.assertNotIn("\x1b", line)
        ignored = line.split("other keyring backends ignored: ", 1)[1]
        self.assertNotIn(";", ignored)
        self.assertNotIn(",", ignored)

    def test_a_long_list_of_ignored_backends_is_capped(self) -> None:
        extras = [untouchable(f"extra.backend{i}", "Keyring")[0] for i in range(11)]
        _, status = store_for_host(PATH, loader(self.k.chain([self.k.os(), *extras])))
        self.assertEqual(len(status.ignored), 11)
        line = doctor_line(doctor(status, None))
        self.assertIn("extra.backend7.Keyring, and 3 more", line)
        self.assertNotIn("extra.backend8", line)

    def test_cli_prints_the_ignored_backends(self) -> None:
        from cockpit.keys import __main__ as cli
        _, status = store_for_host(PATH, loader(self.k.chain([untouchable(*PLAIN)[0], self.k.os()])))
        orig = cli.detect_keychain
        cli.detect_keychain = lambda: status  # type: ignore[assignment]
        self.addCleanup(setattr, cli, "detect_keychain", orig)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["doctor", "--key-file", PATH])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip().splitlines(), [
            "keys: ok - OS keychain (SecretService Keyring); model-provider, broker and venue keys in the keychain; "
            "other keyring backends ignored: keyrings.alt.file.PlaintextKeyring"])


if __name__ == "__main__":
    unittest.main()
