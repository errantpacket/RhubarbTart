#!/bin/bash
# Static checks for this repo: run before every commit. Works on macOS and Linux.
#
#   ./tools/check.sh
#
# 1. Lint/syntax: bash -n, shellcheck, packer validate -syntax-only + fmt, python compile + ruff.
# 2. Invariants: grep for regressions of the project's security/provenance rules
#    (README "Rules" and "Security posture"). Each failure names the file and line.
#
# Tools are taken from .toolchain/bin when present, then PATH; shellcheck and ruff
# fall back to `uvx` (shellcheck-py / ruff). A check whose tool is unavailable is
# reported as SKIPPED, never silently passed.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="$ROOT/.toolchain/bin:$PATH" CHECKPOINT_DISABLE=1

failures=0
skipped=0
ok() { echo "  ok    $*"; }
bad() { echo "  FAIL  $*"; failures=$((failures + 1)); }
skip() { echo "  SKIP  $*"; skipped=$((skipped + 1)); }

SH_FILES=(rhubarb rhubarb-tui tools/bootstrap.sh tools/check.sh scripts/*.sh guest/*/*.sh)
INV_SH=(rhubarb rhubarb-tui tools/bootstrap.sh scripts/*.sh guest/*/*.sh)   # invariant scans skip this file's own patterns
MACOS_BASH=(rhubarb rhubarb-tui tools/bootstrap.sh scripts/*.sh guest/macos/*.sh)   # run by macOS /bin/bash 3.2
HCL_FILES=(packer/*/*.pkr.hcl)

echo "== syntax & lint"
for f in "${SH_FILES[@]}"; do bash -n "$f" || bad "bash -n $f"; done
ok "bash -n (${#SH_FILES[@]} files)"

if command -v shellcheck >/dev/null; then SC=(shellcheck)
elif command -v uvx >/dev/null; then SC=(uvx --quiet --from shellcheck-py shellcheck)
else SC=(); fi
if ((${#SC[@]})); then
  if "${SC[@]}" -s bash -x "${SH_FILES[@]}"; then ok shellcheck; else bad shellcheck; fi
else skip "shellcheck (install it or uv)"; fi

if command -v packer >/dev/null; then
  for f in "${HCL_FILES[@]}"; do
    packer validate -syntax-only "$f" >/dev/null || bad "packer validate $f"
    packer fmt -check "$f" >/dev/null || bad "packer fmt $f (run: packer fmt $f)"
  done
  ok "packer syntax + fmt"
else skip "packer (run tools/bootstrap.sh)"; fi

if command -v uv >/dev/null; then
  if uv run --quiet --no-project python -m py_compile tools/resolve.py tools/rhubarb_cli.py tools/rhubarb_tui.py tools/test_rhubarb_tui.py tools/serve_preseed.py tools/rhubarb/*.py tools/rhubarb/tui/*.py tools/rhubarb/tui/actions/*.py
  then ok "python sources compile"; else bad "python syntax"; fi
  if uv run --quiet --no-project python tools/test_rhubarb.py >/dev/null
  then ok "self-tests (ed25519, NAR, dpkg, clone records, rhubarb CLI, password rotation)"
  else bad "self-tests (run: uv run tools/test_rhubarb.py)"; fi
  # Headless TUI render test: mounts the Textual app with a mocked core API (no Mac/
  # tart/keychain) and asserts each pane renders. Runs via `uv run --script` under the
  # hash-locked script lockfile, like ./rhubarb-tui. See docs/INTERFACE-PLAN.md, Gate B.
  if uv run --quiet --script tools/test_rhubarb_tui.py >/dev/null
  then ok "TUI render test (headless Textual Pilot, mock API — read-only)"
  else bad "TUI render test (run: uv run --script tools/test_rhubarb_tui.py)"; fi
  if out="$(uv run --quiet --no-project python tools/resolve.py list)" && ! grep -q INVALID <<<"$out"
  then ok "all profiles load ($(wc -l <<<"$out" | tr -d ' '))"; else bad "profile config"; echo "$out"; fi
  if uvx --quiet ruff check --quiet --no-cache --select F,B,E7,E9 tools/; then ok "ruff (F,B,E7,E9)"; else bad ruff; fi
else skip "python checks (need uv)"; fi

echo "== invariants"
# check <description> <grep -E pattern> <paths...>: pattern must NOT match.
check() {
  local desc=$1 pattern=$2 hits
  shift 2
  hits="$(grep -rnE -- "$pattern" "$@" 2>/dev/null || true)"
  if [[ -z "$hits" ]]; then ok "$desc"; return; fi
  bad "$desc"
  while IFS= read -r line; do echo "          $line"; done <<<"$hits"
}

BUILD_CODE=(packer guest scripts nix kali tools/bootstrap.sh tools/resolve.py tools/rhubarb)

check "no Homebrew in the toolchain path" \
  '(^|[;&|[:space:]])brew[[:space:]]' scripts tools/bootstrap.sh
check "no 'packer init' (plugin comes from bootstrap only)" \
  '^[^#]*packer[[:space:]]+init' scripts tools/bootstrap.sh tools/resolve.py
check "no --from-ipsw=latest / from_ipsw = \"latest\"" \
  'from[-_]ipsw[[:space:]]*=[[:space:]]*"?latest' packer scripts
check "no default credentials in templates" \
  'default[[:space:]]*=[[:space:]]*"(admin|password|packer|kali|nixos)"' packer
check "no NOPASSWD sudoers being written" \
  'NOPASSWD[^|]*(tee|>)|visudo|wheelNeedsPassword[[:space:]]*=[[:space:]]*false' packer guest nix kali
check "no auto-login configured (kcpassword/autoLoginUser writes)" \
  '(^|[[:space:];&|])(xxd|install|cp|mv|tee)[[:space:]][^#]*kcpassword|defaults write[^#]*autoLoginUser|autoLogin\.enable[[:space:]]*=[[:space:]]*true|autologinUser[[:space:]]*=[[:space:]]*"|autologin-user=[^[:space:]]|logsInAutomatically=true' packer guest nix kali
check "sshd never re-enables password/kbd-interactive auth" \
  '(PasswordAuthentication|KbdInteractiveAuthentication)([[:space:]]+yes|[[:space:]]*=[[:space:]]*true)|PermitRootLogin[[:space:]]*=?[[:space:]]*"?yes' packer guest nix kali
check "Gatekeeper/SIP never disabled" \
  'spctl[[:space:]]+(--global-disable|--master-disable)|csrutil[[:space:]]+disable' packer guest scripts
check "no log() helper shadowing macOS log(1) in guest scripts" \
  '^[[:space:]]*log\(\)' guest/macos/*.sh
check "no bare '! cmd' lines (set -e ignores them; use if/||)" \
  '^[[:space:]]*![[:space:]][^|]*$' "${INV_SH[@]}"
check "bash 3.2 compatible (macOS /bin/bash: no assoc arrays, mapfile, \${x,,}, |&, &>>)" \
  'declare -A|mapfile|readarray|\$\{[A-Za-z_]+,,\}|\$\{[A-Za-z_]+\^\^\}|\|&|&>>|coproc' \
  "${MACOS_BASH[@]}"
check "preseed template carries no literal password (only @BOOTSTRAP@)" \
  'passwd/(user|root)-password(-again)?[[:space:]]+password[[:space:]]+[^@[:space:]]' kali
check "Linux guests never trust unsigned repos" \
  'trusted=yes|allow-unauthenticated|AllowInsecureRepositories|require-sigs[[:space:]]*=[[:space:]]*false' guest nix kali
check "VPN secrets never baked (no auth keys / service tokens in build code)" \
  'tskey-(auth|client)-[A-Za-z0-9]|auth_client_secret</key><string>[^$%<]' packer guest nix kali config
check "downloads use curl with --proto =https (no plain http fetches)" \
  'curl[^#]*http://' "${BUILD_CODE[@]}"
check "the host-only toolchain is not bypassed with 'source' of the pin file" \
  '(^|[;&|[:space:]])(source|\.)[[:space:]]+[^[:space:]]*toolchain\.env' scripts tools

# Plugin version must agree between the pin file and every template.
pin="$(sed -n 's/^PACKER_PLUGIN_TART_VERSION=//p' config/toolchain.env)"
if grep -L "version = \"= $pin\"" "${HCL_FILES[@]}" | grep -q .; then
  bad "required_plugins in packer/*/*.pkr.hcl != PACKER_PLUGIN_TART_VERSION ($pin)"
else ok "plugin version pinned consistently ($pin)"; fi

# Textual is the TUI's only third-party dep (the repo's first). It must be pinned
# to an exact version in tools/rhubarb_tui.py and hash-locked in the adjacent uv
# script lockfile (uv lock --script), which `./rhubarb-tui` runs under with
# `uv run --script`. See docs/INTERFACE-PLAN.md, decision gate B-0.
tui_pin="$(sed -n 's/.*"textual==\([0-9A-Za-z.-]*\)".*/\1/p' tools/rhubarb_tui.py)"
if [[ -z "$tui_pin" ]]; then
  bad "tools/rhubarb_tui.py does not pin textual to an exact version (textual==X.Y.Z)"
elif [[ ! -f tools/rhubarb_tui.py.lock ]]; then
  bad "tools/rhubarb_tui.py.lock missing (run: uv lock --script tools/rhubarb_tui.py)"
elif ! grep -q "specifier = \"==$tui_pin\"" tools/rhubarb_tui.py.lock; then
  bad "tools/rhubarb_tui.py.lock out of sync with textual==$tui_pin (run: uv lock --script tools/rhubarb_tui.py)"
elif ! grep -q 'hash = "sha256:' tools/rhubarb_tui.py.lock; then
  bad "tools/rhubarb_tui.py.lock carries no sha256 hashes"
else ok "textual pinned + hash-locked (textual==$tui_pin)"; fi

# The headless render test pins the same textual and carries its own hash-locked
# script lockfile; keep both in step with the app's pin.
if ! grep -q "\"textual==$tui_pin\"" tools/test_rhubarb_tui.py; then
  bad "tools/test_rhubarb_tui.py does not pin textual==$tui_pin (must match the app)"
elif [[ ! -f tools/test_rhubarb_tui.py.lock ]]; then
  bad "tools/test_rhubarb_tui.py.lock missing (run: uv lock --script tools/test_rhubarb_tui.py)"
elif ! grep -q "specifier = \"==$tui_pin\"" tools/test_rhubarb_tui.py.lock; then
  bad "tools/test_rhubarb_tui.py.lock out of sync with textual==$tui_pin (run: uv lock --script tools/test_rhubarb_tui.py)"
elif ! grep -q 'hash = "sha256:' tools/test_rhubarb_tui.py.lock; then
  bad "tools/test_rhubarb_tui.py.lock carries no sha256 hashes"
else ok "TUI render test pinned + hash-locked (textual==$tui_pin)"; fi

echo
if ((failures)); then echo "check: $failures failure(s), $skipped skipped"; exit 1; fi
echo "check: all passed ($skipped skipped)"
