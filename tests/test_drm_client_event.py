import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/strongtz-master-drm-client-event-conftest.patch"
CC = shutil.which("gcc")

MAKEFILE = r"""
CONFTEST_COMPILE_TESTS := \
	copy_from_user_inatomic_nontemporal \
	drm_exec_for_each_locked_object_no_index \
	drm_fb_helper_alloc_info \
	drm_pagemap_compound_folio \
	pci_resize_resource_4args \
	xe_pmt_telem_read_kernel_device
"""

# Retain the upstream context for all three patched calls, with unrelated
# driver operations stubbed so the real call selection can compile offline.
DRIVER = """#include <drm/drm_client_event.h>
struct drm_device { int unused; };
struct drm_i915_private { struct drm_device drm; int runtime_pm; };
static void intel_runtime_pm_disable(void *runtime_pm) { }
static void intel_display_power_disable(void *display) { }
static void intel_opregion_resume(void *display) { }

void i915_driver_shutdown(struct drm_i915_private *i915)
{
	void *display = 0;
	intel_runtime_pm_disable(&i915->runtime_pm);
	intel_display_power_disable(display);

#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 19, 0)
	drm_client_dev_suspend(&i915->drm);
#else
	drm_client_dev_suspend(&i915->drm, false);
#endif
}

void i915_drm_suspend(struct drm_device *dev)
{
	void *display = 0;
	/* We do a lot of poking in a lot of registers, make sure they work
	 * properly. */
	intel_display_power_disable(display);

#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 19, 0)
	drm_client_dev_suspend(dev);
#else
	drm_client_dev_suspend(dev, false);
#endif
}

void i915_drm_resume(struct drm_device *dev)
{
	void *display = 0;

	intel_opregion_resume(display);

#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 19, 0)
	drm_client_dev_resume(dev);
#else
	drm_client_dev_resume(dev, false);
#endif
}
"""


@unittest.skipUnless(CC, "GCC is required for offline compatibility probes")
class DrmClientEventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="i915-drm-client-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        shutil.copy2(ROOT / "tests/fixtures/drm-client-event/conftest.sh",
                     self.directory / "conftest.sh")
        (self.directory / "conftest.sh").chmod(0o755)
        (self.directory / "Makefile").write_text(MAKEFILE)
        self.driver = self.directory / "drivers/gpu/drm/i915/i915_driver.c"
        self.driver.parent.mkdir(parents=True)
        self.driver.write_text(DRIVER)
        result = subprocess.run(["git", "apply", str(PATCH)], cwd=self.directory,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.include = self.directory / "include"
        (self.include / "drm").mkdir(parents=True)
        (self.include / "linux").mkdir()

    def headers(self, version, suspend_args, resume_args, enabled=True):
        (self.include / "linux/version.h").write_text(
            "#define KERNEL_VERSION(a, b, c) (((a) << 16) + ((b) << 8) + (c))\n"
            f"#define LINUX_VERSION_CODE KERNEL_VERSION{version}\n"
        )
        declarations = []
        for name, count in (("suspend", suspend_args), ("resume", resume_args)):
            params = "struct drm_device *dev"
            if count == 2:
                params += ", bool holds_console_lock"
            declarations.extend([
                "#if defined(CONFIG_DRM_CLIENT)",
                f"void drm_client_dev_{name}({params});",
                "#else",
                f"static inline void drm_client_dev_{name}({params}) {{ }}",
                "#endif",
            ])
        (self.include / "drm/drm_client_event.h").write_text(
            "#include <stdbool.h>\n#include <linux/version.h>\n"
            "struct drm_device;\n" + "\n".join(declarations) + "\n"
        )
        self.cflags = "-std=gnu11" + (" -DCONFIG_DRM_CLIENT=1" if enabled else "")

    def probe_and_compile(self, suspend_args=1, resume_args=1):
        result = subprocess.run(
            ["sh", "conftest.sh", "compile_test", "drm_client_dev_resume_1arg",
             "drm_client_dev_suspend_1arg"], cwd=self.directory,
            env={**os.environ, "CC": CC, "srctree": str(self.directory),
                 "LINUXINCLUDE": f"-I{self.include}", "KBUILD_CFLAGS": self.cflags},
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        for name, count in (("SUSPEND", suspend_args), ("RESUME", resume_args)):
            macro = f"IDB_DRM_CLIENT_DEV_{name}_1ARG"
            expected = f"#define {macro} 1" if count == 1 else f"#undef {macro}"
            self.assertIn(expected, result.stdout)
        results = self.directory / "results.h"
        results.write_text(result.stdout)
        compiled = subprocess.run(
            [CC, *self.cflags.split(), f"-I{self.include}", "-include", str(results),
             "-Werror=implicit-function-declaration", "-fsyntax-only", str(self.driver)],
            text=True, capture_output=True,
        )
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        makefile = (self.directory / "Makefile").read_text()
        for probe in ("drm_client_dev_resume_1arg", "drm_client_dev_suspend_1arg"):
            self.assertIn(f"\t{probe} \\\n", makefile)

    def test_single_argument_backport_in_6_18(self):
        self.headers((6, 18, 58), 1, 1)
        self.probe_and_compile()

    def test_single_argument_interface_in_7_2(self):
        self.headers((7, 2, 0), 1, 1)
        self.probe_and_compile()

    def test_master_with_two_argument_6_18_headers(self):
        self.headers((6, 18, 54), 2, 2)
        self.probe_and_compile(2, 2)

    def test_resume_and_suspend_are_probed_independently(self):
        for suspend_args, resume_args in ((1, 2), (2, 1)):
            with self.subTest(suspend=suspend_args, resume=resume_args):
                self.headers((6, 18, 58), suspend_args, resume_args)
                self.probe_and_compile(suspend_args, resume_args)

    def test_inline_stubs_with_drm_clients_disabled(self):
        self.headers((6, 18, 58), 1, 1, enabled=False)
        self.probe_and_compile()

    def test_unpatched_master_reproduces_argument_error(self):
        self.headers((6, 18, 58), 1, 1)
        self.driver.write_text(DRIVER)
        result = subprocess.run(
            [CC, *self.cflags.split(), f"-I{self.include}", "-fsyntax-only", str(self.driver)],
            text=True, capture_output=True, env={**os.environ, "LC_ALL": "C"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("too many arguments", result.stderr)


if __name__ == "__main__":
    unittest.main()
