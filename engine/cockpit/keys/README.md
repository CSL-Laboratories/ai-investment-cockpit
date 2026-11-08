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

Not here yet: detecting whether the host has an OS keychain (the caller passes `keyring=None`
when it has none), the `doctor` line, and wiring into a Cockpit settings surface.

## Tests

```
PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/keys/tests -t engine -v
```
