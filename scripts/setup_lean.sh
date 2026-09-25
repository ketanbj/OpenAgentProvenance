#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$HOME/.elan/bin/elan" ]]; then
  installer_dir="$(mktemp -d)"
  trap 'rm -rf "$installer_dir"' EXIT
  case "$(uname -s):$(uname -m)" in
    Linux:x86_64) elan_platform=x86_64-unknown-linux-gnu ;;
    Linux:aarch64) elan_platform=aarch64-unknown-linux-gnu ;;
    Darwin:arm64) elan_platform=aarch64-apple-darwin ;;
    Darwin:x86_64) elan_platform=x86_64-apple-darwin ;;
    *) echo 'Unsupported Lean bootstrap platform' >&2; exit 1 ;;
  esac
  curl --proto '=https' --tlsv1.2 -fsSL \
    "https://github.com/leanprover/elan/releases/download/v4.1.2/elan-$elan_platform.tar.gz" \
    -o "$installer_dir/elan.tar.gz"
  tar -xzf "$installer_dir/elan.tar.gz" -C "$installer_dir"
  "$installer_dir/elan-init" -y --no-modify-path --default-toolchain none
fi
export PATH="$HOME/.elan/bin:$PATH"
cd "$project_root/formal"
lake build
audit_output="$(lake env lean Audit.lean)"
printf '%s\n' "$audit_output"
if [[ "$audit_output" == *sorryAx* ]]; then
  echo 'Formal checker contains an unfinished proof' >&2
  exit 1
fi
