set -euo pipefail
# CSL-473 interim E3-1: install a hash-verified Claude Code CLI to a root-owned
# path, then pass path_to_claude_code_executable so the action skips its
# unpinned installer. Pin literals are inlined from the default-branch
# workflow; scripts/ci/*.pin must match.
# This file is a reference copy for tests. Workflows inline the same bytes;
# do not execute it from GITHUB_WORKSPACE (the model-job checkout is untrusted).
# Sunset: retire this skip-path when frozen D10 E3 hosted-byte provenance
# lands. Rollback is revert of the install step and path_to input.

CLAUDE_VERSION="2.1.306"
PLATFORM="linux-x64"
EXPECTED_SHA256="520bc6c1f40af466bd03a278b5ae1af1a2938bc7de5b9e5cb15526da20d71cfe"
EXPECTED_BYTES="290532720"
EXPECTED_FPR="31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE"
RELEASE_BASE="https://downloads.claude.ai/claude-code-releases"
KEY_URL="https://downloads.claude.ai/keys/claude-code.asc"
DEST_DIR="/opt/csl-claude/${CLAUDE_VERSION}"
DEST="${DEST_DIR}/claude"

command -v curl >/dev/null
command -v gpg >/dev/null
command -v python3 >/dev/null
command -v sha256sum >/dev/null
command -v sudo >/dev/null

STAGING="${RUNNER_TEMP:?}/csl-claude-staging"
GNUPGHOME=""
cleanup() {
  rm -rf "$STAGING"
  if [ -n "$GNUPGHOME" ]; then rm -rf "$GNUPGHOME"; fi
}
trap cleanup EXIT

rm -rf "$STAGING"
mkdir -m 0700 "$STAGING"

curl -fsSL --retry 3 --retry-delay 2 -o "$STAGING/claude" \
  "${RELEASE_BASE}/${CLAUDE_VERSION}/${PLATFORM}/claude"

bytes="$(stat -c %s "$STAGING/claude")"
if [ "$bytes" != "$EXPECTED_BYTES" ]; then
  echo "size mismatch: got ${bytes} want ${EXPECTED_BYTES}" >&2
  exit 1
fi
echo "${EXPECTED_SHA256}  ${STAGING}/claude" | sha256sum -c -

curl -fsSL --retry 3 --retry-delay 2 -o "$STAGING/manifest.json" \
  "${RELEASE_BASE}/${CLAUDE_VERSION}/manifest.json"
curl -fsSL --retry 3 --retry-delay 2 -o "$STAGING/manifest.json.sig" \
  "${RELEASE_BASE}/${CLAUDE_VERSION}/manifest.json.sig"
curl -fsSL --retry 3 --retry-delay 2 -o "$STAGING/claude-code.asc" "$KEY_URL"

GNUPGHOME="$(mktemp -d)"
chmod 700 "$GNUPGHOME"
export GNUPGHOME
gpg --batch --import "$STAGING/claude-code.asc"
status="$(gpg --batch --status-fd 1 --verify "$STAGING/manifest.json.sig" "$STAGING/manifest.json")"
case "$status" in
  *"[GNUPG:] VALIDSIG ${EXPECTED_FPR} "*) ;;
  *)
    echo "signed-manifest VALIDSIG mismatch" >&2
    exit 1
    ;;
esac

export CLAUDE_VERSION EXPECTED_SHA256 EXPECTED_BYTES
STAGING="$STAGING" python3 -c '
import json, os, sys
manifest = json.load(open(os.environ["STAGING"] + "/manifest.json", encoding="utf-8"))
plat = manifest["platforms"]["linux-x64"]
errors = []
if manifest.get("version") != os.environ["CLAUDE_VERSION"]:
    errors.append("version")
if plat.get("checksum") != os.environ["EXPECTED_SHA256"]:
    errors.append("checksum")
if int(plat.get("size", -1)) != int(os.environ["EXPECTED_BYTES"]):
    errors.append("size")
if errors:
    sys.stderr.write("signed manifest does not match committed pin: " + ",".join(errors) + "\n")
    sys.exit(1)
'

sudo mkdir -p "$DEST_DIR"
sudo chown root:root /opt/csl-claude "$DEST_DIR"
sudo chmod 0755 /opt/csl-claude "$DEST_DIR"
sudo install -o root -g root -m 0755 "$STAGING/claude" "$DEST"
test "$(stat -c %U:%G:%a "$DEST")" = "root:root:755"
test -x "$DEST"
if [ -w "$DEST" ] || [ -w "$DEST_DIR" ]; then
  echo "install path is writable by the runner" >&2
  exit 1
fi
