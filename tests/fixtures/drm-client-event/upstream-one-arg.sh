ct_drm_client_dev_suspend_one_arg() {
	CODE="
	#include <drm/drm_client_event.h>
	static void conftest_drm_client_dev_suspend_one_arg(void)
	{
		drm_client_dev_suspend((struct drm_device *)NULL);
		drm_client_dev_resume((struct drm_device *)NULL);
	}
	"

	compile_check "$CODE" "IDB_DRM_CLIENT_DEV_SUSPEND_ONE_ARG" 1
}
