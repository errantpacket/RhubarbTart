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

SH_FILES=(tools/bootstrap.sh tools/check.sh scripts/*.sh guest/*.sh)
INV_SH=(tools/bootstrap.sh scripts/*.sh guest/*.sh)   # invariant scans skip this file's own patterns
HCL_FILES=(packer/*.pkr.hcl)

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
  if uv run --quiet --no-project python -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' tools/resolve.py
  then ok "resolve.py parses"; else bad "resolve.py syntax"; fi
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

BUILD_CODE=(packer guest scripts tools/bootstrap.sh tools/resolve.py)

check "no Homebrew in the toolchain path" \
  '(^|[;&|[:space:]])brew[[:space:]]' scripts tools/bootstrap.sh
check "no 'packer init' (plugin comes from bootstrap only)" \
  '^[^#]*packer[[:space:]]+init' scripts tools/bootstrap.sh tools/resolve.py
check "no --from-ipsw=latest / from_ipsw = \"latest\"" \
  'from[-_]ipsw[[:space:]]*=[[:space:]]*"?latest' packer scripts
check "no default credentials in templates" \
  'default[[:space:]]*=[[:space:]]*"(admin|password|packer)"' packer
check "no NOPASSWD sudoers being written" \
  'NOPASSWD[^|]*(tee|>)|visudo' packer guest
check "no auto-login configured (kcpassword/autoLoginUser writes)" \
  '(^|[[:space:];&|])(xxd|install|cp|mv|tee)[[:space:]][^#]*kcpassword|defaults write[^#]*autoLoginUser' packer guest
check "sshd never re-enables password/kbd-interactive auth" \
  '(PasswordAuthentication|KbdInteractiveAuthentication)[[:space:]]+yes' packer guest
check "Gatekeeper/SIP never disabled" \
  'spctl[[:space:]]+(--global-disable|--master-disable)|csrutil[[:space:]]+disable' packer guest scripts
check "no log() helper shadowing macOS log(1) in guest scripts" \
  '^[[:space:]]*log\(\)' guest/*.sh
check "no bare '! cmd' lines (set -e ignores them; use if/||)" \
  '^[[:space:]]*![[:space:]][^|]*$' "${INV_SH[@]}"
check "bash 3.2 compatible (macOS /bin/bash: no assoc arrays, mapfile, \${x,,}, |&, &>>)" \
  'declare -A|mapfile|readarray|\$\{[A-Za-z_]+,,\}|\$\{[A-Za-z_]+\^\^\}|\|&|&>>|coproc' \
  "${INV_SH[@]}"
check "downloads use curl with --proto =https (no plain http fetches)" \
  'curl[^#]*http://' "${BUILD_CODE[@]}"
check "the host-only toolchain is not bypassed with 'source' of the pin file" \
  '(^|[;&|[:space:]])(source|\.)[[:space:]]+[^[:space:]]*toolchain\.env' scripts tools

# Plugin version must agree between the pin file and every template.
pin="$(sed -n 's/^PACKER_PLUGIN_TART_VERSION=//p' config/toolchain.env)"
if grep -L "version = \"= $pin\"" "${HCL_FILES[@]}" | grep -q .; then
  bad "required_plugins in packer/*.pkr.hcl != PACKER_PLUGIN_TART_VERSION ($pin)"
else ok "plugin version pinned consistently ($pin)"; fi

echo
if ((failures)); then echo "check: $failures failure(s), $skipped skipped"; exit 1; fi
echo "check: all passed ($skipped skipped)"
