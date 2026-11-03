#!/usr/bin/env bash
# Independent experimental xe package; never packages i915.ko.
set -euo pipefail
export DRIVER=xe
exec "$(dirname "${BASH_SOURCE[0]}")/build-intel-sriov.sh" "$@"
