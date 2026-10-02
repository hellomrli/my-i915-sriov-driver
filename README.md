# my-i915-sriov-driver

为 **Unraid** 编译的 **Intel i915 SR-IOV 驱动**，基于 [strongtz/i915-sriov-dkms](https://github.com/strongtz/i915-sriov-dkms) **master 最新快照**构建，打包为可直接安装到 Unraid 服务器的 Slackware `.txz` 格式。

配合 [my-unraid-vgpu-manager](https://github.com/hellomrli/my-unraid-vgpu-manager) 插件使用——插件负责下载本包、安装并管理 VF（虚拟功能）供虚拟机直通。

打包方式参照 [giganode/unraid-i915-sriov](https://github.com/giganode/unraid-i915-sriov)：**只走 i915 路线**。

## 功能

- 在 Intel iGPU（支持 SR-IOV 的型号）上启用虚拟功能（VF）
- 每个 VF 可作为一个独立 GPU 直通给虚拟机
- 跟踪 strongtz 上游 **master HEAD**（而非 release tag），第一时间包含最新补丁（如 VF/PF 安全加固、内核同步）
- 包含 Unraid 6.x 内核的 slab 兼容补丁

## 编译的模块（i915-only）

- `i915.ko` — 带 SR-IOV 支持的 i915 驱动
- `intel_sriov_compat.ko` — SR-IOV 兼容层

不编译、不分发 `xe.ko`（对 MTL/LNL 支持不佳且与 i915 路线冲突）与 `kvmgt.ko`（GVT-g，本项目不使用）。

## 版本号规则

包版本号 = 上游快照的提交日期（`YYYYMMDD`，固定 8 位），例如：

```
i915-sriov-20261002-6.18.54-Unraid-1.txz   (+ .md5)
```

即「基于 strongtz master 2026-10-02 快照、面向 6.18.54-Unraid 内核」。`sort -V` 可正确排序，管理插件与 Unraid `upgradepkg` 均能识别新旧。

## 云编译（GitHub Actions）

`.github/workflows/build.yml` 执行 `scripts/build-i915-sriov.sh`：

- **手动触发**：运行 *Build i915 SR-IOV driver* 工作流。`i915_ref` 留空/`latest` 时自动解析上游 master HEAD 的精确 SHA 进行构建；也可填指定 tag/分支/SHA。`kernel_release` 留空则自动取 [ich777/unraid_kernel](https://github.com/ich777/unraid_kernel) 最新内核
- **每日自动检查**：每天 03:30（UTC）解析上游 master HEAD 与 ich777 最新内核，**只有当上游快照日期或内核版本出现新组合时才编译**，否则跳过
- 自动下载 ich777 预编译内核源码树（已知版本校验 SHA256），应用 Unraid slab 补丁后，剔除 xe 再编译

构建产物附加到 **tag 等于内核版本** 的 Release（如 `6.18.54-Unraid`、`6.18.52-Unraid`）。

## 本地构建

```bash
# 需要 Linux 环境，装有 gcc/make/git/curl/tar/xz
# 默认取上游 master HEAD，面向 6.18.54-Unraid：
KERNEL_RELEASE=6.18.54-Unraid ./scripts/build-i915-sriov.sh
```

可用环境变量：`I915_SRIOV_REF`（上游 ref，默认 `master`）、`KERNEL_RELEASE`、`PACKAGE_BUILD`、`PACKAGE_VERSION`（覆盖日期版本号）等。

## Release

当前构建：**20261002**（strongtz master，含 VF/PF 加固）for **6.18.52 / 6.18.54-Unraid**（tag 与内核版本一致）。

历史构建曾使用上游 tag 号（如 `2026.09.16`）命名，管理插件两种形式都能识别。
