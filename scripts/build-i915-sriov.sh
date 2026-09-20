#!/usr/bin/env bash
# build-i915-sriov.sh - build the strongtz i915-sriov-dkms modules for an
# Unraid kernel, packaged as a Slackware .txz installable on the server.
#
# Works both locally and inside GitHub Actions (cloud build):
#   1. fetch the ich777 Unraid kernel source tree (prebuilt, no kernel rebuild)
#   2. clone strongtz/i915-sriov-dkms at the requested ref
#   3. apply the Unraid 6.x slab-compat patch
#   4. build the 4 modules (i915, kvmgt, xe, intel_sriov_compat)
#   5. package them into i915-sriov-<ver>-<kernel>-Unraid-<build>.txz + md5
#
# Outputs land in ./out (or $OUT_DIR).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---------- config (overridable via environment) ----------
TARGET_KERNEL_VERSION="${TARGET_KERNEL_VERSION:-6.18.44}"
KERNEL_RELEASE="${KERNEL_RELEASE:-${TARGET_KERNEL_VERSION}-Unraid}"
JOBS="${JOBS:-$(nproc --all)}"
KERNEL_ARCHIVE_URL="${KERNEL_ARCHIVE_URL:-https://github.com/ich777/unraid_kernel/releases/download/${KERNEL_RELEASE}/linux-${KERNEL_RELEASE}.tar.xz}"
# Per-release SHA256 of the ich777 kernel archive; unknown releases fall back
# to no check so builds for new kernels keep working.
case "${KERNEL_RELEASE}" in
  6.18.44-Unraid) DEFAULT_KERNEL_SHA256="618df8d001e9f98b95306eb2eac4cb776d0bf4b98061f0f4cedbc10c1468858d" ;;
  6.18.45-Unraid) DEFAULT_KERNEL_SHA256="365dee16bbd9c505d36a0d0a1a2bc63723f8a8d55b2f7c7991d806a4c849df7b" ;;
  6.18.46-Unraid) DEFAULT_KERNEL_SHA256="e8969f6a5d31106ae5ebf821ba128e043dcda78bc5f1f1a6344a8e46c9c9e280" ;;
  6.18.47-Unraid) DEFAULT_KERNEL_SHA256="72822aea43a7d6dab3ae7a8489481a583504896927c1ce7117df8ab1b46d173f" ;;
  *)              DEFAULT_KERNEL_SHA256="" ;;
esac
KERNEL_ARCHIVE_SHA256="${KERNEL_ARCHIVE_SHA256:-${DEFAULT_KERNEL_SHA256}}"
I915_SRIOV_REPO="${I915_SRIOV_REPO:-https://github.com/strongtz/i915-sriov-dkms.git}"
I915_SRIOV_REF="${I915_SRIOV_REF:-2026.08.12.1}"
I915_SRIOV_COMMIT="${I915_SRIOV_COMMIT:-}"
I915_MAX_VFS="${I915_MAX_VFS:-7}"
PACKAGE_BUILD="${PACKAGE_BUILD:-1}"
CC="${CC:-gcc}"
HOSTCC="${HOSTCC:-$CC}"
CXX="${CXX:-g++}"
HOSTCXX="${HOSTCXX:-$CXX}"
export CC HOSTCC CXX HOSTCXX

DOWNLOAD_DIR="${DOWNLOAD_DIR:-$ROOT_DIR/downloads}"
BUILD_DIR="${BUILD_DIR:-$ROOT_DIR/build}"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/out}"
KERNEL_DIR="$BUILD_DIR/linux-${TARGET_KERNEL_VERSION}"
I915_DIR="$BUILD_DIR/i915-sriov-dkms-${I915_SRIOV_REF}"

mkdir -p "$DOWNLOAD_DIR" "$BUILD_DIR" "$OUT_DIR"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"; }

kmake() {
  make -C "$KERNEL_DIR" CC="$CC" HOSTCC="$HOSTCC" CXX="$CXX" HOSTCXX="$HOSTCXX" "$@"
}

# ---------- 1. kernel source tree ----------
log "Fetching ich777 kernel tree ${KERNEL_RELEASE}"
need_cmd curl
need_cmd tar
mkdir -p "$KERNEL_DIR"
KERNEL_ARCHIVE="$DOWNLOAD_DIR/linux-${KERNEL_RELEASE}.tar.xz"
if [ ! -s "$KERNEL_ARCHIVE" ]; then
  curl -L --fail --retry 3 --retry-delay 2 -o "$KERNEL_ARCHIVE.tmp" "$KERNEL_ARCHIVE_URL"
  mv "$KERNEL_ARCHIVE.tmp" "$KERNEL_ARCHIVE"
fi
if [ -n "$KERNEL_ARCHIVE_SHA256" ]; then
  echo "$KERNEL_ARCHIVE_SHA256  $KERNEL_ARCHIVE" | sha256sum -c - >/dev/null || die "Kernel archive checksum mismatch"
fi

if [ ! -s "$KERNEL_DIR/.config" ] || [ ! -s "$KERNEL_DIR/Module.symvers" ]; then
  log "Extracting kernel tree (this is a large archive, be patient)"
  tar -xf "$KERNEL_ARCHIVE" -C "$KERNEL_DIR"
fi
[ -s "$KERNEL_DIR/.config" ] || die "Kernel tree missing .config"
[ -s "$KERNEL_DIR/Module.symvers" ] || die "Kernel tree missing Module.symvers"

# ---------- 2. strongtz source ----------
log "Fetching i915-sriov-dkms ${I915_SRIOV_REF}"
need_cmd git
if [ -d "$I915_DIR/.git" ]; then
  git -C "$I915_DIR" fetch --depth 1 origin "refs/tags/$I915_SRIOV_REF"
  git -C "$I915_DIR" reset --hard FETCH_HEAD
  git -C "$I915_DIR" clean -ffdqx
else
  git clone --depth 1 --branch "$I915_SRIOV_REF" "$I915_SRIOV_REPO" "$I915_DIR"
fi
if [ -n "$I915_SRIOV_COMMIT" ]; then
  actual="$(git -C "$I915_DIR" rev-parse HEAD)"
  [ "$actual" = "$I915_SRIOV_COMMIT" ] || die "i915 commit mismatch: expected $I915_SRIOV_COMMIT, got $actual"
fi

# ---------- 3. Unraid slab-compat patch ----------
PATCH="$ROOT_DIR/patches/strongtz-2026.08.08-unraid-6x-slab.patch"
log "Applying Unraid slab-compat patch"
git -C "$I915_DIR" apply --check "$PATCH"
git -C "$I915_DIR" apply "$PATCH"

# ---------- 4. build modules ----------
log "Building modules against ${KERNEL_RELEASE} (CC=$CC, JOBS=$JOBS)"
need_cmd make
kmake -j"$JOBS" M="$I915_DIR" modules 2>&1 | tee "$OUT_DIR/build-i915.log"

for m in \
  "$I915_DIR/drivers/gpu/drm/i915/i915.ko" \
  "$I915_DIR/drivers/gpu/drm/i915/kvmgt.ko" \
  "$I915_DIR/drivers/gpu/drm/xe/xe.ko" \
  "$I915_DIR/compat/intel_sriov_compat.ko"; do
  [ -s "$m" ] || die "Missing built module: $m"
done

vermagic="$(modinfo -F vermagic "$I915_DIR/drivers/gpu/drm/i915/i915.ko" 2>/dev/null | head -1)"
log "i915 vermagic: $vermagic"
[ "$(echo "$vermagic" | xargs)" = "$(echo "$KERNEL_RELEASE SMP preempt mod_unload" | xargs)" ] || die "Vermagic mismatch: got '$vermagic', expected '$KERNEL_RELEASE SMP preempt mod_unload'"

# ---------- 5. package .txz ----------
# Keep the upstream dots (2026.09.16). Stripping them packed 2026.08.12.1 into
# the 9-digit 202608121 and 2026.09.16 into the 8-digit 20260916, and `sort -V`
# compares a digit run by numeric value: the older release outranked every later
# one. That made the manager plugin miss updates and made Unraid's own
# upgradepkg refuse the new package as a downgrade.
PKG_VERSION="${I915_SRIOV_REF}"
PKG_NAME="i915-sriov-${PKG_VERSION}-${KERNEL_RELEASE}-${PACKAGE_BUILD}"
STAGE="$BUILD_DIR/stage-${PKG_NAME}"
rm -rf "$STAGE"
mkdir -p "$STAGE/lib/modules/${KERNEL_RELEASE}/updates/compat"
mkdir -p "$STAGE/lib/modules/${KERNEL_RELEASE}/kernel/drivers/gpu/drm/i915"
mkdir -p "$STAGE/lib/modules/${KERNEL_RELEASE}/kernel/drivers/gpu/drm/xe"

cp "$I915_DIR/compat/intel_sriov_compat.ko" \
   "$STAGE/lib/modules/${KERNEL_RELEASE}/updates/compat/"
cp "$I915_DIR/drivers/gpu/drm/i915/i915.ko" \
   "$I915_DIR/drivers/gpu/drm/i915/kvmgt.ko" \
   "$STAGE/lib/modules/${KERNEL_RELEASE}/kernel/drivers/gpu/drm/i915/"
cp "$I915_DIR/drivers/gpu/drm/xe/xe.ko" \
   "$STAGE/lib/modules/${KERNEL_RELEASE}/kernel/drivers/gpu/drm/xe/"

# compress the in-tree modules like the stock Unraid layout (i915.ko.xz)
need_cmd xz
for ko in "$STAGE"/lib/modules/${KERNEL_RELEASE}/kernel/drivers/gpu/drm/i915/*.ko \
          "$STAGE"/lib/modules/${KERNEL_RELEASE}/kernel/drivers/gpu/drm/xe/*.ko; do
  [ -e "$ko" ] || continue
  xz -f -9 --check=crc32 "$ko"
done

# Slackware package metadata: doinst.sh runs after install so modprobe can find
# the freshly installed modules (depmod), matching giganode's makepkg behaviour.
need_cmd md5sum
mkdir -p "$STAGE/install"
cat > "$STAGE/install/doinst.sh" <<'EOF'
# refresh module dependencies so intel_sriov_compat / i915 / kvmgt / xe resolve
if [ -x /sbin/depmod ]; then
  /sbin/depmod -a >/dev/null 2>&1
fi
EOF
chmod 755 "$STAGE/install/doinst.sh"
cat > "$STAGE/install/slack-desc" <<EOF
       |-----handy-ruler------------------------------------------------------|
i915-sriov: i915-sriov - Intel i915 SR-IOV driver for Unraid
i915-sriov:
i915-sriov: Source: https://github.com/strongtz/i915-sriov-dkms
i915-sriov:
i915-sriov: Custom i915 SR-IOV package for Unraid kernel ${KERNEL_RELEASE}
i915-sriov: modules: i915.ko, kvmgt.ko, xe.ko, intel_sriov_compat.ko
i915-sriov:
i915-sriov:
i915-sriov:
i915-sriov:
EOF

log "Packaging ${PKG_NAME}.txz"
tar -cJf "$OUT_DIR/${PKG_NAME}.txz" --owner=root --group=root -C "$STAGE" .
md5sum "$OUT_DIR/${PKG_NAME}.txz" > "$OUT_DIR/${PKG_NAME}.txz.md5"

# installed-modules manifest for verification
{
  echo "i915 module: $(modinfo -F version "$I915_DIR/drivers/gpu/drm/i915/i915.ko" | head -1)"
  echo "vermagic:    $vermagic"
  echo "max_vfs:     $I915_MAX_VFS"
} > "$OUT_DIR/i915-installed-modules.txt"

log "DONE: $OUT_DIR/${PKG_NAME}.txz ($(du -h "$OUT_DIR/${PKG_NAME}.txz" | cut -f1))"
