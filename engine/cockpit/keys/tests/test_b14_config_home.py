"""B14: the first save on an account with no ``~/.config`` (CSL-59).

Recommendation B14, accepted by Lucas on Nov 12, 2026 ("I agree with all B10-B14 Recommendations"):
"also create `~/.config` when it is missing, owner-only, and only when the home folder is the user's own".
Created ``0700``, only for the default file ``~/.config/csl-cockpit/model-keys.json``, only when the home
folder is the user's, not group- or world-writable, and nothing on the path is a symlink; loose
permissions are never repaired and nothing is created anywhere else.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/keys/tests -t engine -v``.
Temp directories and made-up keys only; no real keyring, provider, venue or network.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import threading
import unittest
from collections.abc import Callable
from unittest import mock

from cockpit.keys import KeychainStatus, KeyRefused, KeyStore, doctor, store_for_host
from cockpit.keys.tests.fakes import FAKE

POSIX = os.name == "posix" and hasattr(os, "O_NOFOLLOW")


def _absent() -> object:
    raise ImportError("fixture has no OS keychain")


def _mode(path: str) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


@unittest.skipUnless(POSIX, "owner-only file requires POSIX")
class FreshAccountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="cockpit-b14-")
        self.addCleanup(self._remove, self.root)
        self.home = os.path.join(self.root, "home")
        os.mkdir(self.home, 0o700)
        self.config = os.path.join(self.home, ".config")
        self.leaf = os.path.join(self.config, "csl-cockpit")
        self.file = os.path.join(self.leaf, "model-keys.json")

    @staticmethod
    def _remove(top: str) -> None:
        for root, dirs, _ in os.walk(top):
            for name in dirs:
                path = os.path.join(root, name)
                if not os.path.islink(path):
                    os.chmod(path, 0o700)
        os.chmod(top, 0o700)
        shutil.rmtree(top, True)

    def store(self, key_file: str | None = None, **env: str) -> KeyStore:
        environ = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
        environ["HOME"] = self.home
        environ.update(env)
        with mock.patch.dict(os.environ, environ, clear=True):
            store, status = store_for_host(key_file, load=_absent)
        self.assertFalse(status.available)
        return store

    def refused(self, cause: str | None, fn: Callable[..., object], *args: object) -> KeyRefused:
        with self.assertRaises(KeyRefused) as ctx:
            fn(*args)
        if cause is not None:
            self.assertEqual(ctx.exception.cause, cause)
        self.assertNotIn(FAKE, str(ctx.exception))
        return ctx.exception

    def every_operation_refused(self, store: KeyStore, cause: str | None) -> None:
        self.refused(cause, store.get, "model_provider", "openai")
        self.refused(cause, store.delete, "model_provider", "openai")
        self.refused(cause, store.put, "model_provider", "openai", FAKE)

    def assert_saved_owner_only(self, store: KeyStore, config_mode: int = 0o700) -> None:
        self.assertEqual(store.put("model_provider", "openai", FAKE), "owner_only_file")
        self.assertEqual(store.get("model_provider", "openai"), FAKE)
        for path, mode in ((self.config, config_mode), (self.leaf, 0o700), (self.file, 0o600),
                           (os.path.join(self.leaf, ".keys.lock"), 0o600)):
            st = os.lstat(path)
            self.assertEqual((stat.S_IMODE(st.st_mode), st.st_uid), (mode, os.geteuid()), path)
        self.assertTrue(stat.S_ISDIR(os.lstat(self.config).st_mode))

    def umask(self, value: int) -> None:
        old = os.umask(value)
        self.addCleanup(os.umask, old)

    # -- the fresh account ---------------------------------------------------------------------
    def test_first_save_creates_config_and_leaf_owner_only_at_any_umask(self) -> None:
        for value in (0o022, 0o000, 0o077, 0o027):
            with self.subTest(umask=oct(value)):
                old = os.umask(value)
                try:
                    if os.path.lexists(self.config):
                        shutil.rmtree(self.config)
                    store = self.store()
                    self.assertIsNone(store.get("model_provider", "openai"))
                    store.delete("model_provider", "openai")
                    self.assertEqual(os.listdir(self.home), [])  # neither created anything
                    result = doctor(KeychainStatus(False, "", "no_keychain_backend"), store._path)
                    self.assertEqual((result["level"], result["key_file"]), ("warn", "dir_missing"))
                    self.assertEqual(os.listdir(self.home), [])
                    self.assert_saved_owner_only(store)
                    self.assertEqual(_mode(self.home), 0o700)
                    store.delete("model_provider", "openai")
                    self.assertIsNone(store.get("model_provider", "openai"))
                finally:
                    os.umask(old)

    def test_both_new_folders_are_owner_only_at_the_moment_they_are_made(self) -> None:
        # Codex's read of #326: making either folder wider (0755 or 0777) and tightening it afterwards
        # passed every other test, which only look at the final mode. Here each folder's mode is read
        # right after mkdir returns, before the store can touch it, at umask 000; chmod and chown on a
        # folder would fail the test outright.
        real_mkdir = os.mkdir
        born: list[tuple[str, int, int]] = []

        def observing_mkdir(name: str, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
            real_mkdir(name, mode, dir_fd=dir_fd)
            born.append((name, mode, stat.S_IMODE(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)))

        self.umask(0o000)
        with mock.patch("cockpit.keys.store.os.mkdir", side_effect=observing_mkdir), \
                mock.patch("cockpit.keys.store.os.chmod", side_effect=AssertionError("a folder was chmodded")), \
                mock.patch("cockpit.keys.store.os.chown", side_effect=AssertionError("a folder was chowned")):
            store = self.store()
            self.assertIsNone(store.get("model_provider", "openai"))
            store.delete("model_provider", "openai")
            self.assertEqual(born, [])  # neither read nor delete makes a folder
            self.assertEqual(store.put("model_provider", "openai", FAKE), "owner_only_file")
        self.assertEqual(born, [(".config", 0o700, 0o700), ("csl-cockpit", 0o700, 0o700)])
        self.assert_saved_owner_only(store)

    def test_a_home_at_0755_or_0750_is_the_users_own(self) -> None:
        for mode in (0o755, 0o750, 0o711, 0o700):
            with self.subTest(mode=oct(mode)):
                if os.path.lexists(self.config):
                    shutil.rmtree(self.config)
                os.chmod(self.home, mode)
                self.assert_saved_owner_only(self.store())
                self.assertEqual(_mode(self.home), mode)

    # -- the home folder ------------------------------------------------------------------------
    def test_home_writable_by_others_gets_nothing_and_is_never_repaired(self) -> None:
        for mode in (0o770, 0o775, 0o777, 0o757, 0o720, 0o702, 0o1777):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.home, mode)
                self.every_operation_refused(self.store(), "key_dir_not_owner_only")
                self.assertEqual(_mode(self.home), mode)
                self.assertEqual(os.listdir(self.home), [])
        os.chmod(self.home, 0o700)  # the user fixes it; Cockpit never does
        self.assert_saved_owner_only(self.store())

    def test_home_that_is_not_the_users_own_gets_nothing(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root owns every root-owned home")
        candidates = [path for path in ("/", tempfile.gettempdir())
                      if os.stat(path).st_uid == 0 and not os.path.lexists(os.path.join(path, ".config"))]
        if not candidates:
            self.skipTest("no root-owned folder without a .config here")
        for home in candidates:
            # Only a wrong implementation could create it; never leave one behind in /tmp.
            self.addCleanup(lambda p=os.path.join(home, ".config"): os.path.lexists(p) and shutil.rmtree(p, True))
            with self.subTest(home=home):
                store = self.store(HOME=home)
                for operation in ((store.get, "model_provider", "openai"), (store.delete, "model_provider", "openai"),
                                  (store.put, "model_provider", "openai", FAKE)):
                    exc = self.refused("key_dir_missing", *operation)
                    self.assertIn("home folder is not the user's own", str(exc))
                self.assertFalse(os.path.lexists(os.path.join(home, ".config")))
        self.assert_saved_owner_only(self.store())

    def test_read_only_home_is_refused_and_left_as_it_is(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root can write to a read-only folder")
        for mode in (0o500, 0o555, 0o100):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.home, mode)
                store = self.store()
                if mode & 0o400:
                    self.assertIsNone(store.get("model_provider", "openai"))
                exc = self.refused("key_dir_not_owner_only", store.put, "model_provider", "openai", FAKE)
                self.assertIn("EACCES", str(exc))
                self.assertEqual(_mode(self.home), mode)
                os.chmod(self.home, 0o700)
                self.assertEqual(os.listdir(self.home), [])
        self.assert_saved_owner_only(self.store())

    def test_a_read_only_walk_never_creates_or_skips_config(self) -> None:
        # The doctor's walk asks neither to create nor to tolerate a missing folder; a home folder
        # passed to it changes nothing: a missing .config is still reported, and nothing is made.
        from cockpit.keys import store as store_mod
        with self.assertRaises(KeyRefused) as ctx:
            store_mod._open_owned_dir(self.leaf, config_home=self.home)
        self.assertEqual(ctx.exception.cause, "key_dir_missing")
        self.assertEqual(os.listdir(self.home), [])
        self.assert_saved_owner_only(self.store())

    def test_missing_home_is_never_created(self) -> None:
        os.rmdir(self.home)
        self.every_operation_refused(self.store(), "key_dir_missing")
        self.assertFalse(os.path.lexists(self.home))
        os.mkdir(self.home, 0o700)
        self.assert_saved_owner_only(self.store())

    # -- symlinks and files in the way -------------------------------------------------------
    def test_symlink_anywhere_on_the_path_is_refused_and_nothing_is_created(self) -> None:
        real = os.path.join(self.root, "real")
        os.mkdir(real, 0o700)
        os.mkdir(os.path.join(real, "home"), 0o700)
        os.symlink(os.path.join(real, "home"), os.path.join(self.root, "home-link"))
        os.symlink(real, os.path.join(self.root, "parent-link"))
        for home in (os.path.join(self.root, "home-link"), os.path.join(self.root, "parent-link", "home")):
            with self.subTest(home=home):
                self.every_operation_refused(self.store(HOME=home), "key_dir_not_owner_only")
                self.assertEqual(os.listdir(os.path.join(real, "home")), [])
        target = os.path.join(self.root, "elsewhere")
        os.mkdir(target, 0o700)
        for link_to in (target, os.path.join(self.root, "dangling")):
            with self.subTest(config_link=link_to):
                os.symlink(link_to, self.config)
                self.every_operation_refused(self.store(), "key_dir_not_owner_only")
                self.assertTrue(os.path.islink(self.config))
                self.assertEqual(os.listdir(target), [])
                self.assertFalse(os.path.lexists(os.path.join(self.root, "dangling")))
                os.unlink(self.config)
        self.assert_saved_owner_only(self.store())

    def test_file_in_the_way_is_refused_and_left_alone(self) -> None:
        with open(self.config, "w") as fh:
            fh.write("not a folder")
        os.chmod(self.config, 0o600)
        self.every_operation_refused(self.store(), "key_dir_not_owner_only")
        with open(self.config) as fh:
            self.assertEqual(fh.read(), "not a folder")
        os.unlink(self.config)
        self.assert_saved_owner_only(self.store())

    def test_existing_loose_config_is_refused_not_repaired(self) -> None:
        os.mkdir(self.config)
        for mode in (0o777, 0o775, 0o757):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.config, mode)
                self.every_operation_refused(self.store(), "key_dir_not_owner_only")
                self.assertEqual(_mode(self.config), mode)
                self.assertEqual(os.listdir(self.config), [])
        os.chmod(self.config, 0o755)  # an existing ~/.config at 0755 is an ordinary ancestor
        self.assert_saved_owner_only(self.store(), config_mode=0o755)
        shutil.rmtree(self.config)  # the user removes it; the next first save makes a 0700 one
        self.assert_saved_owner_only(self.store())

    # -- a competing creator between the check and the mkdir --------------------------------------
    def test_competing_creator_of_config_is_rechecked(self) -> None:
        real_mkdir = os.mkdir
        target = os.path.join(self.root, "target")
        os.mkdir(target, 0o700)

        def make_dir(mode: int) -> Callable[[int], None]:
            def create(dir_fd: int) -> None:
                real_mkdir(".config", mode, dir_fd=dir_fd)
                os.chmod(".config", mode, dir_fd=dir_fd)  # fixture mode must not depend on umask
            return create

        def make_file(dir_fd: int) -> None:
            os.close(os.open(".config", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dir_fd))

        def make_vanishing(dir_fd: int) -> None:
            real_mkdir(".config", 0o700, dir_fd=dir_fd)
            os.rmdir(".config", dir_fd=dir_fd)

        cases: list[tuple[str, Callable[[int], None], bool, int | None]] = [
            ("0700 folder", make_dir(0o700), True, 0o700),
            ("0755 folder, like any existing ~/.config", make_dir(0o755), True, 0o755),
            ("0775 folder", make_dir(0o775), False, 0o775),
            ("0777 folder", make_dir(0o777), False, 0o777),
            ("symlink to the user's own folder", lambda fd: os.symlink(target, ".config", dir_fd=fd), False, None),
            ("dangling symlink", lambda fd: os.symlink(os.path.join(self.root, "nowhere"), ".config", dir_fd=fd),
             False, None),
            ("regular file", make_file, False, 0o600),
            ("folder that vanishes again", make_vanishing, False, None),
        ]
        for label, compete, accepted, left_mode in cases:
            with self.subTest(label):
                calls: list[str] = []

                def competing_mkdir(name: str, mode: int = 0o777, *, dir_fd: int | None = None,
                                    compete: Callable[[int], None] = compete) -> None:
                    calls.append(name)
                    if name != ".config":
                        return real_mkdir(name, mode, dir_fd=dir_fd)
                    compete(dir_fd)  # type: ignore[arg-type]
                    raise FileExistsError("fixture competing creator")

                before = os.lstat(self.config) if os.path.lexists(self.config) else None
                self.assertIsNone(before)
                with mock.patch("cockpit.keys.store.os.mkdir", side_effect=competing_mkdir):
                    store = self.store()
                    if accepted:
                        self.assertEqual(store.put("model_provider", "openai", FAKE), "owner_only_file")
                    else:
                        self.refused("key_dir_not_owner_only", store.put, "model_provider", "openai", FAKE)
                self.assertEqual(calls[0], ".config")
                if left_mode is not None:  # the competitor's entry is never chmodded or repaired
                    self.assertEqual(_mode(self.config), left_mode)
                if accepted:
                    self.assertEqual(_mode(self.leaf), 0o700)
                    self.assertEqual(self.store().get("model_provider", "openai"), FAKE)
                elif os.path.lexists(self.config) and not os.path.islink(self.config) and os.path.isdir(self.config):
                    self.assertEqual(os.listdir(self.config), [])  # the leaf was not created inside it
                self.assertEqual(os.listdir(target), [])
                if os.path.islink(self.config) or os.path.isfile(self.config):
                    os.unlink(self.config)
                elif os.path.lexists(self.config):
                    self._remove(self.config)

    # -- only the default location --------------------------------------------------------------
    def test_only_the_default_location_gets_a_config_folder(self) -> None:
        elsewhere = os.path.join(self.home, "elsewhere")
        refused = {
            "XDG_CONFIG_HOME elsewhere": (None, {"XDG_CONFIG_HOME": elsewhere}),
            "another app's folder": (os.path.join(self.config, "other-app", "model-keys.json"), {}),
            "another file name": (os.path.join(self.leaf, "other.json"), {}),
            "a deeper folder": (os.path.join(self.leaf, "deeper", "model-keys.json"), {}),
            "a relative HOME": (None, {"HOME": os.path.relpath(self.home)}),
        }
        for label, (key_file, env) in refused.items():
            with self.subTest(label):
                self.every_operation_refused(self.store(key_file, **env), "key_dir_missing")
                self.assertEqual(os.listdir(self.home), [])
        direct = KeyStore(keyring=None, key_file=self.file)  # no config_home: #323's rule, unchanged
        self.every_operation_refused(direct, "key_dir_missing")
        self.assertEqual(os.listdir(self.home), [])
        # Given a home, the store itself still creates nothing but ``<home>/.config`` and the leaf.
        for key_file in (os.path.join(self.root, "elsewhere", "leaf", "k.json"),
                         os.path.join(self.leaf, "deeper", "k.json")):
            with self.subTest(direct_with_home=key_file):
                store = KeyStore(keyring=None, key_file=key_file, config_home=self.home)
                self.every_operation_refused(store, "key_dir_missing")
                self.assertEqual(sorted(os.listdir(self.root)), ["home"])
                self.assertEqual(os.listdir(self.home), [])
        allowed = {
            "default": (None, {}),
            "XDG_CONFIG_HOME is ~/.config": (None, {"XDG_CONFIG_HOME": self.config}),
            "HOME with a trailing slash": (None, {"HOME": self.home + os.sep}),
            "the default file passed explicitly, as the app does": (self.file, {}),
        }
        for label, (key_file, env) in allowed.items():
            with self.subTest(label):
                self.assert_saved_owner_only(self.store(key_file, **env))
                shutil.rmtree(self.config)

    # -- concurrency and descriptors ------------------------------------------------------------
    def test_simultaneous_first_saves_on_a_fresh_account_keep_every_key(self) -> None:
        for _ in range(10):
            if os.path.lexists(self.config):
                shutil.rmtree(self.config)
            store = self.store()
            names = [f"k{i:02d}" for i in range(12)]
            barrier = threading.Barrier(len(names))
            errors: list[BaseException] = []

            def work(name: str, store: KeyStore = store) -> None:
                try:
                    barrier.wait(timeout=10)
                    store.put("model_provider", name, FAKE + name)
                except BaseException as exc:  # noqa: BLE001 - reported below
                    errors.append(exc)

            threads = [threading.Thread(target=work, args=(name,)) for name in names]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertEqual({name: store.get("model_provider", name) for name in names},
                             {name: FAKE + name for name in names})
            self.assertEqual((_mode(self.config), _mode(self.leaf)), (0o700, 0o700))

    @unittest.skipUnless(os.path.isdir("/proc/self/fd"), "needs /proc/self/fd")
    def test_no_descriptor_leaks_on_any_path(self) -> None:
        def open_fds() -> int:
            return len(os.listdir("/proc/self/fd"))

        root_home = os.geteuid() != 0 and os.stat("/").st_uid == 0 and not os.path.lexists("/.config")
        before = open_fds()
        for _ in range(30):
            if os.path.lexists(self.config):
                shutil.rmtree(self.config)
            os.chmod(self.home, 0o700)
            store = self.store()
            store.get("model_provider", "openai")
            store.delete("model_provider", "openai")
            store.put("model_provider", "openai", FAKE)
            shutil.rmtree(self.config)
            os.chmod(self.home, 0o775)
            for op in ((store.get, "model_provider", "openai"), (store.put, "model_provider", "openai", FAKE)):
                self.refused(None, *op)
            os.chmod(self.home, 0o700)
            os.symlink(self.root, self.config)
            self.refused(None, store.put, "model_provider", "openai", FAKE)
            os.unlink(self.config)
            if root_home:  # a home that is not the user's (never run as root: root owns "/")
                self.refused(None, self.store(HOME="/").put, "model_provider", "openai", FAKE)
        self.assertEqual(open_fds(), before)


if __name__ == "__main__":
    unittest.main()
