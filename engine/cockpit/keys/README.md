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
world-writable). Each component is opened with `O_DIRECTORY|O_NOFOLLOW`, and the file is replaced
through that directory fd (temp file in the same directory, `fsync`, `rename`). Saves and deletes
take an exclusive `fcntl.flock` on `.keys.lock` in that directory around the read-modify-write.
A file, lock or directory that is not owner-only is refused on read and write and never repaired
silently. The keyring is injected; nothing here imports a keyring library or calls a provider,
venue or network. Frozen `app/` keeps its own keyring code and is untouched.

First save (owner-only file). Candidate default file: `$XDG_CONFIG_HOME/csl-cockpit/model-keys.json`
(`~/.config/csl-cockpit/model-keys.json` when unset). Before the first save, `get` returns `None` and
`delete` does nothing; neither creates anything. The first save creates what is missing, `0700`,
only after every directory above it passed the check through `O_NOFOLLOW` directory fds:

- the key folder itself (`csl-cockpit`, the leaf), for any configured file;
- and, only for the default file `~/.config/csl-cockpit/model-keys.json`, a missing `~/.config`, when
  the home folder is a directory owned by the running user that no one else can write to. With a
  root-owned or shared home (even a sticky one such as `/tmp`), a missing home, or a symlink anywhere
  on the path, nothing is created and the save is refused (`key_dir_missing` or
  `key_dir_not_owner_only`). Any other file location, including `$XDG_CONFIG_HOME` pointing
  elsewhere, gets no parent folder created: a missing one is refused with `key_dir_missing`.

Nothing else is ever created, and nothing is chmodded: an entry that already exists, or that another
process creates first, must pass the same check as any existing one (a `0755` `~/.config` is an
ordinary ancestor; the leaf must be `0700`). A missing or loose ancestor, a loose leaf, a symlink or a
file in the way is refused and left as it is.

Keychain detection (`detect.py`): read-only. The optional `keyring` library is loaded lazily
through an injectable loader. Detection never stores, reads or deletes a key (no canary round trip),
and a host without the library reads as "no keychain". A backend counts as an OS keychain only when
its module is exactly one of `keyring.backends.SecretService`, `libsecret`, `kwallet`, `macOS` or
`Windows` and its priority is a finite number above 0 that reads without error. The library's `fail`
and `null` backends, `keyrings.alt` and any other file, unknown or look-alike backend are not
keychains, so a plaintext keyring is never treated as one for broker or venue secrets.

When the library hands back its chain of several backends (`keyring.backends.chainer`), the chain is
read once and the first OS keychain in it is used; every other backend in the chain is ignored, never
called (not even for its priority) and named in the doctor line. A chain with no OS keychain is
refused like any other non-keychain host. `store_for_host()` builds the `KeyStore` from that answer
and binds it to the chosen OS backend object itself, never to the chain or the module, so a chain or a
global keyring that changes later cannot redirect a key.

"First" is the library's order, by priority (keyring 25.7.0: Secret Service 5, KWallet 4.9, libsecret
4.8, Windows 5, macOS 5). The five never tie, so on one host the choice is the same from run to run.
It can differ between sessions on one host: KWallet's priority is 5.1 when `XDG_CURRENT_DESKTOP`
contains `KDE`, so on a KDE host where both KWallet and Secret Service answer, the desktop session uses
KWallet and a session over SSH, in a service or in cron uses Secret Service. A key saved in one then
reads as "no key saved" in the other (nothing else is consulted), and the doctor line names the
keychain in use. `PYTHON_KEYRING_BACKEND` set to one OS backend makes every session use that one.

Doctor line: `PYTHONPATH=engine python3 -m cockpit.keys doctor [--key-file PATH]` prints one line,
for example

```
keys: ok - OS keychain (SecretService Keyring); model-provider, broker and venue keys in the keychain
keys: ok - OS keychain (Windows WinVaultKeyring); model-provider, broker and venue keys in the keychain; other keyring backends ignored: some_helper.module.CredentialHelper
keys: warn - no OS keychain (no_keychain_backend); model-provider keys in owner-only file /home/u/.config/csl-cockpit/model-keys.json [no_file_yet]; broker and venue keys refused (keychain_required)
```

Ignored backends are named as `module.Class` (at most eight, then a count), with any character
outside letters, digits and `._-<>` shown as `?`. `ok` and `warn` exit 0; `fail` (key file or
directory not owner-only, no file configured, or no owner-only file on this platform) exits 1. It
never prints a key or its value. A missing key folder or `~/.config` shows as `[dir_missing]`; the
doctor never creates it.

Not here yet: wiring into a Cockpit settings surface or the frozen `app/` doctor.

## Tests

```
PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/keys/tests -t engine -v
```

CI runs `engine/tests` (which holds `engine/tests/keys/test_app_owner_keys.py`) but not this folder;
run it by hand. `test_b13_real_library.py` uses the installed `keyring` library's own chain and OS
backend classes, with their password methods pointed at a dict, and is skipped without the library.
