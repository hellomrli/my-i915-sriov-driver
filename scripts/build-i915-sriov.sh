#!/usr/bin/env bash
# Stable entry point for the default, recommended i915 package.
set -euo pipefail
export DRIVER=i915
exec "$(dirname "${BASH_SOURCE[0]}")/build-intel-sriov.sh" "$@"
