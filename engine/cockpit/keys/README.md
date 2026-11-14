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

Not a regular file (B29; Lucas, Nov 13, 2026: "Fix it in #330 (Recommended)"). Anything at the key file
or lock path that is not a regular file (a pipe, a socket, a device or a folder) is refused
(`key_file_not_owner_only`, the detail naming what it is) without being opened: the store decides from
`lstat` first, opens with `O_NOFOLLOW|O_NONBLOCK` so an entry swapped in after that check cannot make it
wait, and checks that the open file is the same regular file (device and inode; a file removed and made
again in between can get the same inode number, so a swap is caught while the checked file still exists).
The keychain record (below) is opened the same way (`keychain_record_not_owner_only`). Folders on the way
are opened with `O_DIRECTORY`, which refuses anything else without opening it, and the doctor only looks
(`lstat`). A key file named like the record or the lock makes the doctor `fail` with
`key_file_not_configured`.

Key names and the file (B23; Lucas, Nov 13, 2026: "Fix before landing (Recommended)"). A key name is 1
to 64 characters of `a-z`, `0-9`, `.`, `_` and `-`, starting with a letter or a digit, matched against the
whole name, so a name ending in a line break is refused. A malformed name supplied as an argument is
refused with `key_name_malformed`; a malformed name stored in the key file makes the file
`key_file_malformed`. The file must say `"version": 1` as an integer: `true` or `1.0` makes it
malformed (`key_file_malformed`), though both compare equal to 1 in Python. A field given twice (two
`version`s, or one key name twice) makes the file malformed too, as it does the keychain record, instead of
the last one winning; any other error while reading the file stops the operation.

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
its type *is* one of the library's own classes, imported from the library: `SecretService.Keyring`,
`kwallet.DBusKeyring` (KWallet 5), `libsecret.Keyring`, `kwallet.DBusKeyringKWallet4` (KWallet 4),
`macOS.Keyring` or `Windows.WinVaultKeyring` (exact identity, so not a subclass, not a proxy, and not
another class that copies a module or class name), and its priority, read from that class, is a finite
number above 0. A class whose module will not import on this host is a keychain this host does not
have. The library's `fail` and `null` backends, `keyrings.alt` and any other file, unknown or
look-alike backend are not keychains, so a plaintext keyring is never treated as one for broker or
venue secrets. When the refused backend looks meant as a supported keychain, the doctor says why:
`unsupported_keychain_class:<module.Class>` (it derives from, or carries the module and class name of, a
supported class but is not that class, as a distribution's own subclass would) or
`keychain_module_unavailable:<module>:<ExceptionType>` (its class names a supported keychain whose module
failed to import here; the exception's message is never shown). The library's chain gets the same two:
`unsupported_keyring_chain:<module.Class>` (it derives from, or carries the module and class name of, the
library's `ChainerBackend` but is not that class; Cockpit does not read its members) or
`keychain_module_unavailable:keyring.backends.chainer:<ExceptionType>`. The library's own chain found
inside the chain Cockpit reads gives `unsupported_nested_keyring_chain`; Cockpit does not read the inner
chain. Another module failing to import, such as another platform's keychain, is not reported when nothing
claims it.

When the library hands back its own chain of several backends (`keyring.backends.chainer`'s class,
also by identity), the chain is read once and one OS keychain in it is used; every other backend in
the chain is ignored and named in the doctor line, and Cockpit sends it no password operation. What
this does not do: stop the library's own discovery from running a backend's code. To build the chain,
the library reads every member's priority (in 25.7.0, twice: to filter and to sort), and its
`get_keyring()` may first initialize installed plugins. A priority that raises there makes detection
fail closed ("no keychain"). A chain with no OS keychain is refused like any other non-keychain host.
`store_for_host()` builds the `KeyStore` from that answer and binds it to the chosen backend object and
its genuine class: every save, read and delete calls that class's own top-level password method with
the object. That protects against a global keyring or a chain replaced after the check and against a
top-level password method shadowed on the instance, and a reassigned `__class__` is refused at use
(`keychain_changed`). It does no more: the class's methods still consult the object (in keyring 25.7.0,
Secret Service's call `self.get_preferred_collection()`), so code that changes the selected object's
helpers or state after the check can redirect a key.

What this cannot defend against: hostile code already running in the Python process. A replaced import
of a keyring module, a monkey-patched genuine class, a changed helper, state or class of the selected
object, or a malicious plugin that the library initializes can do anything this code can; the runtime
and its installed packages have to be trusted.

When the chain holds several OS keychains, Cockpit uses the first in its own fixed order: Secret
Service, KWallet 5, libsecret, KWallet 4 (macOS and Windows are never chained with these). Lucas, Nov
12, 2026, asked "On KDE, should Cockpit use the same keychain in every session (a fixed order)?": "Yes,
fixed order". That is keyring 25.7.0's order by priority (5, 4.9, 4.8, 3.9) everywhere except where
`XDG_CURRENT_DESKTOP` contains `KDE` (normally a KDE desktop session), where the library raises KWallet 5
to 5.1, above Secret Service's 5 (and KWallet 4 to 4.1, still below libsecret). With the library's order
one KDE host would use KWallet in its desktop session and Secret Service over SSH, in a service or in
cron; with the fixed order every session on that host uses Secret Service, as long as the sessions see
the same set of available keychain classes and the same explicit backend setting. `PYTHON_KEYRING_BACKEND`
(or `keyringrc.cfg`) set to one OS backend makes every session that has it use that one.

A change of keychain (B24). The order is fixed, but which keychains answer can still differ between
sessions or library versions (a daemon reachable only on the desktop). Lucas, Nov 13, 2026, asked what
Cockpit should do when a later run selects a different keychain from the one it saved a broker or venue
key in: "Remember and refuse (Recommended)". So Cockpit remembers which keychain holds broker and venue
keys:

- **The record.** The first broker or venue save on a keychain host writes `.keychain.json` beside the key
  file (by default `~/.config/csl-cockpit/.keychain.json`), under the key file's own rules: the same
  owned chain of folders, `0600` in `0700`, owned by the running user, no link followed, replaced
  atomically under `.keys.lock`, never repaired. It holds only Cockpit's own name for the keychain, the one
  the doctor shows: `{"version": 1, "keychain": "SecretService Keyring"}`, plus `"previous"` after an
  accepted switch. Never a key. Cockpit also writes one non-secret marker into that keychain (service
  `csl-cockpit`, account `cockpit-keychain-record`, the keychain's name as its value), so that while the
  marker is there a record deleted later is not taken for "never recorded". The marker is one per keychain
  and the record one per key folder, so a second key folder (another `--key-file`) on the same keychain is
  refused (`keychain_record_missing`) until `accept-keychain` is run for it. A key file named
  `.keychain.json` or `.keys.lock` is refused (`key_file_not_configured`) for broker and venue operations and
  `accept-keychain` on a keychain host too, and the doctor fails it; model-provider keys in a keychain do not
  use the key file.
- **Order and interruptions.** The marker is written first and the record second, for the first save and
  for `accept-keychain`, and the marker is never taken back: an error from the record write can come after
  the record was already published (the folder flush after the rename), and the marker is shared by every
  key folder on that keychain. The two writes are in two different stores, so this is an order, not a
  transaction, and an error does not tell which state was reached:
  - the marker write fails: nothing was written, and a plain retry works;
  - a **first save** whose record was not published (a write that failed before its rename, or a process
    killed between the two writes): the marker stays alone, the next broker or venue operation is refused
    (`keychain_record_missing`, which says a first save may have been cut off), and `accept-keychain`
    records it; the key folder and `.keys.lock` it made stay;
  - a first save whose record was published before the error: record and marker are both there, and a
    plain retry works;
  - an **acceptance that replaces a record** and fails before publishing: the old record is still there, so
    the selected keychain is still refused (`keychain_differs`) until `accept-keychain` is run again; after
    publishing, the record already names the new keychain (with `"previous"`), and running it again keeps
    that history.
  A matching record without a marker (left by a candidate build of this change, by a marker removed by
  hand, or by a marker write that never took effect) stays valid, and the next broker or venue save adds
  the marker if the keychain answers.
- **Calls in flight.** Each broker or venue operation checks the record when it starts and then uses the
  keychain it checked. Saves and `accept-keychain` hold `.keys.lock` from that check to their last write, so
  a save never runs across an acceptance; a read or a delete already past its check when an acceptance
  changes the record finishes against the keychain it checked. Every operation that starts after
  `accept-keychain` returns sees the new record.
- **Same keychain.** Two runs have the same keychain when the admitted class is the same entry of the
  fixed list (identity, as in admission); the record compares Cockpit's name for that entry.
- **Refused, never re-recorded.** Broker and venue `get`, `put` and `delete` are refused when the record
  names another keychain (`keychain_differs`), when the keychain holds the marker but the record is gone
  (`keychain_record_missing`), when the record is malformed (`keychain_record_malformed`: not JSON, another
  field, a `version` that is not the integer 1, a name not in the fixed list, over 4 KiB), a link, a folder,
  a FIFO, another user's or not owner-only (`keychain_record_not_owner_only`), or cannot be read
  (`keychain_record_unreadable`), and when a folder on the way is not owner-only (`key_dir_not_owner_only`).
  On a record refusal no broker or venue key is read, written or deleted; when the record is absent,
  Cockpit may first read its non-secret marker. A content error of the record (bad UTF-8 or JSON, a field
  given twice, nesting too deep) is `keychain_record_malformed`; any other error while reading it (out of
  memory, an internal error) stops the operation and is never called malformed, so `accept-keychain` never
  replaces a valid record because of one. If the record cannot be written, the save is refused and no key
  is stored, though the record itself may have been published (see above).
- **The way out**, named in the record refusals (`keychain_differs`, `keychain_record_missing`, and a
  malformed, loose, linked or unreadable record; a loose folder says only that Cockpit never repairs it):
  make the recorded keychain available
  again, or record the change with `python -m cockpit.keys accept-keychain "<name>"`, where the name is
  exactly the keychain this run selected. It runs only at an interactive terminal, keeps the old keychain
  as `"previous"`, and from then on the doctor line says the keychain was switched. It replaces a record
  that is malformed; a link or a loose record is refused, and you remove it yourself. Code running as the
  same user can still rewrite the record, as it can read the keychain itself: the step makes a switch
  deliberate and visible, not impossible. If it stops on an unexpected error (the keychain or the disk
  fails), it says it did not finish and shows only that error's type; a refusal prints its own text, which
  can name the configured folder.
- **The doctor** (B37; Lucas, Nov 13, 2026: "Note + opt-in check (Recommended)"). The plain
  `python -m cockpit.keys doctor` never contacts the keychain: it reads the key folder and the record file
  only. It names the way out for a record that names another keychain or is broken. With no record on a
  keychain host it adds one clause, "no keychain record yet (if broker or venue keys are refused as
  keychain_record_missing, run python -m cockpit.keys accept-keychain '<name>'; python -m cockpit.keys
  doctor --check-keychain looks in the keychain)", and stays `ok`, since it cannot tell a host that has
  saved no broker or venue key from one whose record is gone. `doctor --check-keychain` (opt-in) reads
  Cockpit's marker in the keychain when the record is absent: marker there, `fail` with
  `keychain_record_missing` and the way out; no marker, the line says so. That is a keychain read, so it
  can show the keychain's unlock prompt or wait for it (Secret Service waits on a locked collection's
  prompt; KWallet asks for the wallet's password). If the read fails or is refused, the line says the check
  did not finish and shows only the error's type, never its message.
- **Nothing to compare.** With no record and no marker (keys saved before this rule, or none yet), reads
  and deletes work as before and create nothing; the next broker or venue save records the keychain
  selected then. If that run selected a different keychain from the one older keys are in, Cockpit cannot
  know.
- **Not covered.** Model-provider keys are neither recorded nor refused: on a keychain host a run that
  selects another keychain reads what that keychain holds (nothing, or an older key), and in the
  owner-only file nothing changes. Windows keeps no record, since this module has no owner-only file there
  and only the Windows vault can be selected there. macOS works as Linux does. A `KeyStore` built directly
  with a keyring but without `keychain=` keeps no record, because only detection knows which keychain was
  admitted; `store_for_host`, the one way the product builds a store (the app's `OwnerKeys.for_host` uses
  it), always passes it.

Doctor line: `PYTHONPATH=engine python3 -m cockpit.keys doctor [--key-file PATH] [--check-keychain]` prints one line,
for example

```
keys: ok - OS keychain (SecretService Keyring); model-provider, broker and venue keys in the keychain
keys: ok - OS keychain (Windows WinVaultKeyring); model-provider, broker and venue keys in the keychain; other keyring backends ignored: some_helper.module.CredentialHelper
keys: fail - OS keychain (kwallet DBusKeyring); model-provider keys in the keychain; broker and venue keys refused (keychain_differs): saved in SecretService Keyring, this run selected kwallet DBusKeyring; to keep broker and venue keys in kwallet DBusKeyring, run at a terminal: python -m cockpit.keys accept-keychain 'kwallet DBusKeyring'; or make SecretService Keyring available again
keys: warn - no OS keychain (no_keychain_backend); model-provider keys in owner-only file /home/u/.config/csl-cockpit/model-keys.json [no_file_yet]; broker and venue keys refused (keychain_required)
```

Ignored backends are named as `module.Class` (at most eight, then a count), with any character
outside letters, digits and `._-<>` shown as `?`. `ok` and `warn` exit 0; `fail` (key file or
directory not owner-only, no file configured, or no owner-only file on this platform) exits 1. It
reads no stored key or value. A missing key folder or `~/.config` shows as `[dir_missing]`; the doctor
never creates it.

What the doctor line shows of a backend, and the one rule for it. The selected keychain is shown by
Cockpit's own name for its class (`SecretService Keyring`, `kwallet DBusKeyring`, `libsecret Keyring`,
`kwallet DBusKeyringKWallet4`, `macOS Keyring`, `Windows WinVaultKeyring`), never by anything the class
says about itself. Every other name comes from a backend: an ignored backend, and the refused class in
`unsupported_keychain_class` or `unsupported_keyring_chain`, are named by that class's module and class
name; `keychain_module_unavailable`, `keychain_unusable`, `keyring_backend_error` and
`keyring_library_error` name an exception's class. Cockpit reads those names from the interpreter's own
type fields, so no metaclass hook of the class runs, and cuts each to one line of letters, digits and
`._-<>` (anything else shown as `?`) and at most 120 characters. It does not read stored values or
exception messages to build the line, and it does not inspect what a name says: the backend's code chose
the name and can put any text in it, a made-up path or a key-shaped string included. This is accepted,
rather than fixed categories with trusted names only, for two reasons: Lucas's B13 decision asks the
doctor to "name the ignored one", which only the backend's own class can name; and code able to choose a
class name already runs inside the process with everything the doctor could reveal (the trust boundary
above), so showing its name gives it nothing. A fixed category would tell the user that a backend was
refused but not which installed class to look at.

Not here yet: wiring into a Cockpit settings surface or the frozen `app/` doctor.

## Tests

```
PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/keys/tests -t engine -v
```

CI runs `engine/tests`; `engine/tests/keys/test_keys_suite_ci.py` imports this folder's test classes so
that the same step collects and runs them. Its coverage check fails unless every runnable test that
unittest discovery of this folder finds (any test method, whatever its class is called, in any test module
or test package here, or a module that fails to import) is among the tests pytest will run from that file,
exactly once. Under pytest it reads the running session's own items, after every collection hook
(including any `conftest.py`) has run: a test pytest leaves out, such as one marked `__test__ = False`, an
item a plugin drops, and a class exposed twice all fail it. In a run narrowed by one of these selection
options (node ids, `-k`, `-m`, `--deselect`, `--lf`, `--sw`), and under plain unittest, it compares
pytest's collection of the file in a child process instead, which sees only what `--collect-only` sees; a
run narrowed any other way, such as a plugin's own selection option, fails the check. It cannot see a test
a plugin skips at run time, a plugin that drops the check itself, or a method that is no longer a test at
all (renamed from `test_...`), and it is skipped on an interpreter without pytest; CI has pytest. A second
check fails when a test module here defines a test class twice, or a test method twice in one class, since
the later definition replaces the earlier one before either runner sees it. Definitions inside blocks of
the same scope (`if`, `for`, `while`, `with`, `try`, `match`) count. Alternatives that cannot both run
count once: the branches of one `if`/`elif`/`else` or `match`, and the ordinary `except` arms and `else` of
one `try`. A `try` body and its `finally` count on top of whichever of those runs, and `except*` arms add
up, since several can run. Rebinding a name by assignment or import is not checked.
The suite is stdlib `unittest`; a pytest-style test function here would not be covered. Tests that need an OS
keychain use the installed `keyring` library's own classes, with their probe and password methods pointed
at a dict (no real keychain or D-Bus is reached), and are skipped without the library. Each test host gets
its own temp key folder (`fakes.key_file`), since a broker or venue save now writes the keychain record
beside the key file (B24); no test uses the tester's own default key file.
