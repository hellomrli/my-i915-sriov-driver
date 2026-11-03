from datetime import datetime, timezone
from copy import deepcopy
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
TREE_SHA = "b" * 40


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
            "commit": {"committer": {"date": "2026-10-02T00:35:00Z"},
                       "tree": {"sha": TREE_SHA}},
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
        self.assertEqual(self.output.read_text(),
                         f"i915_ref={SHA}\ni915_tree={TREE_SHA}\npkg_version=2026.10.02\n")
        requests = self.requests.read_text().splitlines()
        self.assertEqual(len(requests), 1)
        self.assertIn("Authorization: Bearer test-token", requests[0])
        self.assertTrue(requests[0].endswith("/commits/master"), requests)
        self.assertIn("2026.10.02 UTC", result.stdout)
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertNotIn("i915_ref:", workflow.split("jobs:\n", 1)[0])
        self.assertIn("ref=master", workflow_step("Resolve i915 ref"))

    def test_bad_api_data_is_rejected_before_outputs(self):
        responses = [{}, {"sha": "null"}, {"sha": "master"}, {"sha": SHA}]
        for date in (None, "today", "2026-02-30T00:00:00Z"):
            response = deepcopy(self.response)
            response["commit"]["committer"]["date"] = date
            responses.append(response)
        for tree in ({}, {"sha": None}, {"sha": "master"}):
            response = deepcopy(self.response)
            response["commit"]["tree"] = tree
            responses.append(response)
        for response in responses:
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
        build_step = workflow.split("      - name: Build Intel SR-IOV package\n", 1)[1]
        build_step = build_step.split("      - name:", 1)[0]
        for variable in ("I915_SRIOV_REF", "I915_SRIOV_COMMIT"):
            self.assertIn(f"{variable}: ${{{{ needs.resolve.outputs.i915_ref }}}}", build_step)

    def test_workflow_uses_gcc_16_and_runs_regressions(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertIn("image: gcc:16.2.0", workflow)
        self.assertIn("python3 -m unittest discover -s tests -v",
                      workflow_step("Test build tooling"))


class WorkflowKernelMatrixTests(unittest.TestCase):
    def test_official_resolver_replaces_fixed_kernel_and_manual_history_inputs(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        block = workflow_step("Resolve kernel release")
        self.assertIn("python3 scripts/resolve-unraid.py", block)
        self.assertNotIn("6.18.58-Unraid", block)
        self.assertNotIn("inputs.kernel_release", workflow)
        self.assertNotIn("kernel_release:", workflow.split("jobs:", 1)[0])

    def test_matrix_shares_one_sha_and_does_not_cancel_other_kernel(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertIn("needs: resolve", workflow)
        self.assertIn("fail-fast: false", workflow)
        self.assertIn("kernel_release: ${{ fromJSON(needs.resolve.outputs.kernel_releases) }}",
                      workflow)
        self.assertEqual(workflow.count("KERNEL_RELEASE: ${{ matrix.kernel_release }}"), 2)
        self.assertNotIn("steps.resolve.outputs", workflow.split("  build:\n", 1)[1])


class MockBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="i915-build-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.project = self.directory / "project"
        scripts = self.project / "scripts"
        scripts.mkdir(parents=True)
        for name in ("build-i915-sriov.sh", "build-xe-sriov.sh", "build-intel-sriov.sh", "package-version.sh", "ci-build.py"):
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
        for name in ("conftest.sh", "drivers/gpu/drm/i915/i915.c", "drivers/gpu/drm/xe/xe.c", "compat/Makefile", "include/config.h"):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        self.git_env = {
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
            result = run(["git", "-C", str(self.source), *args], env=self.git_env)
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
other=xe; [ "$DRIVER" != xe ] || other=i915
grep -q "obj-m += drivers/gpu/drm/$DRIVER/" "$module_dir/Makefile"
! grep -q "obj-m += drivers/gpu/drm/$other/" "$module_dir/Makefile"
mkdir -p "$module_dir/compat" "$module_dir/drivers/gpu/drm/$DRIVER"
printf 'mock compat module\\n' > "$module_dir/compat/intel_sriov_compat.ko"
printf 'mock driver module\\n' > "$module_dir/drivers/gpu/drm/$DRIVER/$DRIVER.ko"
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
        for key in ("PACKAGE_VERSION", "SOURCE_FINGERPRINT", "RECIPE_FINGERPRINT", "BUILD_KEY"):
            self.env.pop(key, None)

    def build(self, driver="i915", **overrides):
        return run(["bash", str(self.project / f"scripts/build-{driver}-sriov.sh")],
                   env={**self.env, **overrides}, cwd=self.project)

    def assert_package(self, version, build="1", fixture="compatible\n", driver="i915"):
        name = f"{driver}-sriov-{version}-6.18.54-Unraid-{build}.txz"
        package = self.out / name
        self.assertTrue(package.is_file(), name)
        checksum = (self.out / f"{name}.md5").read_text().split()[0]
        self.assertEqual(checksum, hashlib.md5(package.read_bytes()).hexdigest())
        manifest = (self.out / f"{driver}-installed-modules.txt").read_text()
        self.assertIn(f"upstream commit: {self.sha}", manifest)
        self.assertIn("upstream date:   2026-10-02 (UTC)", manifest)
        self.assertIn(f"package version: {version}", manifest)
        self.assertIn(f"package file:    {name}", manifest)
        self.assertIn("compiler:        ", manifest)
        self.assertIn(f"{driver} module:     2026.09.16-sriov", manifest)
        log = (self.out / f"build-{driver}.log").read_text()
        self.assertIn(self.sha, log)
        self.assertIn("Upstream date: 2026-10-02 (UTC)", log)
        self.assertIn(f"Package version: {version}", log)
        self.assertIn(name, log)
        self.assertIn("mock compiler output", log)
        self.assertIn("Compiler: ", log)
        with tarfile.open(package) as tar:
            self.assertIn(f"./lib/modules/6.18.54-Unraid/kernel/drivers/gpu/drm/{driver}/{driver}.ko.xz", tar.getnames())
            self.assertIn("./lib/modules/6.18.54-Unraid/updates/compat/intel_sriov_compat.ko", tar.getnames())
            modules = [name for name in tar.getnames() if ".ko" in name]
            self.assertEqual(len(modules), 2)
        fetched = Path(self.env["BUILD_DIR"]) / f"i915-sriov-dkms-{driver}-master"
        self.assertEqual((fetched / "fixture.txt").read_text(), fixture)
        self.assertEqual((fetched / "dkms.conf").read_text(), 'PACKAGE_VERSION="2026.09.16"\n')

    def test_default_version_uses_actual_committer_utc_date(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_package("2026.10.02")

    def test_repeat_build_preserves_previous_checkout_debugging_changes(self):
        self.assertEqual(self.build().returncode, 0)
        fetched = Path(self.env["BUILD_DIR"]) / "i915-sriov-dkms-i915-master"
        (fetched / "fixture.txt").write_text("local debugging changes\n")
        result = self.build(PACKAGE_BUILD="2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((fetched / "fixture.txt").read_text(), "local debugging changes\n")
        self.assertTrue((self.out / "i915-sriov-2026.10.02-6.18.54-Unraid-2.txz").is_file())

    def test_checksum_uses_portable_package_basename(self):
        result = self.build(driver="xe")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        name = "xe-sriov-2026.10.02-6.18.54-Unraid-1.txz"
        self.assertEqual((self.out / f"{name}.md5").read_text().split()[1], name)

    def test_xe_is_packaged_separately_without_removing_shared_i915_sources(self):
        result = self.build(driver="xe")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_package("2026.10.02", driver="xe")
        fetched = Path(self.env["BUILD_DIR"]) / "i915-sriov-dkms-xe-master"
        self.assertTrue((fetched / "drivers/gpu/drm/i915/i915.c").is_file())
        self.assertFalse((fetched / "drivers/gpu/drm/i915/i915.ko").exists())

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

    def test_invalid_build_number_stops_before_fetch_or_compilation(self):
        for number in ("auto", "0", "-1", "01", "../2", "1\nextra"):
            with self.subTest(number=number):
                result = self.build(PACKAGE_BUILD=number)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("PACKAGE_BUILD must be a positive integer", result.stderr)
                self.assertNotIn("Fetching", result.stderr)
                self.assertFalse(self.compiler_calls.exists())

    def test_source_fingerprint_is_verified_and_recorded(self):
        result = run(["python3", str(self.project / "scripts/ci-build.py"),
                      "source-fingerprint", "--source-dir", str(self.source)])
        self.assertEqual(result.returncode, 0, result.stderr)
        fingerprint = result.stdout.strip()
        self.assertEqual(len(fingerprint), 64)
        result = self.build(SOURCE_FINGERPRINT=fingerprint,
                            RECIPE_FINGERPRINT="c" * 64, BUILD_KEY="d" * 64)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_package("2026.10.02")
        for filename in ("build-i915.log", "i915-installed-modules.txt"):
            text = (self.out / filename).read_text()
            for value in (fingerprint, "c" * 64, "d" * 64):
                self.assertIn(value, text)

    def test_wrong_source_fingerprint_stops_before_compilation(self):
        result = self.build(SOURCE_FINGERPRINT="0" * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("i915 source fingerprint mismatch", result.stderr)
        self.assertFalse(self.compiler_calls.exists())

    def prepare_upstream_drm_compat(self):
        makefile = self.source / "Makefile"
        makefile.write_text(
            'CONFTEST_COMPILE_TESTS := \\\n\tdrm_client_dev_suspend_one_arg \\\n\tother_probe\n' +
            'DKMS_MODULE_VERSION := "2026.09.16-sriov"\n' +
            'obj-m += compat/\nobj-m += drivers/gpu/drm/i915/\nobj-m += drivers/gpu/drm/xe/\n'
        )
        fixtures = ROOT / "tests/fixtures/drm-client-event"
        shutil.copy2(fixtures / "upstream-one-arg.sh", self.source / "conftest.sh")
        driver = self.source / "drivers/gpu/drm/i915/display/intel_display_driver.c"
        driver.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fixtures / "upstream-display-driver.c", driver)
        shutil.copy2(ROOT / "patches/strongtz-master-drm-client-event-conftest.patch",
                     self.project / "patches/strongtz-master-drm-client-event-conftest.patch")

    def commit_source_changes(self):
        for args in (["add", "."], ["-c", "commit.gpgsign=false", "commit", "-qm", "compat fixture"]):
            result = run(["git", "-C", str(self.source), *args], env=self.git_env)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.sha = run(["git", "-C", str(self.source), "rev-parse", "HEAD"], check=True).stdout.strip()
        self.env["I915_SRIOV_COMMIT"] = self.sha

    def test_upstream_drm_compat_skips_only_the_legacy_patch(self):
        self.prepare_upstream_drm_compat()
        self.commit_source_changes()
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_package("2026.10.02", fixture="after\n")
        log = (self.out / "build-i915.log").read_text()
        self.assertIn("Applying strongtz-2026.08.08-unraid-6x-slab.patch", log)
        self.assertIn("Using upstream DRM client-event compatibility", log)
        self.assertNotIn("Applying strongtz-master-drm-client-event-conftest.patch", log)

    def test_incomplete_upstream_compat_is_not_silently_skipped(self):
        for missing in ("registration", "probe", "caller"):
            with self.subTest(missing=missing):
                self.prepare_upstream_drm_compat()
                if missing == "registration":
                    path = self.source / "Makefile"
                    path.write_text(path.read_text().replace("drm_client_dev_suspend_one_arg", "other"))
                elif missing == "probe":
                    (self.source / "conftest.sh").unlink()
                else:
                    path = self.source / "drivers/gpu/drm/i915/display/intel_display_driver.c"
                    path.write_text(path.read_text().replace(
                        "#ifdef IDB_DRM_CLIENT_DEV_SUSPEND_ONE_ARG", "#if 1", 1))
                self.commit_source_changes()
                result = self.build()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.compiler_calls.exists())
                self.assertIn("patch does not apply", (self.out / "build-i915.log").read_text())


if __name__ == "__main__":
    unittest.main()
