#!/bin/bash
# Static checks for this repo: run before every commit. Works on macOS and Linux.
#
#   ./tools/check.sh
#
# 1. Lint/syntax: bash -n, shellcheck, packer validate -syntax-only + fmt, python compile + ruff.
# 2. Invariants: grep for regressions of the project's security/provenance rules
#    (CONTRIBUTING.md "Ground rules", docs/trust-model.md "Security posture"). Each failure names
#    the file and line.
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

SH_FILES=(rhubarbtart rhubarbtart-tui rbt-range tools/bootstrap.sh tools/check.sh scripts/*.sh guest/*/*.sh)
INV_SH=(rhubarbtart rhubarbtart-tui rbt-range tools/bootstrap.sh scripts/*.sh guest/*/*.sh)   # invariant scans skip this file's own patterns
MACOS_BASH=(rhubarbtart rhubarbtart-tui rbt-range tools/bootstrap.sh scripts/*.sh guest/macos/*.sh)   # run by macOS /bin/bash 3.2
HCL_FILES=(packer/*/*.pkr.hcl)

echo "== syntax & lint"
for f in "${SH_FILES[@]}"; do bash -n "$f" || bad "bash -n $f"; done
ok "bash -n (${#SH_FILES[@]} files)"

# The lists above are kept by hand. Make sure every tracked bash script is on them (so it is
# linted and scanned), and that every tracked script with a shebang is executable in git (the
# CLI runs scripts/enroll.sh directly; a 0644 checkout fails with "permission denied", #127).
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  unlisted="" notexec=""
  while IFS= read -r entry; do
    mode="${entry%% *}" f="${entry#*$'\t'}"
    first="$(head -n 1 "$f" 2>/dev/null || true)"
    case "$first" in '#!'*) ;; *) continue ;; esac
    [[ "$mode" == 100755 ]] || notexec="$notexec $f"
    case "$first" in
      *bash*) listed=0
              for s in "${SH_FILES[@]}"; do [[ "$s" == "$f" ]] && listed=1; done
              ((listed)) || unlisted="$unlisted $f" ;;
    esac
  done < <(git ls-files -s)
  if [[ -z "$unlisted" ]]; then ok "every tracked bash script is linted and scanned"
  else bad "bash scripts missing from SH_FILES in tools/check.sh:$unlisted"; fi
  if [[ -z "$notexec" ]]; then ok "every tracked script with a shebang is executable"
  else bad "scripts with a shebang but no execute bit (git update-index --chmod=+x):$notexec"; fi
else skip "script list and execute-bit checks (not a git checkout)"; fi

# Linters are pinned so every machine (the Mac, CI) gets the same findings (#137): a newer or
# older shellcheck on PATH can disagree. Bump these deliberately, like any other pin.
SHELLCHECK_PY="shellcheck-py==0.11.0.1"
RUFF="ruff==0.16.9"
if command -v uvx >/dev/null; then SC=(uvx --quiet --from "$SHELLCHECK_PY" shellcheck)
elif command -v shellcheck >/dev/null; then SC=(shellcheck)   # unpinned fallback without uv
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
  if uv run --quiet --no-project python -m py_compile tools/*.py tools/tests/*.py tools/rhubarb/*.py tools/rhubarb/tui/*.py tools/rhubarb/tui/actions/*.py
  then ok "python sources compile"; else bad "python syntax"; fi
  # On failure, show which checks failed (CI has no other way to see them).
  if out="$(uv run --quiet --no-project python tools/test_rhubarb.py 2>&1)"
  then ok "self-tests (verification, clone records, CLI, core API, engagements, evidence, service)"
  else bad "self-tests (run: uv run tools/test_rhubarb.py)"
       grep -E "FAIL|Error|Traceback" <<<"$out" | head -40 | sed 's/^/          /'; fi
  # Headless TUI render test: mounts the Textual app with a mocked core API (no Mac/
  # tart/keychain) and asserts each pane renders. Runs via `uv run --script` under the
  # hash-locked script lockfile, like ./rhubarbtart-tui. See docs/INTERFACE-PLAN.md, Gate B.
  if out="$(uv run --quiet --script tools/test_rhubarb_tui.py 2>&1)"
  then ok "TUI test (headless Textual Pilot, mocked core API)"
  else bad "TUI render test (run: uv run --script tools/test_rhubarb_tui.py)"
       grep -E "FAIL|Error|Traceback" <<<"$out" | head -40 | sed 's/^/          /'; fi
  if out="$(uv run --quiet --no-project python tools/resolve.py list)" && ! grep -q INVALID <<<"$out"
  then ok "all profiles load ($(wc -l <<<"$out" | tr -d ' '))"; else bad "profile config"; echo "$out"; fi
  if uvx --quiet "$RUFF" check --quiet --no-cache --select F,B,E7,E9 tools/; then ok "ruff (F,B,E7,E9)"; else bad ruff; fi
else skip "python checks (need uv)"; fi

echo "== invariants"
# check <description> <grep -E pattern> <paths...>: pattern must NOT match.
check() {
  local desc=$1 pattern=$2 hits p
  shift 2
  # A scan target that no longer exists would make grep find nothing and the check pass; after a
  # move or rename, fail instead so the check gets pointed at the new path.
  for p in "$@"; do
    if [[ ! -e "$p" ]]; then bad "$desc (scan target missing: $p)"; return; fi
  done
  hits="$(grep -rnE -- "$pattern" "$@" 2>/dev/null || true)"
  if [[ -z "$hits" ]]; then ok "$desc"; return; fi
  bad "$desc"
  while IFS= read -r line; do echo "          $line"; done <<<"$hits"
}

BUILD_CODE=(packer guest scripts tools/bootstrap.sh tools/resolve.py tools/rhubarb)

check "no Homebrew in the toolchain path" \
  '(^|[;&|[:space:]])brew[[:space:]]' scripts tools/bootstrap.sh
check "no 'packer init' (plugin comes from bootstrap only)" \
  '^[^#]*packer[[:space:]]+init' scripts tools/bootstrap.sh tools/resolve.py
check "no --from-ipsw=latest / from_ipsw = \"latest\"" \
  'from[-_]ipsw[[:space:]]*=[[:space:]]*"?latest' packer scripts
check "no default credentials in templates" \
  'default[[:space:]]*=[[:space:]]*"(admin|password|packer|kali|nixos)"' packer
check "no NOPASSWD sudoers being written" \
  'NOPASSWD[^#]*(tee|>)|visudo|wheelNeedsPassword[[:space:]]*=[[:space:]]*false' packer guest
check "no auto-login configured (kcpassword/autoLoginUser writes)" \
  '(^|[[:space:];&|])(xxd|install|cp|mv|tee)[[:space:]][^#]*kcpassword|defaults write[^#]*autoLoginUser|autoLogin\.enable[[:space:]]*=[[:space:]]*true|autologinUser[[:space:]]*=[[:space:]]*"|autologin-user=[^[:space:]]|logsInAutomatically=true' packer guest
check "sshd never re-enables password/kbd-interactive auth" \
  '(PasswordAuthentication|KbdInteractiveAuthentication)([[:space:]]+yes|[[:space:]]*=[[:space:]]*true)|PermitRootLogin[[:space:]]*=?[[:space:]]*"?yes' packer guest
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
  'passwd/(user|root)-password(-again)?[[:space:]]+password[[:space:]]+[^@[:space:]]' guest/kali
check "Linux guests never trust unsigned repos" \
  'trusted=yes|allow-unauthenticated|AllowInsecureRepositories|require-sigs[[:space:]]*=[[:space:]]*false' guest
check "VPN secrets never baked (no auth keys / service tokens in build code)" \
  'tskey-(auth|client)-[A-Za-z0-9]|auth_client_secret</key><string>[^$%<]' packer guest config
# Plain http is allowed only to loopback (the localhost-only registry, #32), never the network.
hits="$(grep -rnE 'curl[^#]*http://' "${BUILD_CODE[@]}" 2>/dev/null \
  | grep -vE 'http://(127\.0\.0\.1|localhost)[:/"]' || true)"
if [[ -z "$hits" ]]; then ok "downloads use curl with --proto =https (no plain http fetches; loopback excepted)"
else bad "downloads use curl with --proto =https (no plain http fetches; loopback excepted)"
  while IFS= read -r line; do echo "          $line"; done <<<"$hits"; fi
check "the host-only toolchain is not bypassed with 'source' of the pin file" \
  '(^|[;&|[:space:]])(source|\.)[[:space:]]+[^[:space:]]*toolchain\.env' scripts tools
# The control-plane service (#104) must stay a thin client of the typed core: it may not reach
# tart, the keychain (security) or ssh directly — only through api.py.
check "control-plane service touches only the typed core (no direct tart/keychain/ssh)" \
  '(^|[^a-zA-Z_.])(subprocess|hostops|os\.system|security |/usr/bin/security)' tools/rhubarb/service.py tools/rhubarb/agent.py

# The herdr driver reaches tart/keychain only through the typed core, never directly (it drives
# herdr via subprocess, which is fine; calling tart/security itself is not).
check "herdr driver reaches tart/keychain only through the core (no direct hostops/tart/security)" \
  '(^|[^a-zA-Z_.])(hostops|find-generic-password|/usr/bin/security|[\"\x27]tart[\"\x27 ])' tools/rhubarb/herdr.py

# Evidence pulled from a guest is parsed, never unpacked onto the host (#85): the guest is under
# test, and its archive could plant symlinks or ../ paths.
check "guest evidence is never extracted onto the host (no tarfile extract/extractall)" \
  '\.extractall\(|\.extract\(' tools/rhubarb/api.py tools/rhubarb/evidence.py
# gpg.py alone picks the binary (on macOS: the pinned one bootstrap built, never PATH's).
NOT_GPG_PY=()
for f in tools/resolve.py tools/rhubarb/*.py; do [[ "$f" == tools/rhubarb/gpg.py ]] || NOT_GPG_PY+=("$f"); done
check "gpg is only invoked through tools/rhubarb/gpg.py (pinned toolchain gpg on macOS)" \
  '\[[[:space:]]*"gpg"[[:space:]]*,|,[[:space:]]*"gpg"[[:space:]]*[],]|which\("gpg"\)' "${NOT_GPG_PY[@]}"

# Plugin version must agree between the pin file and every template.
pin="$(sed -n 's/^PACKER_PLUGIN_TART_VERSION=//p' config/toolchain.env)"
if grep -L "version = \"= $pin\"" "${HCL_FILES[@]}" | grep -q .; then
  bad "required_plugins in packer/*/*.pkr.hcl != PACKER_PLUGIN_TART_VERSION ($pin)"
else ok "plugin version pinned consistently ($pin)"; fi

# Prose style (#145): no em dashes in tracked Markdown (README, docs, skills, changelog). The
# full writing rules are in the rhubarb-dev skill, "Writing docs and skills".
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  md_dashes="$(git ls-files -z '*.md' | xargs -0 grep -n '—' 2>/dev/null || true)"
  if [[ -z "$md_dashes" ]]; then ok "no em dashes in tracked Markdown"
  else bad "em dashes in tracked Markdown (use . , : ; or parentheses):"; echo "$md_dashes" | head -20 | sed 's/^/          /'; fi
fi

# CI (#137): every action is pinned to a full commit SHA (a tag can be moved), and the workflow's
# uv is the version config/toolchain.env pins.
if compgen -G ".github/workflows/*.yml" >/dev/null; then
  unpinned="$(grep -hnE '^[[:space:]]*-?[[:space:]]*uses:' .github/workflows/*.yml \
              | grep -vE 'uses:[[:space:]]*[^@[:space:]]+@[0-9a-f]{40}([[:space:]]|$)' || true)"
  if [[ -z "$unpinned" ]]; then ok "workflow actions pinned to commit SHAs"
  else bad "workflow actions not pinned to a commit SHA:"; echo "          $unpinned"; fi
  uv_pin="$(sed -n 's/^UV_VERSION=//p' config/toolchain.env)"
  ci_uv="$(sed -n 's/^[[:space:]]*version:[[:space:]]*"\{0,1\}\([0-9.]*\)"\{0,1\}.*/\1/p' .github/workflows/check.yml | head -1)"
  if [[ "$ci_uv" == "$uv_pin" ]]; then ok "CI uv matches config/toolchain.env ($uv_pin)"
  else bad "CI uv '$ci_uv' != UV_VERSION '$uv_pin' (.github/workflows/check.yml)"; fi
fi

# Version (#138): one SemVer in tools/rhubarb/__init__.py, with a matching CHANGELOG.md section.
ver="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' tools/rhubarb/__init__.py)"
if [[ ! "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  bad "tools/rhubarb/__init__.py: __version__ '$ver' is not MAJOR.MINOR.PATCH"
elif ! grep -qE "^## \[$ver\] - [0-9]{4}-[0-9]{2}-[0-9]{2}$" CHANGELOG.md; then
  bad "CHANGELOG.md has no '## [$ver] - YYYY-MM-DD' section for __version__ $ver"
else ok "version $ver has a CHANGELOG entry"; fi

# Textual is the TUI's only third-party dep (the repo's first). It must be pinned
# to an exact version in tools/rhubarb_tui.py and hash-locked in the adjacent uv
# script lockfile (uv lock --script), which `./rhubarbtart-tui` runs under with
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
