# my-i915-sriov-driver

为 **Unraid** 提供独立的 **i915 SR-IOV** 与 **xe SR-IOV** 驱动包，基于 [strongtz/i915-sriov-dkms](https://github.com/strongtz/i915-sriov-dkms)，配合 [my-unraid-vgpu-manager](https://github.com/hellomrli/my-unraid-vgpu-manager) 管理 Intel 核显虚拟功能（VF）及虚拟机直通。

> [!IMPORTANT]
> **默认、推荐使用 i915。xe 是供测试使用的实验性选项，不会自动替换 i915。**

## 安装管理插件

**[打开插件安装文件（.plg）](https://github.com/hellomrli/my-unraid-vgpu-manager/raw/master/my-unraid-vgpu-manager.plg)**

在 Unraid 的 **插件（Plugins）→ 安装插件（Install Plugin）** 中粘贴下面的地址。点击代码块右上角的**复制按钮**即可复制完整链接：

```text
https://github.com/hellomrli/my-unraid-vgpu-manager/raw/master/my-unraid-vgpu-manager.plg
```

Unraid 会通过 `.plg` 下载对应的插件安装包；后续在插件管理器中更新即可。安装插件后，打开 **设置 → Unraid vGPU 管理器 → 驱动管理**，选择并安装所需的 Intel 驱动。

## i915 还是 xe？

| 驱动 | 定位与建议 |
|---|---|
| **i915（默认、推荐）** | 保持本项目已有的使用路线；现有用户无需为上游 xe 更新而切换驱动。 |
| **xe（实验性）** | Intel 较新的驱动架构，作为独立可选包提供，适合愿意验证硬件和客户机兼容性、能够排查及回退的用户。 |

**xe 较新不等于在 Unraid SR-IOV 中一定更快、更稳定。** 主线驱动的普通图形支持与本项目使用的社区 SR-IOV 移植不是同一回事；应结合 GPU 型号、宿主内核、客户机驱动和实际负载判断，不能仅凭提交数量比较优劣。

两个包均来自社区实验性移植，并非 Intel 官方支持承诺。[上游说明](https://github.com/strongtz/i915-sriov-dkms#required-kernel-parameters)目前特别警告其 **xe 模块不支持 MTL（Meteor Lake）和 LNL（Lunar Lake）**；这不代表主线 xe 的支持情况，也不保证这些平台都能使用 i915 SR-IOV。

## 切换与回退

1. 在插件的 **驱动管理 → Intel SR-IOV 驱动** 中选择 i915 或 xe。已有配置和新安装均默认 i915；升级插件不会自动切换。
2. 插件下载并校验独立驱动包、保留回退缓存，准备下次启动配置；**切换需要重启**，不会热切换正在使用的驱动或 VF。重启前可以取消切换。
3. 测试 xe 前，关闭使用 GPU 的虚拟机和容器的自动启动。重启后先检查实际加载的驱动与 VF 直通，再启动工作负载；驱动启动失败不保证 Unraid 会阻止自动启动。
4. 若测试异常，选择 i915 并再次重启回退。两个包共用兼容层，**不要手动同时安装**，也不要将物理功能（PF）直通给虚拟机。

安装驱动前，请卸载管理同一驱动的其他插件并重启。IOMMU、启动参数冲突、客户机驱动与具体操作见[管理插件中文说明](https://github.com/hellomrli/my-unraid-vgpu-manager/blob/master/README.zh-CN.md)。

## 内核覆盖与更新

只维护[当前 Unraid 正式版及公开测试版](https://docs.unraid.net/unraid-os/download_list/)在对应 [Release notes](https://docs.unraid.net/category/release-notes/) 中列出的内核，以及 [ich777 最新内核](https://github.com/ich777/unraid_kernel/releases/latest)，合并去重后分别提供两种驱动。ich777 内核用于提前准备即将发布的系统版本；退出这些来源的历史内核不再跟进更新，已有有效包保留。

i915 与 xe 独立检查实际相关源码变化：仅 xe 更新不会重新发布未变化的 i915，文档等不参与构建的上游改动也不会触发无效更新；共享依赖或必要的兼容修复仍会更新受影响的驱动。
