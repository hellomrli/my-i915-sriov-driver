from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import call, patch
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ci_build", ROOT / "scripts/ci-build.py")
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)
SHA = "a" * 40
KERNEL = "6.18.58-Unraid"
VERSION = "2026.10.28"
IGNORED_PATHS = (
    "drivers/gpu/drm/xe/xe_pci_rebar.c", "docs/install.md", ".github/workflows/build.yml",
    "debian/control", "flake.nix", "flake.lock", "README.md", "dkms.conf", "PKGBUILD",
    ".clang-format", ".gitignore", "COPYING", "i915-set-sriov-numvfs.conf", "i915-sriov-dkms.install",
)


def entry(path, kind="blob", mode="100644"):
    return {"path": path, "type": kind, "mode": mode,
            "sha": hashlib.sha1(path.encode()).hexdigest()}


def source_tree():
    paths = ["Makefile", "conftest.sh", "drivers/gpu/drm/i915/i915_driver.c",
             "compat/Makefile", "include/config.h", "shared/helper.c", *IGNORED_PATHS]
    return {"sha": "b" * 40, "truncated": False,
            "tree": [entry(path) for path in paths] +
                    [entry(path, "tree", "040000") for path in
                     ("drivers", "drivers/gpu", "drivers/gpu/drm", "drivers/gpu/drm/xe")]}


def candidate(**overrides):
    record = {"schema": 2, "driver": "i915", "source_commit": SHA,
              "source_fingerprint": "c" * 64, "recipe_fingerprint": "d" * 64,
              "compiler": "gcc (GCC) 16.2.0", "kernel_release": KERNEL,
              "package_version": VERSION, "repository_commit": "e" * 40,
              **overrides}
    record["build_key"] = ci.build_key(record)
    return record


def built_record(number=1, **overrides):
    record = candidate(**overrides)
    return {**record, "package_build": number,
            "package": ci.package_name(record["package_version"], record["kernel_release"], number, record["driver"])}


def asset(name, size=10, state="uploaded"):
    return {"name": name, "size": size, "state": state}


def complete_assets(record):
    return [asset(record["package"]), asset(record["package"] + ".md5")]


def make_recipe(root):
    for name in (".github/workflows/build.yml", "scripts/build-i915-sriov.sh",
                 "scripts/build-intel-sriov.sh", "scripts/build-xe-sriov.sh",
                 "scripts/ci-build.py", "patches/fix.patch", "config/options"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n")


class SourceFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.tree = source_tree()
        self.fingerprint = ci.source_fingerprint(self.tree)

    def test_xe_packaging_and_docs_changes_do_not_rebuild(self):
        for path in IGNORED_PATHS:
            for operation in ("edit", "delete"):
                with self.subTest(path=path, operation=operation):
                    tree = deepcopy(self.tree)
                    if operation == "edit":
                        next(item for item in tree["tree"] if item["path"] == path)["sha"] = "f" * 40
                    else:
                        tree["tree"] = [item for item in tree["tree"] if item["path"] != path]
                    self.assertEqual(ci.source_fingerprint(tree), self.fingerprint)
        for path in ("drivers/gpu/drm/xe/new_file.c", "docs/new.md", ".github/new.yml", "debian/new"):
            with self.subTest(added=path):
                tree = deepcopy(self.tree)
                tree["tree"].append(entry(path))
                self.assertEqual(ci.source_fingerprint(tree), self.fingerprint)

    def test_parent_tree_hashes_and_order_are_not_inputs(self):
        tree = deepcopy(self.tree)
        tree["sha"] = "f" * 40
        for item in tree["tree"]:
            if item["type"] == "tree":
                item["sha"] = "f" * 40
        tree["tree"].reverse()
        self.assertEqual(ci.source_fingerprint(tree), self.fingerprint)

    def test_xe_changes_only_rebuild_xe_but_shared_i915_code_is_tracked(self):
        original = ci.source_fingerprint(self.tree, "xe")
        tree = deepcopy(self.tree)
        next(item for item in tree["tree"] if item["path"].endswith("xe_pci_rebar.c"))["sha"] = "f" * 40
        self.assertNotEqual(ci.source_fingerprint(tree, "xe"), original)
        self.assertEqual(ci.source_fingerprint(tree, "i915"), self.fingerprint)
        tree = deepcopy(self.tree)
        tree["tree"].append(entry("drivers/gpu/drm/i915/display/intel_display.c"))
        self.assertNotEqual(ci.source_fingerprint(tree, "xe"), original)
        self.assertNotEqual(ci.source_fingerprint(tree, "i915"), self.fingerprint)

    def test_retained_driver_shared_headers_and_build_tools_are_inputs(self):
        for path in ("drivers/gpu/drm/i915/i915_driver.c", "compat/Makefile",
                     "include/config.h", "Makefile", "conftest.sh", "shared/helper.c"):
            with self.subTest(path=path):
                tree = deepcopy(self.tree)
                next(item for item in tree["tree"] if item["path"] == path)["sha"] = "f" * 40
                self.assertNotEqual(ci.source_fingerprint(tree), self.fingerprint)

    def test_new_unknown_files_deletions_renames_and_modes_are_inputs(self):
        for operation in ("add", "delete", "rename", "mode"):
            with self.subTest(operation=operation):
                tree = deepcopy(self.tree)
                item = next(item for item in tree["tree"] if item["path"] == "shared/helper.c")
                if operation == "add":
                    tree["tree"].append(entry("new-build-helper.sh"))
                elif operation == "delete":
                    tree["tree"].remove(item)
                elif operation == "rename":
                    item["path"] = "shared/renamed.c"
                else:
                    item["mode"] = "100755"
                self.assertNotEqual(ci.source_fingerprint(tree), self.fingerprint)

    def test_truncated_malformed_or_incomplete_trees_fail_closed(self):
        trees = [None, [], {}, {"tree": []}, {**self.tree, "truncated": True},
                 {"tree": {}, "truncated": False}, {"tree": [], "truncated": False}]
        for path in ("Makefile", "conftest.sh", "compat/Makefile", "include/config.h",
                     "drivers/gpu/drm/i915/i915_driver.c"):
            tree = deepcopy(self.tree)
            tree["tree"] = [item for item in tree["tree"] if item["path"] != path]
            trees.append(tree)
        for item in (None, {"path": None}, entry("duplicate"),
                     {**entry("bad-sha"), "sha": "null"}, entry("bad-type", "unknown"),
                     entry("bad-mode", mode="000000"), entry("symlink", mode="120000"),
                     entry("submodule", "commit", "160000")):
            tree = deepcopy(self.tree)
            tree["tree"].append(item)
            if item and item["path"] == "duplicate":
                tree["tree"].append(item)
            trees.append(tree)
        for tree in trees:
            with self.subTest(tree=tree), self.assertRaises(ValueError):
                ci.source_fingerprint(tree)

    def test_api_tree_is_pinned_and_authenticated(self):
        with patch.dict(os.environ, {"I915_TREE": self.tree["sha"], "GH_TOKEN": "test-token"}), \
                patch("sys.argv", ["ci-build.py", "source-fingerprint"]), \
                patch.object(ci, "GitHub") as api, redirect_stdout(io.StringIO()) as output:
            api.return_value.request.return_value = self.tree
            ci.main()
        api.assert_called_once_with("strongtz/i915-sriov-dkms", "test-token")
        api.return_value.request.assert_called_once_with("GET", f"git/trees/{self.tree['sha']}?recursive=1")
        self.assertEqual(output.getvalue(), self.fingerprint + "\n")

    def test_api_wrong_tree_or_truncation_does_not_emit_fingerprint(self):
        for tree in (None, [], {**self.tree, "sha": "f" * 40}, {**self.tree, "truncated": True}):
            with self.subTest(tree=tree), \
                    patch.dict(os.environ, {"I915_TREE": self.tree["sha"], "GH_TOKEN": "test-token"}), \
                    patch("sys.argv", ["ci-build.py", "source-fingerprint"]), \
                    patch.object(ci, "GitHub") as api, redirect_stdout(io.StringIO()) as output:
                api.return_value.request.return_value = tree
                with self.assertRaises(ValueError):
                    ci.main()
                self.assertEqual(output.getvalue(), "")


class RecipeFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="i915-recipe-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        make_recipe(self.root)
        self.fingerprint = ci.recipe_fingerprint(self.root)

    def test_workflow_scripts_patches_and_config_changes_are_inputs(self):
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            with self.subTest(path=path):
                previous = path.read_bytes()
                path.write_bytes(previous + b"changed\n")
                self.assertNotEqual(ci.recipe_fingerprint(self.root), self.fingerprint)
                path.write_bytes(previous)

    def test_file_modes_additions_and_deletions_are_inputs(self):
        path = self.root / "scripts/build-i915-sriov.sh"
        path.chmod(0o755)
        self.assertNotEqual(ci.recipe_fingerprint(self.root), self.fingerprint)
        path.chmod(0o644)
        added = self.root / "scripts/new-helper.sh"
        added.write_text("new\n")
        self.assertNotEqual(ci.recipe_fingerprint(self.root), self.fingerprint)
        added.unlink()
        (self.root / "patches/fix.patch").unlink()
        self.assertNotEqual(ci.recipe_fingerprint(self.root), self.fingerprint)

    def test_docs_tests_cache_and_mtime_are_not_build_inputs(self):
        for name in ("README.md", "tests/test_ci.py", "scripts/__pycache__/ci-build.cpython-313.pyc"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("not compiled\n")
        os.utime(self.root / "scripts/ci-build.py", (1, 1))
        self.assertEqual(ci.recipe_fingerprint(self.root), self.fingerprint)

    def test_missing_recipe_fails_closed(self):
        (self.root / ".github/workflows/build.yml").unlink()
        with self.assertRaises(ValueError):
            ci.recipe_fingerprint(self.root)

    def test_symlink_to_untracked_inputs_fails_closed(self):
        target = self.root / "README.md"
        target.write_text("outside tracked build inputs\n")
        (self.root / "scripts/linked-input").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            ci.recipe_fingerprint(self.root)


class BuildPlanTests(unittest.TestCase):
    def setUp(self):
        self.record = built_record()
        self.body = ci.release_body("Release notes.", self.record)
        self.assets = complete_assets(self.record)

    def test_xe_only_commit_on_new_date_keeps_original_package_and_provenance(self):
        current = candidate(source_commit="f" * 40, package_version="2026.11.01")
        plan = ci.plan_build(current, self.body, self.assets)
        self.assertFalse(plan["build"])
        self.assertEqual(plan["record"], self.record)
        self.assertIn("skipping", plan["reason"])

    def test_local_recipe_source_kernel_or_compiler_changes_rebuild(self):
        for key, value in (("source_fingerprint", "0" * 64), ("recipe_fingerprint", "0" * 64),
                           ("kernel_release", "6.18.59-Unraid"), ("compiler", "gcc (GCC) 17.1.0")):
            with self.subTest(key=key):
                current = candidate(**{key: value})
                plan = ci.plan_build(current, self.body, self.assets)
                self.assertTrue(plan["build"])
                self.assertNotEqual(plan["record"]["build_key"], self.record["build_key"])
                expected_number = 1 if key == "kernel_release" else 2
                self.assertEqual(plan["record"]["package_build"], expected_number)

    def test_repository_commit_and_snapshot_date_alone_do_not_change_build_key(self):
        current = candidate(repository_commit="0" * 40, source_commit="1" * 40,
                            package_version="2026.11.02")
        self.assertEqual(current["build_key"], self.record["build_key"])

    def test_same_day_source_change_increments_without_overwriting(self):
        current = candidate(source_commit="f" * 40, source_fingerprint="0" * 64)
        plan = ci.plan_build(current, self.body, self.assets)
        self.assertTrue(plan["build"])
        self.assertEqual(plan["record"]["package_build"], 2)
        self.assertNotEqual(plan["record"]["package"], self.record["package"])

    def test_legacy_or_invalid_metadata_rebuilds_once(self):
        corrupt = {**self.record, "build_key": "0" * 64}
        unknown = {**self.record, "schema": 3}
        bodies = ["", "Auto-built from strongtz/i915-sriov-dkms @ old",
                  ci.STATE_PREFIX + "not json" + ci.STATE_SUFFIX,
                  ci.STATE_PREFIX + json.dumps(corrupt) + ci.STATE_SUFFIX,
                  ci.STATE_PREFIX + json.dumps(unknown) + ci.STATE_SUFFIX,
                  self.body + "\n" + self.body]
        for body in bodies:
            with self.subTest(body=body):
                plan = ci.plan_build(candidate(), body, self.assets)
                self.assertTrue(plan["build"])
                self.assertEqual(plan["record"]["package_build"], 2)
                migrated = ci.release_body(body, plan["record"])
                retry = ci.plan_build(candidate(), migrated, complete_assets(plan["record"]))
                self.assertFalse(retry["build"])

    def test_both_exact_recorded_assets_must_be_nonempty_and_uploaded(self):
        incomplete = [[], self.assets[:1], self.assets[1:], complete_assets(built_record(2))]
        for index in (0, 1):
            for key, value in (("size", 0), ("state", "starter")):
                assets = deepcopy(self.assets)
                assets[index][key] = value
                incomplete.append(assets)
        for assets in incomplete:
            with self.subTest(assets=assets):
                self.assertTrue(ci.plan_build(candidate(), self.body, assets)["build"])

    def test_manual_dispatch_always_builds_and_auto_increments(self):
        plan = ci.plan_build(candidate(), self.body, self.assets, force=True)
        self.assertTrue(plan["build"])
        self.assertEqual(plan["record"]["package_build"], 2)
        self.assertIn("Manual dispatch", plan["reason"])

    def test_auto_build_numbers_reserve_incomplete_assets_and_filter_other_versions(self):
        assets = [asset(built_record(3)["package"], size=0, state="starter"),
                  asset(built_record(5)["package"] + ".md5"),
                  asset(built_record(99, package_version="2026.10.27")["package"]),
                  asset(built_record(99, kernel_release="6.18.59-Unraid")["package"])]
        for requested in ("auto", ""):
            self.assertEqual(ci.next_build(assets, VERSION, KERNEL, requested), 6)
        self.assertEqual(ci.next_build(assets, VERSION, KERNEL, "10"), 10)

    def test_invalid_or_non_increasing_explicit_build_is_rejected(self):
        for requested in ("0", "01", "-1", "1", "null", "Auto", "../2", "2\nextra"):
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                ci.plan_build(candidate(), self.body, self.assets, force=True, requested=requested)

    def test_invalid_candidate_cannot_skip(self):
        for values in ({"source_commit": "master"}, {"package_version": "2026.02.29"},
                       {"kernel_release": "../tag"}, {"build_key": "0" * 64}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ci.plan_build({**candidate(), **values}, self.body, self.assets)

    def test_driver_names_versions_and_metadata_are_independent(self):
        current = candidate(driver="xe")
        plan = ci.plan_build(current, self.body, self.assets)
        self.assertTrue(plan["build"])
        self.assertEqual(plan["record"]["package_build"], 1)
        self.assertTrue(plan["record"]["package"].startswith("xe-sriov-"))
        body = ci.release_body("Xe is experimental.", plan["record"])
        self.assertIsNone(ci.read_record(body, "i915"))
        self.assertFalse(ci.plan_build(current, body, complete_assets(plan["record"]))["build"])
        self.assertNotEqual(current["build_key"], self.record["build_key"])


class GitHubTests(unittest.TestCase):
    def setUp(self):
        self.api = ci.GitHub("test/repo", "test-token")

    def test_only_404_means_no_release(self):
        for status in (404, 401, 403, 429, 500, 503):
            error = HTTPError("https://api.github.com/test", status, "failure", {}, None)
            self.addCleanup(error.close)
            with self.subTest(status=status), patch.object(self.api, "request", side_effect=error):
                if status == 404:
                    self.assertIsNone(self.api.release(KERNEL))
                else:
                    with self.assertRaises(HTTPError):
                        self.api.release(KERNEL)

    def test_all_asset_pages_are_used_for_build_number(self):
        first = [asset(f"other-{index}.txz") for index in range(100)]
        last = complete_assets(built_record(9))
        with patch.object(self.api, "request", side_effect=[first, last]) as request:
            assets = self.api.assets(123)
        self.assertEqual(ci.next_build(assets, VERSION, KERNEL), 10)
        self.assertEqual(request.call_args_list, [
            call("GET", "releases/123/assets?per_page=100&page=1"),
            call("GET", "releases/123/assets?per_page=100&page=2")])

    def test_xe_uses_a_separate_release_without_changing_i915_urls(self):
        with patch.object(self.api, "request", return_value={"id": 123, "body": ""}) as request:
            self.api.release(KERNEL, "i915")
            self.api.release(KERNEL, "xe")
        self.assertEqual(request.call_args_list, [call("GET", f"releases/tags/{KERNEL}"),
                                                call("GET", f"releases/tags/xe-{KERNEL}")])

    def test_malformed_release_or_asset_response_fails_closed(self):
        for response in (None, [], {}, {"id": "123"}, {"id": 123, "body": ["invalid"]}):
            with self.subTest(response=response), patch.object(self.api, "request", return_value=response), \
                    self.assertRaises(ValueError):
                self.api.release(KERNEL)
        for response in (None, {}, [None], [{}], [{"name": "file", "size": "10", "state": "uploaded"}]):
            with self.subTest(response=response), patch.object(self.api, "request", return_value=response), \
                    self.assertRaises(ValueError):
                self.api.assets(123)

    def test_requests_use_authentication_and_upload_host(self):
        with patch.object(ci, "urlopen", return_value=io.BytesIO(b'{"id": 123}')) as open_url:
            self.assertEqual(self.api.request("GET", "releases/123"), {"id": 123})
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.github.com/repos/test/repo/releases/123")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "fixture.txz"
            package.write_bytes(b"fixture")
            with patch.object(ci, "urlopen", return_value=io.BytesIO(b'{}')) as open_url:
                self.api.request("POST", "releases/123/assets?name=fixture.txz", upload=package)
            request = open_url.call_args.args[0]
            self.assertTrue(request.full_url.startswith("https://uploads.github.com/"))
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.data, b"fixture")


class FakeGitHub:
    def __init__(self, body="Release notes.", assets=None, exists=True):
        self.body = body
        self.existing_assets = assets or []
        self.exists = exists
        self.requests = []
        self.fail_upload = 0
        self.partial_upload = False
        self.uploads = 0

    def release(self, kernel, driver="i915"):
        return {"id": 123, "body": self.body} if self.exists else None

    def assets(self, release_id):
        return self.existing_assets

    def request(self, method, path, body=None, upload=None):
        self.requests.append({"method": method, "path": path, "body": body, "upload": upload})
        if upload is not None:
            self.uploads += 1
            if self.uploads == self.fail_upload:
                raise OSError("Upload failed")
            return asset(upload.name, upload.stat().st_size,
                         "starter" if self.partial_upload else "uploaded")
        return {"id": 123}


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="i915-publish-")
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.record = built_record(2)
        self.plan = {"build": True, "record": self.record}
        self.package = self.out / self.record["package"]
        self.package.write_bytes(b"fixture package\n")
        self.checksum = self.out / (self.record["package"] + ".md5")
        self.checksum.write_text(hashlib.md5(self.package.read_bytes()).hexdigest() + f"  {self.package}\n")
        self.api = FakeGitHub(body=ci.release_body("User release notes.", built_record()),
                              assets=complete_assets(built_record()))

    def publish(self):
        with redirect_stdout(io.StringIO()):
            ci.publish(self.api, self.plan, self.out)

    def test_exact_planned_package_is_published_before_metadata_and_never_replaced(self):
        (self.out / built_record()["package"]).write_text("wrong, older package\n")
        self.publish()
        requests = self.api.requests
        self.assertEqual([item["upload"] for item in requests[:2]], [self.checksum, self.package])
        self.assertEqual([item["method"] for item in requests], ["POST", "POST", "PATCH"])
        body = requests[-1]["body"]["body"]
        self.assertIn("User release notes.", body)
        self.assertEqual(ci.read_record(body), self.record)
        self.assertEqual(body.count(ci.STATE_PREFIX), 1)
        self.assertEqual(body.count("Auto-built from strongtz/i915-sriov-dkms"), 1)

    def test_either_failed_upload_does_not_mark_inputs_built(self):
        for index in (1, 2):
            with self.subTest(index=index):
                self.api = FakeGitHub()
                self.api.fail_upload = index
                with self.assertRaises(OSError):
                    self.publish()
                self.assertFalse(any(item["method"] == "PATCH" for item in self.api.requests))

    def test_incomplete_upload_response_does_not_mark_inputs_built(self):
        self.api.partial_upload = True
        with self.assertRaisesRegex(ValueError, "Upload did not complete"):
            self.publish()
        self.assertFalse(any(item["method"] == "PATCH" for item in self.api.requests))

    def test_new_release_is_not_marked_built_before_uploads(self):
        self.api.exists = False
        self.publish()
        create = self.api.requests[0]
        self.assertEqual(create["path"], "releases")
        self.assertNotIn(SHA, create["body"]["body"])
        self.assertNotIn(ci.STATE_PREFIX, create["body"]["body"])
        self.assertEqual(ci.read_record(self.api.requests[-1]["body"]["body"]), self.record)

    def test_collision_between_plan_and_publish_aborts_without_deleting_assets(self):
        for name in (self.package.name, self.checksum.name):
            with self.subTest(name=name):
                self.api = FakeGitHub(assets=[asset(name, size=0, state="starter")])
                with self.assertRaisesRegex(ValueError, "already exists"):
                    self.publish()
                self.assertEqual(self.api.requests, [])

    def test_missing_package_or_wrong_checksum_is_rejected_before_upload(self):
        self.checksum.write_text("invalid\n")
        with self.assertRaisesRegex(ValueError, "MD5"):
            self.publish()
        self.checksum.unlink()
        with self.assertRaisesRegex(ValueError, "MD5"):
            self.publish()
        self.package.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "Missing package"):
            self.publish()
        self.package.unlink()
        with self.assertRaisesRegex(ValueError, "Missing package"):
            self.publish()
        self.assertEqual(self.api.requests, [])

    def test_skipped_or_tampered_plan_is_not_published(self):
        self.plan["build"] = False
        with self.assertRaises(ValueError):
            self.publish()
        self.plan["build"] = True
        self.record["package"] = "../outside.txz"
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.api.requests, [])


class PlanCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="i915-ci-plan-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        make_recipe(self.root)
        self.output = self.root / "github-output"
        self.plan_file = self.root / "out/build-plan.json"
        self.api = FakeGitHub(exists=False)
        self.env = {"GH_TOKEN": "test-token", "GITHUB_REPOSITORY": "test/repo",
                    "GITHUB_EVENT_NAME": "schedule", "GITHUB_SHA": "e" * 40,
                    "GITHUB_OUTPUT": str(self.output), "I915_REF": SHA,
                    "SOURCE_FINGERPRINT": "c" * 64, "PKG_VERSION": VERSION,
                    "KERNEL_RELEASE": KERNEL, "PACKAGE_BUILD_INPUT": "auto"}

    def plan(self, **overrides):
        self.output.write_text("")
        with patch.dict(os.environ, {**self.env, **overrides}, clear=True), \
                patch("sys.argv", ["ci-build.py", "plan"]), patch.object(ci, "ROOT", self.root), \
                patch.object(ci, "GitHub", return_value=self.api), \
                patch.object(ci.subprocess, "check_output", return_value="gcc (GCC) 16.2.0\n"), \
                redirect_stdout(io.StringIO()):
            ci.main()
        return json.loads(self.plan_file.read_text())

    def test_plan_outputs_chosen_number_and_metadata_then_skips_equivalent_head(self):
        first = self.plan()
        self.assertTrue(first["build"])
        self.assertIn("new_version=true\npackage_build=1\n", self.output.read_text())
        self.assertIn(f"build_key={first['record']['build_key']}\n", self.output.read_text())
        self.api = FakeGitHub(ci.release_body("Notes.", first["record"]), complete_assets(first["record"]))
        second = self.plan(I915_REF="f" * 40, PKG_VERSION="2026.11.01")
        self.assertFalse(second["build"])
        self.assertEqual(second["record"], first["record"])
        self.assertIn("new_version=false\n", self.output.read_text())
        manual = self.plan(GITHUB_EVENT_NAME="workflow_dispatch")
        self.assertTrue(manual["build"])
        self.assertEqual(manual["record"]["package_build"], 2)

    def test_api_failure_does_not_emit_outputs_or_plan(self):
        with patch.object(self.api, "release", side_effect=OSError("API unavailable")):
            with self.assertRaises(OSError):
                self.plan()
        self.assertEqual(self.output.read_text(), "")
        self.assertFalse(self.plan_file.exists())

    def test_invalid_manual_build_does_not_emit_outputs_or_plan(self):
        with self.assertRaises(ValueError):
            self.plan(GITHUB_EVENT_NAME="workflow_dispatch", PACKAGE_BUILD_INPUT="0")
        self.assertEqual(self.output.read_text(), "")
        self.assertFalse(self.plan_file.exists())

    def test_workflow_wires_fingerprint_and_planned_number_into_build(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        for text in ("default: 'auto'", "I915_TREE: ${{ steps.resolve.outputs.i915_tree }}",
                     "source_fingerprints: ${{ steps.fingerprint.outputs.source_fingerprints }}",
                     "driver: ${{ fromJSON(needs.resolve.outputs.drivers) }}",
                     "PACKAGE_BUILD_INPUT: ${{ inputs.package_build }}",
                     "PACKAGE_BUILD: ${{ steps.check.outputs.package_build }}",
                     "RECIPE_FINGERPRINT: ${{ steps.check.outputs.recipe_fingerprint }}",
                     "BUILD_KEY: ${{ steps.check.outputs.build_key }}",
                     "python3 scripts/ci-build.py plan", "python3 scripts/ci-build.py publish"):
            self.assertIn(text, workflow)
        self.assertEqual(workflow.count("SOURCE_FINGERPRINT: ${{ fromJSON(needs.resolve.outputs.source_fingerprints)[matrix.driver] }}"), 2)
        self.assertNotIn("-X DELETE", workflow)
        self.assertNotIn("ls out/i915-sriov", workflow)


if __name__ == "__main__":
    unittest.main()
