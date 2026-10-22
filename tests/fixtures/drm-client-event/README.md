`conftest.sh` is the unmodified probe runner from strongtz/i915-sriov-dkms
commit `f4cb98f4c28e1f3ac78ca88501a87c86d0c21a88` (master, 2026-10-02).
The tests apply the DRM client-event patch and run its new probes with GCC
against minimal kernel headers, without downloading upstream or a kernel.
