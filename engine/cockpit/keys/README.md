# cockpit.keys: owner-only key file (CSL-59, candidate)

Governing spec: CSLR-D-011 and `contracts/PRODUCT.md` at csl-research
`055cc7a5c793b7135e5d0565dc4198cf96230531`
(SHA-256 `e64783152f6d67d4ede774dc41d112fe46c2d7536d53cb2c04e71a3566044daf`),
A44, sections 3.8 and 5.4.
Paper-only remains PRODUCT.md 5.2 / CP0-v2. This package does not place orders.

Lucas, Nov 8: "Owner-only file" (a host with no OS keychain keeps saved API keys in an owner-only
file), and for Cockpit bar 2, "Yes, model keys only".

| Key kind | Host with an OS keychain | Host with none |
|---|---|---|
| `model_provider` (user-entered BYOK) | OS keyring | owner-only file |
| `broker`, `venue` | OS keyring | refused (`keychain_required`) |

Owner-only file: a `0600` regular file in a `0700` directory, both owned by the running user,
neither a symlink. Every directory from `/` to that directory is root-owned or the running user's,
and no other user can rename an entry in it (a root-owned sticky directory such as `/tmp` may be
world-writable). Each directory component is opened with `O_DIRECTORY|O_NOFOLLOW`. The key file
and `.keys.lock` are classified with `lstat` (symlink, fifo, socket, and device are distinct from
a mode problem) and then opened with `O_NOFOLLOW|O_NONBLOCK`; the fd is checked again so a swap
between the stat and the open is still refused. Secret bytes are written only after the file mode
is `0600`. Any other mode, including `0000`, `0400`, and `0700`, is not owner-only.
The replacement is a temp file in that directory, `fsync` of the file, `rename`, then
`fsync` of the directory. If the save fails, the temp file is wiped, unlinked, and the directory
is `fsync`ed, including when `close` or `unlink` itself fails. Saves and deletes take an exclusive
`fcntl.flock` on `.keys.lock` around the read-modify-write. A key file or directory that is not
owner-only is refused on read and on write and never repaired silently. `put` and `delete` also
refuse a lock that is not owner-only. The keyring is injected; nothing here imports a keyring
library or calls a provider, venue or network. Frozen `app/` keeps its own keyring code and is
untouched.

## Open owner question: loose lock on read

`get` does not open `.keys.lock`. When the key file itself is owner-only, a read still returns the
key if the lock is missing, mode `0644`, or a symlink. `key_file_state` and `doctor` do not look
at the lock, so that case still reports `owner_only` for the key file.

Whether a read should be refused then is an owner choice. This package does not choose:

1. Keep the read when the key file is owner-only, and enforce the lock only on `put` and `delete`
   (what the code does now).
2. Refuse `get` when a lock is present and is not owner-only, and fail `doctor` for that case.
3. Refuse `get` when the lock is missing as well as when it is present and not owner-only.

`test_loose_or_symlinked_lock_is_refused_not_repaired` records today's behavior: the later `put`
is refused and the read still returns the key. That test is not a founder ruling.

Keychain detection (`detect.py`): read-only. The optional `keyring` library is loaded lazily
through an injectable loader; a host counts as having an OS keychain only when the active backend is
a real one (not the library's `fail` or `null` backend, not an empty chainer, priority above 0, and
no error from probing it). Detection never stores, reads or deletes a key (no canary round trip).
A host without the library reads as "no keychain". `store_for_host()` builds the `KeyStore` from
that answer. Candidate default file: `$XDG_CONFIG_HOME/csl-cockpit/model-keys.json`
(`~/.config/...` when unset).

Doctor line: `PYTHONPATH=engine python3 -m cockpit.keys doctor [--key-file PATH]` prints one line,
for example

```
keys: ok - OS keychain (SecretService Keyring); model-provider, broker and venue keys in the keychain
keys: warn - no OS keychain (no_keychain_backend); model-provider keys in owner-only file /home/u/.config/csl-cockpit/model-keys.json [no_file_yet]; broker and venue keys refused (keychain_required)
```

`ok` and `warn` exit 0; `fail` (key file or directory not owner-only, no file configured, or no
owner-only file on this platform) exits 1. It never prints a key or its value.

Not here yet: wiring into a Cockpit settings surface or the frozen `app/` doctor.

## Tests

```
PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/keys/tests -t engine -v
```
