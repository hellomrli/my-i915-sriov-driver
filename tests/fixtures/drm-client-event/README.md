`conftest.sh` is the unmodified probe runner from strongtz/i915-sriov-dkms
commit `f4cb98f4c28e1f3ac78ca88501a87c86d0c21a88` (master, 2026-10-02).
The tests apply the DRM client-event patch and run its new probes with GCC
against minimal kernel headers, without downloading upstream or a kernel.

`upstream-one-arg.sh` is the unmodified DRM probe function from commit
`3e820c086b38be0feb2c4de36ace53533c20feae` (master, 2026-10-28).
`upstream-display-driver.c` retains that snapshot's two DRM call-selection
blocks from `display/intel_display_driver.c`, with unrelated operations
removed and a minimal display structure for offline compilation.
