#!/usr/bin/env python3
"""Content-based CI build planning and append-only GitHub releases."""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
STATE_PREFIX = "<!-- i915-sriov-build: "
STATE_SUFFIX = " -->"
DRIVERS = ("i915", "xe")
IGNORED_SOURCE_DIRS = (".github/", "docs/", "debian/")
# These are not used by our direct Kbuild invocation. Unknown paths remain
# inputs, so a new shared helper cannot silently bypass the build check.
IGNORED_SOURCE_FILES = {
    ".clang-format", ".gitignore", "COPYING", "README.md", "PKGBUILD",
    "dkms.conf", "flake.nix", "flake.lock", "i915-set-sriov-numvfs.conf",
    "i915-sriov-dkms.install",
}


def digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def require_match(value, pattern, name):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError(f"Invalid {name}: {value!r}")
    return value


def validate_driver(driver):
    if driver not in DRIVERS:
        raise ValueError(f"Unsupported Intel driver: {driver!r}")
    return driver


def source_fingerprint(tree, driver="i915"):
    validate_driver(driver)
    if (not isinstance(tree, dict) or tree.get("truncated") is not False
            or not isinstance(tree.get("tree"), list)):
        raise ValueError("Incomplete upstream Git tree; refusing to guess build inputs")
    inputs = {}
    for entry in tree["tree"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"]:
            raise ValueError("Invalid upstream Git tree entry")
        path = entry["path"]
        # Parent tree SHAs include xe/docs changes; hash only leaf entries.
        if entry["type"] == "tree":
            continue
        if entry["type"] not in ("blob", "commit"):
            raise ValueError(f"Unknown Git object type for {path}")
        if path in IGNORED_SOURCE_FILES or path.startswith(IGNORED_SOURCE_DIRS):
            continue
        if driver == "i915" and path.startswith("drivers/gpu/drm/xe/"):
            continue
        # Xe builds shared i915 display code and includes i915 root headers.
        # Keep that tree for xe; dropping the other obj-m is not a dependency filter.
        if path in inputs:
            raise ValueError(f"Duplicate upstream path: {path}")
        # A symlink can point into an excluded directory. Do not treat its
        # target pathname as a complete content fingerprint.
        if entry["type"] != "blob" or entry["mode"] not in ("100644", "100755"):
            raise ValueError(f"Unsupported indirect upstream input: {path}")
        require_match(entry["sha"], r"[0-9a-f]{40}", "Git object SHA")
        inputs[path] = [entry["mode"], entry["sha"]]
    for required in ("Makefile", "conftest.sh"):
        if required not in inputs:
            raise ValueError(f"Upstream tree is missing {required}")
    required_dirs = {f"drivers/gpu/drm/{driver}/", "drivers/gpu/drm/i915/", "compat/", "include/"}
    for directory in sorted(required_dirs):
        if not any(path.startswith(directory) for path in inputs):
            raise ValueError(f"Upstream tree is missing {directory}")
    return digest({"schema": 2, "driver": driver, "inputs": inputs})


def local_source_tree(directory):
    output = subprocess.check_output(
        ["git", "-C", str(directory), "ls-tree", "-rz", "HEAD"])
    entries = []
    for line in output.decode().split("\0"):
        if not line:
            continue
        attributes, path = line.split("\t", 1)
        mode, kind, sha = attributes.split()
        entries.append({"path": path, "mode": mode, "type": kind, "sha": sha})
    return {"truncated": False, "tree": entries}


def recipe_fingerprint(root):
    inputs = {}
    for directory in (".github", "scripts", "patches", "config"):
        for path in sorted((root / directory).rglob("*")):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if path.is_symlink():
                raise ValueError(f"Unsupported symlink in build recipe: {path}")
            elif path.is_file():
                content = path.read_bytes()
            else:
                continue
            inputs[path.relative_to(root).as_posix()] = [
                stat.S_IMODE(path.lstat().st_mode), hashlib.sha256(content).hexdigest()]
    if ".github/workflows/build.yml" not in inputs or "scripts/build-intel-sriov.sh" not in inputs:
        raise ValueError("Missing local build recipe")
    return digest({"schema": 1, "inputs": inputs})


def build_key(record):
    return digest({key: record[key] for key in (
        "schema", "driver", "source_fingerprint", "recipe_fingerprint", "kernel_release", "compiler")})


def package_name(version, kernel, number, driver="i915"):
    return f"{validate_driver(driver)}-sriov-{version}-{kernel}-{number}.txz"


def release_tag(kernel, driver="i915"):
    # Separate releases avoid concurrent i915/xe publishers replacing each
    # other's build metadata. Existing i915 URLs remain unchanged.
    return kernel if validate_driver(driver) == "i915" else f"xe-{kernel}"


def state_prefix(driver):
    return f"<!-- {validate_driver(driver)}-sriov-build: "


def validate_record(record):
    if (not isinstance(record, dict) or type(record.get("schema")) is not int
            or record["schema"] != 2):
        raise ValueError("Unknown build metadata schema")
    validate_driver(record["driver"])
    require_match(record["kernel_release"], r"[0-9]+\.[0-9]+\.[0-9]+-Unraid", "kernel release")
    require_match(record["source_commit"], r"[0-9a-f]{40}", "upstream commit")
    require_match(record["repository_commit"], r"[0-9a-f]{40}", "local repository commit")
    for key in ("source_fingerprint", "recipe_fingerprint", "build_key"):
        require_match(record[key], r"[0-9a-f]{64}", key)
    version = require_match(record["package_version"], r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}", "package date")
    datetime.strptime(version, "%Y.%m.%d")
    number = record["package_build"]
    if type(number) is not int or number < 1:
        raise ValueError("Package build must be a positive integer")
    if not isinstance(record["compiler"], str) or not record["compiler"]:
        raise ValueError("Missing compiler identity")
    if record["package"] != package_name(version, record["kernel_release"], number, record["driver"]):
        raise ValueError("Package name does not match build metadata")
    if record["build_key"] != build_key(record):
        raise ValueError("Build metadata fingerprint mismatch")


def read_record(body, driver="i915"):
    prefix = state_prefix(driver)
    lines = [line for line in body.splitlines() if line.startswith(prefix)]
    if len(lines) != 1 or not lines[0].endswith(STATE_SUFFIX):
        return None
    try:
        record = json.loads(lines[0][len(prefix):-len(STATE_SUFFIX)])
        validate_record(record)
        return record if record["driver"] == driver else None
    except (KeyError, TypeError, ValueError):
        return None


def release_body(body, record):
    driver = record["driver"]
    prefix = state_prefix(driver)
    source_line = re.compile(
        rf"^Auto-built from strongtz/i915-sriov-dkms @ .* \(snapshot .*, {driver}-only\)$")
    notes = "\n".join(line for line in body.splitlines()
                      if not line.startswith(prefix) and not source_line.fullmatch(line)).strip()
    source = (f"Auto-built from strongtz/i915-sriov-dkms @ {record['source_commit']} "
              f"(snapshot {record['package_version']}, {driver}-only)")
    state = prefix + json.dumps(record, sort_keys=True, separators=(",", ":")) + STATE_SUFFIX
    return "\n\n".join(part for part in (notes, source, state) if part)


def next_build(assets, version, kernel, requested="auto", driver="i915"):
    pattern = re.compile(re.escape(f"{validate_driver(driver)}-sriov-{version}-{kernel}-") + r"([0-9]+)\.txz(?:\.md5)?")
    numbers = [int(match[1]) for asset in assets
               if (match := pattern.fullmatch(asset["name"]))]
    highest = max(numbers, default=0)
    if requested in ("", "auto"):
        return highest + 1
    require_match(requested, r"[1-9][0-9]*", "package build (use auto or a positive integer)")
    number = int(requested)
    if number <= highest:
        raise ValueError(f"Package build {number} must exceed existing build {highest}; use auto")
    return number


def plan_build(candidate, body, assets, force=False, requested="auto"):
    driver = validate_driver(candidate["driver"])
    validate_record({**candidate, "package_build": 1,
                     "package": package_name(candidate["package_version"], candidate["kernel_release"], 1, driver)})
    previous = read_record(body, driver)
    complete = {asset["name"] for asset in assets
                if asset.get("state") == "uploaded" and asset.get("size", 0) > 0}
    if not force and previous and previous["build_key"] == candidate["build_key"]:
        package = previous["package"]
        if package in complete and package + ".md5" in complete:
            return {"build": False, "record": previous, "reason": f"Effective {driver} inputs already built; skipping"}
    record = {**candidate}
    record["package_build"] = next_build(assets, record["package_version"], record["kernel_release"], requested, driver)
    record["package"] = package_name(record["package_version"], record["kernel_release"], record["package_build"], driver)
    validate_record(record)
    return {"build": True, "record": record,
            "reason": "Manual dispatch: always build" if force else "New inputs, legacy metadata or incomplete assets"}


class GitHub:
    def __init__(self, repository, token):
        require_match(repository, r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", "GitHub repository")
        if not token:
            raise ValueError("GH_TOKEN is required")
        self.repository = repository
        self.token = token

    def request(self, method, path, body=None, upload=None):
        host = "uploads.github.com" if upload is not None else "api.github.com"
        headers = {"Authorization": f"Bearer {self.token}",
                   "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        data = None
        if upload is not None:
            data = upload.read_bytes()
            headers["Content-Type"] = "application/octet-stream"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = Request(f"https://{host}/repos/{self.repository}/{path}",
                          data=data, headers=headers, method=method)
        with urlopen(request, timeout=300 if upload is not None else 60) as response:
            return json.load(response)

    def release(self, kernel, driver="i915"):
        try:
            release = self.request("GET", f"releases/tags/{quote(release_tag(kernel, driver), safe='')}")
        except HTTPError as error:
            if error.code == 404:
                error.close()
                return None
            raise
        if (not isinstance(release, dict) or type(release.get("id")) is not int
                or not isinstance(release.get("body") or "", str)):
            raise ValueError("Invalid release response")
        return release

    def assets(self, release_id):
        assets, page = [], 1
        while True:
            batch = self.request("GET", f"releases/{release_id}/assets?per_page=100&page={page}")
            if not isinstance(batch, list):
                raise ValueError("Invalid release asset response")
            for asset in batch:
                if (not isinstance(asset, dict) or not isinstance(asset.get("name"), str)
                        or not isinstance(asset.get("state"), str)
                        or type(asset.get("size")) is not int or asset["size"] < 0):
                    raise ValueError("Invalid release asset entry")
            assets.extend(batch)
            if len(batch) < 100:
                return assets
            page += 1


def publish(api, plan, out):
    if not isinstance(plan, dict) or plan.get("build") is not True:
        raise ValueError("Refusing to publish a skipped build")
    record = plan["record"]
    validate_record(record)
    package = out / record["package"]
    checksum = out / (record["package"] + ".md5")
    if not package.is_file() or package.stat().st_size == 0:
        raise ValueError(f"Missing package: {package}")
    expected = hashlib.md5(package.read_bytes()).hexdigest()
    if not checksum.is_file() or checksum.read_text().split()[:1] != [expected]:
        raise ValueError("Package MD5 is missing or does not match")
    release = api.release(record["kernel_release"], record["driver"])
    body = (release.get("body") or "") if release else ""
    if release:
        names = {asset["name"] for asset in api.assets(release["id"])}
        if names.intersection((package.name, checksum.name)):
            raise ValueError("Release asset already exists; re-plan with a new build number")
    else:
        release = api.request("POST", "releases", {
            "tag_name": release_tag(record["kernel_release"], record["driver"]),
            "name": f"{record['driver']} SR-IOV for {record['kernel_release']}", "body": "Build in progress."})
    rid = release["id"]
    # Publish the checksum first, so an upload failure cannot expose a new
    # package without its checksum. Never delete or overwrite existing assets.
    for path in (checksum, package):
        asset = api.request("POST", f"releases/{rid}/assets?name={quote(path.name, safe='')}", upload=path)
        if (not isinstance(asset, dict) or asset.get("name") != path.name
                or asset.get("state") != "uploaded" or asset.get("size") != path.stat().st_size):
            raise ValueError(f"Upload did not complete: {path.name}")
    api.request("PATCH", f"releases/{rid}", {"body": release_body(body, record)})
    print(f"Published {package.name} to release {release_tag(record['kernel_release'], record['driver'])}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("source-fingerprint", "source-fingerprints", "plan", "publish"))
    parser.add_argument("--driver", choices=DRIVERS, default=os.environ.get("DRIVER", "i915"))
    parser.add_argument("--source-dir", type=Path, help="Fingerprint a local Git HEAD without network access")
    args = parser.parse_args()
    driver = validate_driver(args.driver)
    if args.command in ("source-fingerprint", "source-fingerprints"):
        if args.source_dir:
            tree = local_source_tree(args.source_dir)
        else:
            tree_sha = require_match(os.environ["I915_TREE"], r"[0-9a-f]{40}", "upstream tree SHA")
            api = GitHub("strongtz/i915-sriov-dkms", os.environ["GH_TOKEN"])
            tree = api.request("GET", f"git/trees/{tree_sha}?recursive=1")
            if not isinstance(tree, dict) or tree.get("sha") != tree_sha:
                raise ValueError("Upstream tree SHA mismatch")
        if args.command == "source-fingerprints":
            print(json.dumps({name: source_fingerprint(tree, name) for name in DRIVERS}, sort_keys=True))
        else:
            print(source_fingerprint(tree, driver))
        return
    api = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    out = ROOT / "out"
    plan_file = out / "build-plan.json"
    if args.command == "publish":
        publish(api, json.loads(plan_file.read_text()), out)
        return
    compiler = subprocess.check_output([os.environ.get("CC") or "gcc", "--version"], text=True).splitlines()[0]
    candidate = {
        "schema": 2, "driver": driver, "source_commit": os.environ["I915_REF"],
        "source_fingerprint": os.environ["SOURCE_FINGERPRINT"],
        "recipe_fingerprint": recipe_fingerprint(ROOT), "compiler": compiler,
        "kernel_release": os.environ["KERNEL_RELEASE"], "package_version": os.environ["PKG_VERSION"],
        "repository_commit": os.environ["GITHUB_SHA"],
    }
    candidate["build_key"] = build_key(candidate)
    # Validate before using kernel/version values in URLs or output files.
    validate_record({**candidate, "package_build": 1,
                     "package": package_name(candidate["package_version"], candidate["kernel_release"], 1, driver)})
    release = api.release(candidate["kernel_release"], driver)
    assets = api.assets(release["id"]) if release else []
    plan = plan_build(candidate, (release.get("body") or "") if release else "", assets,
                      force=os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch",
                      requested=os.environ.get("PACKAGE_BUILD_INPUT", "auto"))
    out.mkdir(exist_ok=True)
    plan_file.write_text(json.dumps(plan, indent=2) + "\n")
    record = plan["record"]
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"new_version={str(plan['build']).lower()}\n")
        for key in ("package_build", "recipe_fingerprint", "build_key"):
            output.write(f"{key}={record[key]}\n")
    print(f"{plan['reason']}: {record['package']} (build key {record['build_key']})")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"ERROR: {error}")
