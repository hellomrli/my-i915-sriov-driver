# my-i915-sriov-driver

为 Unraid 构建与内核匹配的 Intel i915 和 xe SR-IOV 驱动包，配合 vGPU 管理插件创建 VF 并直通给虚拟机。

[驱动下载](https://github.com/hellomrli/my-i915-sriov-driver/releases) · [安装与回退](docs/install.md) · [构建维护](docs/building.md) · [文档索引](docs/index.md)

> [!IMPORTANT]
> 默认选择 i915。xe 是独立的实验性选项；驱动、完整内核版本和 GPU 型号需要匹配，切换后重启生效。

## 通过 Unraid 插件安装

需要 Unraid 7.2.3 或更新版本。在“插件 → 安装插件”中粘贴管理插件地址：

```text
https://github.com/hellomrli/my-unraid-vgpu-manager/raw/master/my-unraid-vgpu-manager.plg
```

安装后打开“设置 → Unraid vGPU 管理器 → 驱动管理”，选择 Intel 的 i915 驱动。插件下载与当前 Unraid 内核匹配的包；重启后，在 Intel 显卡页面查看 VF 并绑定虚拟机。

已有其他插件管理同一驱动时，先按[安装与回退指南](docs/install.md)处理冲突和启动参数。

## 选择 i915 或 xe

| 驱动 | 使用场景 | 发布形式 |
|---|---|---|
| i915 | 默认路线，优先用于现有配置 | `i915-sriov-日期-内核-构建号.txz` |
| xe | 需要验证硬件和客户机兼容性的实验环境 | `xe-sriov-日期-内核-构建号.txz` |

两者来自 [strongtz/i915-sriov-dkms](https://github.com/strongtz/i915-sriov-dkms) 的社区移植，不代表 Intel 官方对 Unraid SR-IOV 的支持。xe 的主线图形支持与该移植的 SR-IOV 支持不同；不能仅因架构较新就推断性能或稳定性更好。

上游特别提示其 xe 模块不支持 Meteor Lake 和 Lunar Lake。其他硬件也应核对上游要求，不能由驱动名称推断兼容性。

## 驱动更新如何发布

- 跟踪当前 Unraid 正式版、公开 beta/RC 版本对应内核，以及 ich777 最新内核，合并去重。
- i915 和 xe 独立检查实际相关源码，只有受影响的驱动重新构建。
- 包版本来自固定上游 commit 的 UTC 日期；同日重建使用递增构建号。
- 每个包附带 `.md5`，实际可用组合以 Releases 为准。

历史内核退出当前目标后不再跟进，已发布包仍保留。更新和回退由[管理插件](https://github.com/hellomrli/my-unraid-vgpu-manager)执行。

## 开发与反馈

构建入口、环境变量、CI 目标和离线检查见[构建维护](docs/building.md)。使用问题优先附上 GPU 型号、Unraid 与 `uname -r` 结果、i915/xe 选择和相关日志，再提交 [Issue](https://github.com/hellomrli/my-i915-sriov-driver/issues)。

本仓库目前没有根目录许可证文件；上游驱动和兼容代码沿用各自授权。
