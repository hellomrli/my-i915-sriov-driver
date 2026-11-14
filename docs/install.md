# 安装、切换与回退 Intel 驱动

在支持的 Intel 硬件上，用 [my-unraid-vgpu-manager](https://github.com/hellomrli/my-unraid-vgpu-manager) 安装驱动和管理 VF。插件要求 Unraid 7.2.3 或更新版本。

## 安装 i915

1. 卸载管理同一 Intel 驱动的其他插件并重启。
2. 在管理插件中核对 IOMMU、启动参数与当前完整内核版本。
3. 在“驱动管理 → Intel SR-IOV 驱动”中选择 i915 并安装匹配包。
4. 按页面提示重启，检查实际加载的驱动与 VF。
5. 将 VF 绑定到目标虚拟机，并冷启动客户机验证其驱动。

不要把物理功能 PF 作为 VF 直通，也不要手动同时安装 i915 与 xe 包。它们共享兼容模块，插件负责保持一致的选择。

## 在测试环境切换到 xe

1. 先核对 [strongtz 上游要求](https://github.com/strongtz/i915-sriov-dkms#required-kernel-parameters)，确认 GPU、宿主内核和客户机驱动适合测试。
2. 关闭使用 GPU 的虚拟机和容器自动启动。
3. 在插件的 Intel 驱动选择中改为 xe，等待下载、校验和启动配置准备完成。
4. 重启后先检查驱动、VF 和单台虚拟机，再启动其他工作负载。

切换不是热切换。升级插件不会自动把 i915 改为 xe；重启前可取消待生效的选择。驱动启动失败不意味着 Unraid 会自动阻止已有工作负载启动，因此应提前调整自动启动。

## 回退到 i915

测试异常时，在插件中选择 i915 并再次重启。插件保留校验过的回退缓存，具体启动恢复和内核参数操作以[插件中文说明](https://github.com/hellomrli/my-unraid-vgpu-manager/blob/master/README.zh-CN.md)为准。

## 找不到匹配驱动包

先查看 `uname -r`，再在[已发布驱动](https://github.com/hellomrli/my-i915-sriov-driver/releases)中找完全相同的内核标签。i915 使用内核名标签，xe 使用带 `xe-` 前缀的标签。不要将另一内核的包当作替代。

当前跟踪范围是官方正式版、公开测试版及 ich777 最新内核；历史目标的包可能保留但不再更新。硬件或客户机问题不应只靠更换同内核包版本判断。

返回[文档索引](index.md)。
