"""R6: secure leaf-only initialization, authorized by Lucas for CSL-59."""
import os
import stat
import tempfile
import unittest
from unittest import mock

from cockpit.keys import store_for_host


@unittest.skipUnless(os.name == "posix", "owner-only file requires POSIX")
class FreshHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.xdg = os.path.join(self.tmp.name, "config")
        os.mkdir(self.xdg, 0o700)
        def absent(): raise ImportError("fixture has no OS keychain")
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.xdg}):
            self.store, _ = store_for_host(load=absent)
    def test_unsaved_lookup_returns_none(self):
        self.assertIsNone(self.store.get("model_provider", "openai"))
        self.assertFalse(os.path.exists(os.path.dirname(self.store._path)))
    def test_delete_unsaved_key_is_a_noop(self):
        self.store.delete("model_provider", "openai")
        self.assertFalse(os.path.exists(self.store._path))
        self.assertFalse(os.path.exists(os.path.dirname(self.store._path)))
    def test_first_save_creates_private_directory(self):
        self.store.put("model_provider", "openai", "fake-r6-key")
        self.assertEqual(self.store.get("model_provider", "openai"), "fake-r6-key")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.store._path)).st_mode), 0o700)

    def test_missing_ancestor_is_never_created_or_treated_as_empty(self):
        from cockpit.keys import KeyRefused
        os.rmdir(self.xdg)
        for operation in (lambda: self.store.get("model_provider", "openai"),
                          lambda: self.store.delete("model_provider", "openai"),
                          lambda: self.store.put("model_provider", "openai", "fake-r6-key")):
            with self.assertRaises(KeyRefused) as cm:
                operation()
            self.assertEqual(cm.exception.cause, "key_dir_missing")
            self.assertFalse(os.path.exists(self.xdg))

    def test_loose_ancestor_or_leaf_is_never_repaired(self):
        from cockpit.keys import KeyRefused
        leaf = os.path.dirname(self.store._path)
        for directory, mode in ((self.xdg, 0o777), (leaf, 0o755)):
            if directory == leaf:
                os.chmod(self.xdg, 0o700)
                os.mkdir(leaf, mode)
            os.chmod(directory, mode)
            for operation in (lambda: self.store.get("model_provider", "openai"),
                              lambda: self.store.delete("model_provider", "openai"),
                              lambda: self.store.put("model_provider", "openai", "fake-r6-key")):
                with self.assertRaises(KeyRefused):
                    operation()
                self.assertEqual(stat.S_IMODE(os.stat(directory).st_mode), mode)
                self.assertFalse(os.path.exists(self.store._path))
                if directory == self.xdg:
                    self.assertFalse(os.path.exists(leaf))

    def test_competing_leaf_creation_is_rechecked(self):
        from cockpit.keys import KeyRefused
        leaf = os.path.dirname(self.store._path)
        real_mkdir = os.mkdir
        for mode in (0o700, 0o755):
            def competing_mkdir(name, requested_mode, *, dir_fd, mode=mode):
                real_mkdir(name, mode, dir_fd=dir_fd)
                os.chmod(name, mode, dir_fd=dir_fd)  # Fixture mode must not depend on umask.
                raise FileExistsError("fixture competing creator")

            with mock.patch("cockpit.keys.store.os.mkdir", side_effect=competing_mkdir):
                if mode == 0o700:
                    self.store.put("model_provider", "openai", "fake-r6-key")
                else:
                    with self.assertRaises(KeyRefused):
                        self.store.put("model_provider", "openai", "fake-r6-key")
            self.assertEqual(stat.S_IMODE(os.stat(leaf).st_mode), mode)
            import shutil
            shutil.rmtree(leaf)
