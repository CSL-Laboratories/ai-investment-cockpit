"""B13: a keyring chain that holds an OS keychain and other backends (CSL-59).

Recommendation B13, accepted by Lucas on Nov 12, 2026 ("I agree with all B10-B14 Recommendations"):
"use the OS backend, name the ignored one in the doctor line". #323's rule must still hold: "a plaintext
keyring is never treated as an OS keychain for broker or venue secrets". These tests try to break it.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/keys/tests -t engine -v``.
Fakes only: no real keyring, keychain, key, provider, venue or network.
"""

from __future__ import annotations

import contextlib
import io
import itertools
import unittest

from cockpit.keys import KEY_KINDS, KeyRefused, doctor, doctor_line, store_for_host
from cockpit.keys.tests.fakes import FAKE, chain, loader, os_backend, untouchable

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
    "keyring.backends.SecretServiceEvil", "keyring.backends.SecretService.evil",
    "keyring.backends.secretservice", "Keyring.backends.SecretService", "keyrings.backends.SecretService",
    " keyring.backends.Windows", "keyring.backends.Windows ", "keyring.backends.Windows\n",
    "keyring.backends.macOS_", "keyring.backends.macOS.evil", "keyring.backends.kwallet.file",
    "keyring.backends.libsecret2", "keyring.backends", "keyring", "",
    _LyingStr("keyrings.alt.file"), 5,
)


class MixedChainTests(unittest.TestCase):
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

    def test_every_order_of_a_mixed_chain_uses_only_the_os_keychain(self) -> None:
        for order in itertools.permutations(range(3)):
            with self.subTest(order=order):
                ss = os_backend()
                plain, plain_touched = untouchable(*PLAIN)
                helper, helper_touched = untouchable(*HELPER)
                members = [(ss, None), (plain, ".".join(PLAIN)), (helper, ".".join(HELPER))]
                links = [members[i] for i in order]
                ch = chain([obj for obj, _ in links])
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
                plain = os_backend(PLAIN[0], "file PlaintextKeyring", priority=priority)
                ss = os_backend()
                store, status = store_for_host(PATH, loader(chain([plain, ss])))
                self.assertTrue(status.available)
                self.assertEqual(status.ignored, ("keyrings.alt.file.Keyring",))
                for kind in ("broker", "venue", "model_provider"):
                    store.put(kind, "fixture", FAKE)
                    self.assertEqual(store.get(kind, "fixture"), FAKE)
                self.assertEqual((plain.calls, plain.data), ([], {}))
                self.assertEqual(sorted(name for _, name in ss.data),
                                 ["broker:fixture", "model_provider:fixture", "venue:fixture"])

    def test_imitation_module_names_are_never_an_os_keychain(self) -> None:
        for module in IMITATIONS:
            with self.subTest(module=repr(module)):
                fake, touched = untouchable(module)
                store, status = store_for_host(PATH, loader(fake))
                self.assertFalse(status.available)
                self.assertEqual(status.reason, "no_keychain_backend")
                self.assert_refused(store)
                ss = os_backend()
                store, status = store_for_host(PATH, loader(chain([fake, ss])))
                self.assertTrue(status.available)
                self.assertEqual(len(status.ignored), 1)
                self.assertTrue(status.ignored[0].endswith(".PlaintextKeyring"))
                self.round_trip_every_kind(store)
                self.assertEqual(touched, [])

    def test_an_imitation_chainer_is_not_read_as_a_chain(self) -> None:
        for module in ("keyring.backends.chainerX", "keyring.backends.chainer.evil", "keyring.backends.Chainer"):
            with self.subTest(module=module):
                ss = os_backend()
                fake = chain([ss])
                type(fake).__module__ = module
                store, status = store_for_host(PATH, loader(fake))
                self.assertFalse(status.available)
                self.assert_refused(store)
                self.assertEqual((ss.calls, fake.calls), ([], []))
        ss = os_backend()
        store, status = store_for_host(PATH, loader(chain([untouchable(*PLAIN)[0], ss])))
        self.assertTrue(status.available)  # the real chainer's module name still works

    def test_a_chain_that_changes_after_the_check_cannot_redirect_a_key(self) -> None:
        ss = os_backend()
        plain = os_backend(PLAIN[0], "file PlaintextKeyring")
        ch = chain([ss, plain])
        load = loader(ch)
        store, status = store_for_host(PATH, load)
        self.assertTrue(status.available)
        ch.backends[:] = [plain, plain]  # the library recomputes its list on every read
        load().backend = plain  # and the global keyring may be replaced (keyring.set_keyring)
        self.round_trip_every_kind(store)
        self.assertEqual((plain.calls, plain.data), ([], {}))
        self.assertEqual(len(ss.calls), 4 * len(KEY_KINDS))
        self.assertEqual(ch.calls, [])

    def test_a_chain_that_answers_differently_on_each_read_is_read_once(self) -> None:
        ss = os_backend()
        plain = os_backend(PLAIN[0], "file PlaintextKeyring")
        reads: list[int] = []

        class Shifting:
            __module__ = "keyring.backends.chainer"

            @property
            def backends(self) -> list[object]:
                reads.append(1)
                return [ss, plain] if len(reads) == 1 else [plain]

        store, status = store_for_host(PATH, loader(Shifting()))
        self.assertTrue(status.available)
        self.round_trip_every_kind(store)
        self.assertEqual(len(reads), 1)
        self.assertEqual((plain.calls, plain.data), ([], {}))

    def test_a_chain_without_an_os_keychain_is_still_refused(self) -> None:
        def plain() -> tuple[object, list[str]]:
            return untouchable(*PLAIN)

        def helper() -> tuple[object, list[str]]:
            return untouchable(*HELPER)

        def recorder(module: str) -> tuple[object, list[tuple[str, str, str]]]:
            backend = os_backend(module, module)
            return backend, backend.calls

        def nested() -> tuple[object, list[tuple[str, str, str]]]:
            inner = os_backend()
            return chain([inner]), inner.calls

        refused = {
            "plaintext only": [plain],
            "plaintext and helper": [plain, helper],
            "sentinels only": [lambda: recorder("keyring.backends.fail"), lambda: recorder("keyring.backends.null")],
            "sentinel and plaintext": [lambda: recorder("keyring.backends.fail"), plain],
            "empty": [],
            "keychain only inside a nested chain": [nested],
        }
        for label, makers in refused.items():
            with self.subTest(label):
                made = [make() for make in makers]
                store, status = store_for_host(PATH, loader(chain([kid for kid, _ in made])))
                self.assertFalse(status.available)
                self.assertEqual(status.reason, "no_keychain_backend")
                self.assertEqual(status.ignored, ())
                self.assert_refused(store)
                self.assertEqual([record for _, record in made], [[] for _ in made])
        ss = os_backend()
        store, status = store_for_host(PATH, loader(chain([untouchable(*PLAIN)[0], ss])))
        self.assertTrue(status.available)  # the same chain with a keychain in it is used

    def test_secret_service_is_used_before_kwallet_in_any_chain_order(self) -> None:
        # Option B: Cockpit's fixed order among OS keychains, not the chain's (the library's) order.
        for order in ((0, 1, 2), (1, 0, 2), (2, 1, 0), (1, 2, 0)):
            with self.subTest(order=order):
                kwallet = os_backend("keyring.backends.kwallet", "kwallet DBusKeyring", qualname="DBusKeyring")
                ss = os_backend()
                plain, touched = untouchable(*PLAIN)
                members = [kwallet, ss, plain]
                store, status = store_for_host(PATH, loader(chain([members[i] for i in order])))
                self.assertTrue(status.available)
                self.assertEqual(status.backend, "SecretService Keyring")
                names = {0: "keyring.backends.kwallet.DBusKeyring", 2: ".".join(PLAIN)}
                self.assertEqual(status.ignored, tuple(names[i] for i in order if i != 1))
                self.round_trip_every_kind(store)
                self.assertEqual((kwallet.calls, touched), ([], []))

    def test_two_os_keychains_follow_cockpits_order_and_name_the_other(self) -> None:
        # The two pairs keyring 25.7 can find on one desktop, in the order the library hands them over.
        pairs = (
            (("keyring.backends.SecretService", "SecretService Keyring"), ("keyring.backends.libsecret", "libsecret Keyring"), 0),
            (("keyring.backends.kwallet", "kwallet DBusKeyring"), ("keyring.backends.SecretService", "SecretService Keyring"), 1),
        )
        for (first_module, first_name), (second_module, second_name), pick in pairs:
            with self.subTest(first=first_module, second=second_module):
                pair = [os_backend(first_module, first_name), os_backend(second_module, second_name)]
                used, other = pair[pick], pair[1 - pick]
                plain, touched = untouchable(*PLAIN)
                store, status = store_for_host(PATH, loader(chain([*pair, plain])))
                self.assertTrue(status.available)
                self.assertEqual(status.backend, used.name)
                other_name = f"{type(other).__module__}.Keyring"
                self.assertEqual(status.ignored, (other_name, ".".join(PLAIN)))
                for kind in KEY_KINDS:
                    store.put(kind, "fixture", FAKE)
                    self.assertEqual(store.get(kind, "fixture"), FAKE)
                self.assertEqual(len(used.data), len(KEY_KINDS))
                self.assertEqual((other.calls, other.data, touched), ([], {}, []))
                self.assertEqual(doctor_line(doctor(status, None)), (
                    f"keys: ok - OS keychain ({used.name}); model-provider, broker and venue keys in the keychain; "
                    f"other keyring backends ignored: {other_name}, keyrings.alt.file.PlaintextKeyring"))

    def test_one_kde_host_picks_the_same_keychain_in_both_session_kinds(self) -> None:
        # keyring 25.7 hands over a desktop session's chain and an SSH, service or cron session's chain in
        # different orders (KWallet 5.1 against 4.9; logs/72_order_stability.log). Cockpit picks the same
        # keychain from both, so a key saved in one session is found in the other.
        def kw5() -> object:
            return os_backend("keyring.backends.kwallet", "kwallet DBusKeyring", qualname="DBusKeyring")

        def kw4() -> object:
            return os_backend("keyring.backends.kwallet", "kwallet DBusKeyringKWallet4", qualname="DBusKeyringKWallet4")

        cases = {
            # name: (makers, desktop order, other order, expected keychain)
            "SecretService + KWallet 5": ([os_backend, kw5], (1, 0), (0, 1), "SecretService Keyring"),
            "SecretService + KWallet 5 + KWallet 4": ([os_backend, kw5, kw4], (1, 0, 2), (0, 1, 2), "SecretService Keyring"),
            "KWallet 5 only": ([kw5], (0,), (0,), "kwallet DBusKeyring"),
            "KWallet 5 + KWallet 4": ([kw5, kw4], (0, 1), (0, 1), "kwallet DBusKeyring"),
            "SecretService + libsecret (GNOME)": ([os_backend, lambda: os_backend("keyring.backends.libsecret", "libsecret Keyring")],
                                                  (0, 1), (0, 1), "SecretService Keyring"),
        }
        for label, (makers, desktop_order, other_order, expected) in cases.items():
            with self.subTest(label):
                rings = [make() for make in makers]
                desktop, status = store_for_host(PATH, loader(chain([rings[i] for i in desktop_order])))
                self.assertEqual(status.backend, expected)
                desktop_ignored = status.ignored
                desktop.put("broker", "fixture", FAKE)
                elsewhere, status = store_for_host(PATH, loader(chain([rings[i] for i in other_order])))
                self.assertEqual(status.backend, expected)
                self.assertEqual(sorted(status.ignored), sorted(desktop_ignored))
                self.assertEqual(elsewhere.get("broker", "fixture"), FAKE)
                used = [ring for ring in rings if ring.name == expected]
                self.assertEqual([len(ring.data) for ring in rings], [1 if ring in used else 0 for ring in rings])

    def test_every_pair_of_os_keychains_follows_the_fixed_order(self) -> None:
        # keyring 25.7.0's priorities in a KDE desktop session and elsewhere; the library chains by priority.
        priority = {"desktop": {"SS": 5, "KW5": 5.1, "LIB": 4.8, "KW4": 4.1},
                    "elsewhere": {"SS": 5, "KW5": 4.9, "LIB": 4.8, "KW4": 3.9}}
        make = {
            "SS": lambda: os_backend(),
            "KW5": lambda: os_backend("keyring.backends.kwallet", "kwallet DBusKeyring", qualname="DBusKeyring"),
            "LIB": lambda: os_backend("keyring.backends.libsecret", "libsecret Keyring"),
            "KW4": lambda: os_backend("keyring.backends.kwallet", "kwallet DBusKeyringKWallet4",
                                      qualname="DBusKeyringKWallet4"),
        }
        # pair: (library's pick on a KDE desktop, library's pick elsewhere, Cockpit's pick in both)
        table = {
            ("SS", "LIB"): ("SS", "SS", "SS"),
            ("SS", "KW5"): ("KW5", "SS", "SS"),  # the one pair the library flips
            ("SS", "KW4"): ("SS", "SS", "SS"),
            ("KW5", "LIB"): ("KW5", "KW5", "KW5"),
            ("KW5", "KW4"): ("KW5", "KW5", "KW5"),
            ("LIB", "KW4"): ("LIB", "LIB", "LIB"),
        }
        for pair, (lib_desktop, lib_elsewhere, cockpit) in table.items():
            for session, library_pick in (("desktop", lib_desktop), ("elsewhere", lib_elsewhere)):
                with self.subTest(pair=pair, session=session):
                    order = sorted(pair, key=lambda key: priority[session][key], reverse=True)
                    self.assertEqual(order[0], library_pick)  # what the library itself would use
                    rings = {key: make[key]() for key in pair}
                    store, status = store_for_host(PATH, loader(chain([rings[key] for key in order])))
                    self.assertEqual(status.backend, rings[cockpit].name)
                    store.put("broker", "fixture", FAKE)
                    other = next(key for key in pair if key != cockpit)
                    self.assertEqual((len(rings[cockpit].data), rings[other].calls), (1, []))
                    self.assertIn(f"other keyring backends ignored: {type(rings[other]).__module__}."
                                  f"{type(rings[other]).__qualname__}", doctor_line(doctor(status, None)))

    def test_a_tie_in_the_fixed_order_keeps_the_chains_order(self) -> None:
        # Two KWallet classes the order does not list share the module's place; the chain's order decides.
        first = os_backend("keyring.backends.kwallet", "kwallet Future", qualname="FutureKWallet")
        second = os_backend("keyring.backends.kwallet", "kwallet Other", qualname="OtherKWallet")
        for pair in ((first, second), (second, first)):
            with self.subTest(first=pair[0].name):
                _, status = store_for_host(PATH, loader(chain(list(pair))))
                self.assertEqual(status.backend, pair[0].name)
        _, status = store_for_host(PATH, loader(chain([first, os_backend("keyring.backends.libsecret", "libsecret Keyring")])))
        self.assertEqual(status.backend, "libsecret Keyring")  # listed classes come before the module fallback

    def test_the_librarys_sentinels_are_skipped_without_a_name(self) -> None:
        fail = os_backend("keyring.backends.fail", "fail Keyring")
        null = os_backend("keyring.backends.null", "null Keyring")
        ss = os_backend()
        store, status = store_for_host(PATH, loader(chain([fail, ss, null, untouchable(*PLAIN)[0]])))
        self.assertEqual((status.backend, status.ignored), ("SecretService Keyring", (".".join(PLAIN),)))
        self.round_trip_every_kind(store)
        self.assertEqual((fail.calls, null.calls), ([], []))

    def test_a_key_saved_under_another_os_keychain_reads_as_not_saved(self) -> None:
        # The order is fixed, but which keychains answer can still differ between sessions (a daemon
        # reachable on the desktop and not over SSH). A key saved under one then reads as "no key saved"
        # under the other; nothing else is ever consulted.
        kwallet = os_backend("keyring.backends.kwallet", "kwallet DBusKeyring")
        ss = os_backend()
        plain = os_backend(PLAIN[0], "file PlaintextKeyring")
        desktop, status = store_for_host(PATH, loader(chain([kwallet, plain])))
        self.assertEqual(status.backend, "kwallet DBusKeyring")
        for kind in KEY_KINDS:
            desktop.put(kind, "fixture", FAKE)
        plain.data[("csl-cockpit", "broker:fixture")] = FAKE  # even a copy elsewhere is never read
        saved = dict(kwallet.data)
        kwallet.calls.clear()
        later, status = store_for_host(PATH, loader(chain([ss, plain])))
        self.assertEqual(status.backend, "SecretService Keyring")
        self.assertEqual(status.ignored, ("keyrings.alt.file.Keyring",))
        for kind in KEY_KINDS:
            self.assertIsNone(later.get(kind, "fixture"))
            later.delete(kind, "fixture")
        self.assertEqual((kwallet.calls, plain.calls), ([], []))
        self.assertEqual(kwallet.data, saved)  # the other keychain's keys are left as they are
        self.assertEqual([op for op, _, _ in ss.calls], ["get", "delete"] * len(KEY_KINDS))

    def test_an_unusable_chosen_keychain_refuses_and_falls_through_to_nothing(self) -> None:
        for priority in (0, -1, float("nan"), float("inf"), RuntimeError("no dbus")):
            with self.subTest(priority=repr(priority)):
                if isinstance(priority, Exception):
                    exc = priority

                    class _Raises:
                        def __get__(self, obj: object, owner: object) -> float:
                            raise exc

                    dead = os_backend()
                    type(dead).priority = _Raises()
                else:
                    dead = os_backend(priority=priority)
                plain, touched = untouchable(*PLAIN)
                spare = os_backend("keyring.backends.kwallet", "kwallet DBusKeyring")
                store, status = store_for_host(PATH, loader(chain([plain, dead, spare])))
                self.assertFalse(status.available)
                self.assertTrue(status.reason.startswith("keychain_unusable"))
                self.assert_refused(store)
                self.assertEqual((dead.calls, spare.calls, touched), ([], [], []))
        store, status = store_for_host(PATH, loader(chain([untouchable(*PLAIN)[0], os_backend()])))
        self.assertTrue(status.available)


class DoctorLineTests(unittest.TestCase):
    def test_doctor_line_names_each_ignored_backend(self) -> None:
        ch = chain([untouchable(*HELPER)[0], os_backend(), untouchable(*PLAIN)[0]])
        _, status = store_for_host(PATH, loader(ch))
        result = doctor(status, None)
        self.assertEqual(result["level"], "ok")
        self.assertEqual(doctor_line(result), (
            "keys: ok - OS keychain (SecretService Keyring); model-provider, broker and venue keys in the keychain; "
            "other keyring backends ignored: review_standin.pip_credential_helper.CredentialHelper, "
            "keyrings.alt.file.PlaintextKeyring"))

    def test_hostile_backend_names_stay_on_one_line(self) -> None:
        hostile = untouchable("evil\nkeys: ok; broker in keychain\r", "Plain;text,Keyring\x1b[2J")[0]
        _, status = store_for_host(PATH, loader(chain([hostile, os_backend()])))
        line = doctor_line(doctor(status, None))
        self.assertEqual(len(line.splitlines()), 1)
        self.assertNotIn("\x1b", line)
        ignored = line.split("other keyring backends ignored: ", 1)[1]
        self.assertNotIn(";", ignored)
        self.assertNotIn(",", ignored)

    def test_a_long_list_of_ignored_backends_is_capped(self) -> None:
        extras = [untouchable(f"extra.backend{i}", "Keyring")[0] for i in range(11)]
        _, status = store_for_host(PATH, loader(chain([os_backend(), *extras])))
        self.assertEqual(len(status.ignored), 11)
        line = doctor_line(doctor(status, None))
        self.assertIn("extra.backend7.Keyring, and 3 more", line)
        self.assertNotIn("extra.backend8", line)

    def test_cli_prints_the_ignored_backends(self) -> None:
        from cockpit.keys import __main__ as cli
        _, status = store_for_host(PATH, loader(chain([untouchable(*PLAIN)[0], os_backend()])))
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
