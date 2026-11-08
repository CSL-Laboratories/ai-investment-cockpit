# cockpit.keys: owner-only key file (CSL-59, candidate)

Governing spec: `contracts/PRODUCT.md` at csl-research `055cc7a5c793b7135e5d0565dc4198cf96230531`
(SHA-256 `e64783152f6d67d4ede774dc41d112fe46c2d7536d53cb2c04e71a3566044daf`), A44, sections 3.8 and 5.4.

Lucas, Nov 8: "Owner-only file" (a host with no OS keychain keeps saved API keys in an owner-only
file), and for Cockpit bar 2, "Yes, model keys only".

| Key kind | Host with an OS keychain | Host with none |
|---|---|---|
| `model_provider` (user-entered BYOK) | OS keyring | owner-only file |
| `broker`, `venue` | OS keyring | refused (`keychain_required`) |

Owner-only file: a `0600` regular file in a `0700` directory, both owned by the running user,
neither a symlink, written atomically (temp file in the same directory, `fsync`, `os.replace`).
A file or directory that is not owner-only is refused on read and write and never repaired
silently. The keyring is injected; nothing here imports a keyring library or calls a provider,
venue or network. Frozen `app/` keeps its own keyring code and is untouched.

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
