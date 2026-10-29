#include <drm/drm_client_event.h>

struct intel_display {
	struct drm_device *drm;
};

void intel_display_driver_suspend(struct intel_display *display)
{
#ifdef IDB_DRM_CLIENT_DEV_SUSPEND_ONE_ARG
	drm_client_dev_suspend(display->drm);
#else
	drm_client_dev_suspend(display->drm, false);
#endif
}

void intel_display_driver_resume(struct intel_display *display)
{
#ifdef IDB_DRM_CLIENT_DEV_SUSPEND_ONE_ARG
	drm_client_dev_resume(display->drm);
#else
	drm_client_dev_resume(display->drm, false);
#endif
}
