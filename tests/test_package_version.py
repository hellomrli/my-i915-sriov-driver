from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/package-version.sh"
SHA = "f4cb98f4c28e1f3ac78ca88501a87c86d0c21a88"


def run(command, **kwargs):
    return subprocess.run(command, text=True, capture_output=True, **kwargs)


def executable(path, body):
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
    path.chmod(0o755)


def workflow_step(name):
    # Extract a run block without adding a YAML dependency to the test suite.
    lines = (ROOT / ".github/workflows/build.yml").read_text().splitlines()
    start = lines.index(f"      - name: {name}") + 1
    for index in range(start, len(lines)):
        if lines[index] == "        run: |":
            body = []
            for line in lines[index + 1:]:
                if line and not line.startswith("          "):
                    break
                body.append(line[10:])
            return "\n".join(body)
        if lines[index].startswith("      - name:"):
            break
    raise AssertionError(f"No run block in workflow step {name}")


class DateVersionTests(unittest.TestCase):
    def helper(self, function, value, tz="UTC"):
        return run(
            ["bash", "-c", 'source "$1"; "$2" "$3"',
             "bash", str(HELPER), function, value],
            env={**os.environ, "TZ": tz},
        )

    def test_supported_date_formats(self):
        for value, expected in (
            ("20261002", "2026.10.02"),
            ("2026-10-02", "2026.10.02"),
            ("2026.10.02", "2026.10.02"),
            ("20260109", "2026.01.09"),
            ("2024-02-29", "2024.02.29"),
            ("2000.02.29", "2000.02.29"),
        ):
            with self.subTest(value=value):
                result = self.helper("normalize_package_version", value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected + "\n")

    def test_invalid_dates_and_unsafe_versions(self):
        for value in (
            "", "null", "2026.02.29", "1900.02.29", "20260230",
            "2026-13-01", "2026-00-01", "2026-10-00", "2026.1.09",
            "2026-10.02", "v2026.10.02", "../2026.10.02", "2026.10.02-1",
            "2026.10.02\nextra", "$(date)", "today", " 20261002",
        ):
            with self.subTest(value=value):
                result = self.helper("normalize_package_version", value)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("Invalid package date", result.stderr)

    def test_git_epoch_and_api_timestamp_have_same_utc_date(self):
        epoch = str(int(datetime(2026, 10, 2, 0, 35, tzinfo=timezone.utc).timestamp()))
        for tz in ("UTC", "Pacific/Honolulu", "Pacific/Kiritimati"):
            for timestamp in (epoch, "2026-10-02T00:35:00Z"):
                with self.subTest(tz=tz, timestamp=timestamp):
                    result = self.helper("snapshot_date_utc", timestamp, tz)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "2026-10-02\n")

    def test_invalid_source_timestamps(self):
        for value in ("", "null", "today", "2026-02-30T12:00:00Z", "2026.10.02"):
            with self.subTest(value=value):
                result = self.helper("snapshot_date_utc", value)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")


class WorkflowRefTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="i915-ref-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        self.output = self.directory / "output"
        self.requests = self.directory / "requests"
        executable(self.bin / "curl", '''printf '%s\\n' "$*" >> "$MOCK_REQUESTS"
printf '%s\\n' "$MOCK_RESPONSE"
exit "${MOCK_STATUS:-0}"
''')
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GITHUB_OUTPUT": str(self.output),
            "MOCK_REQUESTS": str(self.requests),
            "MOCK_STATUS": "0",
            "GH_TOKEN": "test-token",
            "TZ": "Pacific/Honolulu",
        }
        self.response = {
            "sha": SHA,
            "commit": {"committer": {"date": "2026-10-02T00:35:00Z"}},
        }

    def resolve(self, response=None, status="0"):
        self.output.write_text("")
        self.requests.write_text("")
        return run(
            ["bash", "-c", workflow_step("Resolve i915 ref")], cwd=ROOT,
            env={**self.env, "MOCK_STATUS": status,
                 "MOCK_RESPONSE": json.dumps(self.response if response is None else response)},
        )

    def test_latest_supported_master_is_resolved_once_and_pinned(self):
        result = self.resolve()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_text(), f"i915_ref={SHA}\npkg_version=2026.10.02\n")
        requests = self.requests.read_text().splitlines()
        self.assertEqual(len(requests), 1)
        self.assertIn("Authorization: Bearer test-token", requests[0])
        self.assertTrue(requests[0].endswith("/commits/master"), requests)
        self.assertIn("2026.10.02 UTC", result.stdout)
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertNotIn("i915_ref:", workflow)
        self.assertIn("ref=master", workflow_step("Resolve i915 ref"))

    def test_bad_api_data_is_rejected_before_outputs(self):
        for response in (
            {}, {"sha": "null"}, {"sha": "master"},
            {"sha": SHA, "commit": {"committer": {"date": None}}},
            {"sha": SHA, "commit": {"committer": {"date": "today"}}},
            {"sha": SHA, "commit": {"committer": {"date": "2026-02-30T00:00:00Z"}}},
        ):
            with self.subTest(response=response):
                result = self.resolve(response)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.output.read_text(), "")

    def test_http_failure_does_not_publish_outputs(self):
        result = self.resolve(status="22")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_text(), "")

    def test_build_passes_pinned_commit_for_verification(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        build_step = workflow.split("      - name: Build i915 SR-IOV package\n", 1)[1]
        build_step = build_step.split("      - name:", 1)[0]
        for variable in ("I915_SRIOV_REF", "I915_SRIOV_COMMIT"):
            self.assertIn(f"{variable}: ${{{{ steps.resolve.outputs.i915_ref }}}}", build_step)

    def test_workflow_uses_gcc_16_and_runs_regressions(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertIn("image: gcc:16.2.0", workflow)
        self.assertIn("python3 -m unittest discover -s tests -v",
                      workflow_step("Test build tooling"))


class WorkflowDedupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="i915-release-dedup-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        self.output = self.directory / "output"
        self.requests = self.directory / "requests"
        executable(self.bin / "curl", '''printf '%s\\n' "$*" >> "$MOCK_REQUESTS"
printf '%s\\n' "$MOCK_RELEASE"
''')
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GH_TOKEN": "test-token",
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_REPOSITORY": "test/repo",
            "KERNEL_RELEASE": "6.18.58-Unraid",
            "I915_REF": SHA,
            "PKG_VERSION": "2026.10.02",
            "MOCK_REQUESTS": str(self.requests),
        }

    def check(self, body, event="schedule", complete=True):
        self.output.write_text("")
        self.requests.write_text("")
        package = "i915-sriov-2026.10.02-6.18.58-Unraid-1.txz"
        assets = [{"name": package}]
        if complete:
            assets.append({"name": package + ".md5"})
        release = json.dumps({"id": 123, "body": body, "assets": assets})
        result = run(
            ["bash", "-c", workflow_step("Check if this version is already built")],
            cwd=ROOT, env={**self.env, "GITHUB_EVENT_NAME": event, "MOCK_RELEASE": release},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.output.read_text(), result

    def test_already_built_master_commit_is_skipped(self):
        body = f"Auto-built from strongtz/i915-sriov-dkms @ {SHA} "
        output, result = self.check(body + "(snapshot 2026.10.02, i915-only)")
        self.assertEqual(output, "new_version=false\n")
        self.assertIn("already built; skipping", result.stdout)

    def test_new_master_sha_on_same_day_is_built(self):
        previous_sha = "0" * 40
        body = f"Auto-built from strongtz/i915-sriov-dkms @ {previous_sha} "
        output, result = self.check(body + "(snapshot 2026.10.02, i915-only)")
        self.assertEqual(output, "new_version=true\n")
        self.assertIn(SHA, result.stdout)

    def test_old_date_only_release_is_rebuilt_once(self):
        output, _ = self.check("Auto-built from strongtz/i915-sriov-dkms @ 2026.09.16")
        self.assertEqual(output, "new_version=true\n")

    def test_incomplete_assets_are_rebuilt(self):
        body = f"Auto-built from strongtz/i915-sriov-dkms @ {SHA} "
        output, _ = self.check(body + "(snapshot 2026.10.02, i915-only)", complete=False)
        self.assertEqual(output, "new_version=true\n")

    def test_manual_dispatch_always_builds(self):
        body = f"Auto-built from strongtz/i915-sriov-dkms @ {SHA} "
        output, result = self.check(body + "(snapshot 2026.10.02, i915-only)", event="workflow_dispatch")
        self.assertEqual(output, "new_version=true\n")
        self.assertIn("Manual dispatch: always build", result.stdout)


class WorkflowPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="i915-release-publish-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        self.requests = self.directory / "requests"
        curl = self.bin / "curl"
        curl.write_text('''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
url = args[-1]
method = args[args.index("-X") + 1] if "-X" in args else "GET"
request = {"method": method, "url": url}
if "-d" in args:
    request["data"] = json.loads(args[args.index("-d") + 1])
with open(os.environ["MOCK_REQUESTS"], "a") as log:
    log.write(json.dumps(request) + "\\n")
if "/tags/" in url:
    if os.environ.get("MOCK_NEW_RELEASE") == "1":
        sys.exit(22)
    print(json.dumps({"id": 123, "body": os.environ["MOCK_RELEASE_BODY"]}))
elif "uploads.github.com" in url:
    if os.environ.get("MOCK_FAIL_UPLOAD") == "1":
        sys.exit(22)
    print("{}")
elif method == "POST":
    print('{"id": 123}')
elif "assets?" in url:
    print("[]")
else:
    print("{}")
''')
        curl.chmod(0o755)
        out = self.directory / "out"
        out.mkdir()
        self.package = "i915-sriov-2026.10.02-6.18.58-Unraid-1.txz"
        (out / self.package).write_text("fixture package\n")
        (out / f"{self.package}.md5").write_text("fixture checksum\n")
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GH_TOKEN": "test-token",
            "GITHUB_REPOSITORY": "test/repo",
            "KERNEL_RELEASE": "6.18.58-Unraid",
            "I915_REF": SHA,
            "PKG_VERSION": "2026.10.02",
            "MOCK_REQUESTS": str(self.requests),
            "MOCK_RELEASE_BODY": "User release notes.\n\n"
                                 "Auto-built from strongtz/i915-sriov-dkms @ old "
                                 "(snapshot 2026.09.16, i915-only)",
            "MOCK_FAIL_UPLOAD": "0",
            "MOCK_NEW_RELEASE": "0",
        }

    def publish(self, **overrides):
        self.requests.write_text("")
        result = run(["bash", "-c", workflow_step("Publish Release")],
                     cwd=self.directory, env={**self.env, **overrides})
        requests = [json.loads(line) for line in self.requests.read_text().splitlines()]
        return result, requests

    def test_master_is_marked_built_after_both_uploads(self):
        result, requests = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        uploads = [index for index, request in enumerate(requests)
                   if "uploads.github.com" in request["url"]]
        updates = [index for index, request in enumerate(requests)
                   if request["method"] == "PATCH"]
        self.assertEqual(len(uploads), 2)
        self.assertEqual(len(updates), 1)
        self.assertLess(max(uploads), updates[0])
        body = requests[updates[0]]["data"]["body"]
        self.assertIn("User release notes.", body)
        self.assertIn(f"strongtz/i915-sriov-dkms @ {SHA}", body)
        self.assertNotIn("@ old", body)

    def test_failed_upload_does_not_mark_master_built(self):
        result, requests = self.publish(MOCK_FAIL_UPLOAD="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(request["method"] == "PATCH" for request in requests))

    def test_new_release_is_not_marked_built_before_upload(self):
        result, requests = self.publish(MOCK_NEW_RELEASE="1", MOCK_RELEASE_BODY="")
        self.assertEqual(result.returncode, 0, result.stderr)
        create = next(request for request in requests
                      if request["method"] == "POST" and "uploads." not in request["url"])
        self.assertNotIn(SHA, create["data"]["body"])
        update = next(request for request in requests if request["method"] == "PATCH")
        self.assertIn(SHA, update["data"]["body"])


class MockBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="i915-build-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.project = self.directory / "project"
        scripts = self.project / "scripts"
        scripts.mkdir(parents=True)
        for name in ("build-i915-sriov.sh", "package-version.sh"):
            shutil.copy2(ROOT / "scripts" / name, scripts / name)
        patches = self.project / "patches"
        patches.mkdir()
        (patches / "strongtz-2026.08.08-unraid-6x-slab.patch").write_text(
            "diff --git a/fixture.txt b/fixture.txt\n"
            "--- a/fixture.txt\n+++ b/fixture.txt\n@@ -1 +1 @@\n-before\n+after\n"
        )
        (patches / "strongtz-master-drm-client-event-conftest.patch").write_text(
            "diff --git a/fixture.txt b/fixture.txt\n"
            "--- a/fixture.txt\n+++ b/fixture.txt\n@@ -1 +1 @@\n-after\n+compatible\n"
        )
        self.source = self.directory / "upstream"
        self.source.mkdir()
        (self.source / "fixture.txt").write_text("before\n")
        (self.source / "Makefile").write_text(
            'DKMS_MODULE_VERSION := "2026.09.16-sriov"\n'
            'obj-m += compat/\nobj-m += drivers/gpu/drm/i915/\nobj-m += drivers/gpu/drm/xe/\n'
        )
        (self.source / "dkms.conf").write_text('PACKAGE_VERSION="2026.09.16"\n')
        git_env = {
            **os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "GIT_AUTHOR_DATE": "2026-01-09T00:00:00Z",
            # Committer's local date is Oct 3, but UTC is still Oct 2.
            "GIT_COMMITTER_DATE": "2026-10-03T00:15:00+14:00",
        }
        for args in (
            ["init", "-q", "--initial-branch=master"], ["add", "."],
            ["-c", "commit.gpgsign=false", "commit", "-qm", "fixture"],
        ):
            result = run(["git", "-C", str(self.source), *args], env=git_env)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.sha = run(["git", "-C", str(self.source), "rev-parse", "HEAD"], check=True).stdout.strip()
        self.out = self.directory / "out"
        downloads = self.directory / "downloads"
        downloads.mkdir()
        kernel = self.directory / "kernel"
        kernel.mkdir()
        (kernel / ".config").write_text("CONFIG_TEST=y\n")
        (kernel / "Module.symvers").write_text("fixture\n")
        archive = downloads / "linux-6.18.54-Unraid.tar.xz"
        with tarfile.open(archive, "w:xz") as tar:
            for path in kernel.iterdir():
                tar.add(path, arcname=path.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        self.compiler_calls = self.directory / "compiler-calls"
        executable(self.bin / "make", '''printf 'called\\n' >> "$MOCK_COMPILER_CALLS"
module_dir=""
for arg in "$@"; do
  case "$arg" in M=*) module_dir="${arg#M=}" ;; esac
done
[ -n "$module_dir" ]
mkdir -p "$module_dir/compat" "$module_dir/drivers/gpu/drm/i915"
printf 'mock compat module\\n' > "$module_dir/compat/intel_sriov_compat.ko"
printf 'mock i915 module\\n' > "$module_dir/drivers/gpu/drm/i915/i915.ko"
printf 'mock compiler output\\n'
''')
        executable(self.bin / "modinfo", '''case "$2" in
  vermagic) printf '%s SMP preempt mod_unload\\n' "$KERNEL_RELEASE" ;;
  version) printf '2026.09.16-sriov\\n' ;;
  *) exit 1 ;;
esac
''')
        executable(self.bin / "curl", "printf 'Unexpected network request\\n' >&2\nexit 99\n")
        self.env = {
            **os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "I915_SRIOV_REPO": str(self.source), "I915_SRIOV_REF": "master",
            "I915_SRIOV_COMMIT": self.sha,
            "TARGET_KERNEL_VERSION": "6.18.54", "KERNEL_RELEASE": "6.18.54-Unraid",
            "KERNEL_ARCHIVE_SHA256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "BUILD_DIR": str(self.directory / "build"), "DOWNLOAD_DIR": str(downloads),
            "OUT_DIR": str(self.out), "PACKAGE_BUILD": "1", "JOBS": "1",
            "MOCK_COMPILER_CALLS": str(self.compiler_calls), "TZ": "Pacific/Honolulu",
        }
        self.env.pop("PACKAGE_VERSION", None)

    def build(self, **overrides):
        return run(["bash", str(self.project / "scripts/build-i915-sriov.sh")],
                   env={**self.env, **overrides}, cwd=self.project)

    def assert_package(self, version, build="1"):
        name = f"i915-sriov-{version}-6.18.54-Unraid-{build}.txz"
        package = self.out / name
        self.assertTrue(package.is_file(), name)
        checksum = (self.out / f"{name}.md5").read_text().split()[0]
        self.assertEqual(checksum, hashlib.md5(package.read_bytes()).hexdigest())
        manifest = (self.out / "i915-installed-modules.txt").read_text()
        self.assertIn(f"upstream commit: {self.sha}", manifest)
        self.assertIn("upstream date:   2026-10-02 (UTC)", manifest)
        self.assertIn(f"package version: {version}", manifest)
        self.assertIn(f"package file:    {name}", manifest)
        self.assertIn("compiler:        ", manifest)
        self.assertIn("i915 module:     2026.09.16-sriov", manifest)
        log = (self.out / "build-i915.log").read_text()
        self.assertIn(self.sha, log)
        self.assertIn("Upstream date: 2026-10-02 (UTC)", log)
        self.assertIn(f"Package version: {version}", log)
        self.assertIn(name, log)
        self.assertIn("mock compiler output", log)
        self.assertIn("Compiler: ", log)
        with tarfile.open(package) as tar:
            self.assertIn("./lib/modules/6.18.54-Unraid/kernel/drivers/gpu/drm/i915/i915.ko.xz", tar.getnames())
            self.assertIn("./lib/modules/6.18.54-Unraid/updates/compat/intel_sriov_compat.ko", tar.getnames())
        fetched = Path(self.env["BUILD_DIR"]) / "i915-sriov-dkms-master"
        self.assertEqual((fetched / "fixture.txt").read_text(), "compatible\n")
        self.assertEqual((fetched / "dkms.conf").read_text(), 'PACKAGE_VERSION="2026.09.16"\n')

    def test_default_version_uses_actual_committer_utc_date(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_package("2026.10.02")

    def test_empty_override_uses_source_date(self):
        result = self.build(PACKAGE_VERSION="")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_package("2026.10.02")

    def test_kernel_directory_is_derived_from_release(self):
        self.env.pop("TARGET_KERNEL_VERSION")
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_package("2026.10.02")
        kernel = Path(self.env["BUILD_DIR"]) / "linux-6.18.54"
        self.assertTrue((kernel / ".config").is_file())

    def test_override_formats_and_rebuild_number(self):
        for version in ("20260109", "2026-01-09", "2026.01.09"):
            with self.subTest(version=version):
                result = self.build(PACKAGE_VERSION=version, PACKAGE_BUILD="2")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assert_package("2026.01.09", "2")

    def test_invalid_override_stops_before_compilation(self):
        result = self.build(PACKAGE_VERSION="2026.02.29")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid package date", result.stderr)
        self.assertFalse(self.compiler_calls.exists())
        self.assertEqual(list(self.out.glob("*.txz")), [])

    def test_wrong_sha_stops_before_compilation(self):
        result = self.build(I915_SRIOV_COMMIT="0" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("i915 commit mismatch", result.stderr)
        self.assertFalse(self.compiler_calls.exists())


if __name__ == "__main__":
    unittest.main()
