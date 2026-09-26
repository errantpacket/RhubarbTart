# Sourced by the scripts in this directory: use only the pinned, repo-local toolchain.
# shellcheck shell=bash

# Works whether sourced by bash (BASH_SOURCE) or interactively in zsh ($0 is the sourced file).
RHUBARB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
TOOLCHAIN="$RHUBARB_ROOT/.toolchain"
[[ -x "$TOOLCHAIN/bin/tart" && -x "$TOOLCHAIN/bin/packer" && -x "$TOOLCHAIN/bin/uv" ]] || {
  echo "toolchain missing; run ./tools/bootstrap.sh" >&2
  # shellcheck disable=SC2317  # exit is reached only when executed rather than sourced
  return 1 2>/dev/null || exit 1   # `return` when sourced interactively: don't kill the shell
}
export PATH="$TOOLCHAIN/bin:$PATH"
export PACKER_PLUGIN_PATH="$TOOLCHAIN/packer-plugins"
export CHECKPOINT_DISABLE=1   # no Packer version-check/telemetry calls to HashiCorp
# Always the same uv-managed interpreter, never whatever python3 is on the host.
# uv verifies its Python downloads against hashes built into the pinned uv release.
export UV_PYTHON=3.13
export UV_PYTHON_PREFERENCE=only-managed
