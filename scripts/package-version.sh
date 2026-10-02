#!/usr/bin/env bash
# Shared source-date version helpers for local and GitHub Actions builds.

snapshot_date_utc() {
  local timestamp="$1"
  if [[ "$timestamp" =~ ^[0-9]+$ ]]; then
    timestamp="@$timestamp"
  elif [[ ! "$timestamp" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
    printf 'Invalid upstream commit timestamp: %s\n' "$timestamp" >&2
    return 1
  fi
  LC_ALL=C date -u --date="$timestamp" +%F
}

normalize_package_version() {
  local version="$1" iso canonical
  if [[ "$version" =~ ^[0-9]{8}$ ]]; then
    iso="${version:0:4}-${version:4:2}-${version:6:2}"
  elif [[ "$version" =~ ^[0-9]{4}(-[0-9]{2}-[0-9]{2}|\.[0-9]{2}\.[0-9]{2})$ ]]; then
    iso="${version//./-}"
  else
    printf 'Invalid package date: %s (expected YYYY.MM.DD, YYYY-MM-DD or YYYYMMDD)\n' "$version" >&2
    return 1
  fi
  if ! canonical="$(LC_ALL=C date -u --date="$iso" +%F 2>/dev/null)" || [ "$canonical" != "$iso" ]; then
    printf 'Invalid package date: %s\n' "$version" >&2
    return 1
  fi
  printf '%s\n' "${iso//-/.}"
}
