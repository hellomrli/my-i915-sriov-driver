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

包版本号默认取**实际检出的上游提交的 committer 日期（UTC）**，格式为 `YYYY.MM.DD`，例如：

```
i915-sriov-2026.10.02-6.18.54-Unraid-1.txz   (+ .md5)
```

即「基于 strongtz 2026-10-02（UTC）的源码快照、面向 6.18.54-Unraid 内核」。月份和日期保留前导零；使用同一份源码在其他日期重新编译，默认版本号不变。需要区分同日重打包时可增加 `PACKAGE_BUILD`。

包日期不是上游 DKMS/模块的版本号。例如 2026-10-02 的源码仍可能声明 `2026.09.16-sriov`，本项目保留该模块版本，只规范包名中的日期。精确源码 SHA、UTC 日期及包版本会分别记录到 `out/build-i915.log` 和 `out/i915-installed-modules.txt`。

显式设置 `PACKAGE_VERSION` 可以覆盖包日期，接受 `20261002`、`2026-10-02`、`2026.10.02` 三种写法，统一输出 `2026.10.02`；非法日历日期或其他版本字符串会使构建失败。覆盖值不会改变记录的真实源码日期。

## 云编译（GitHub Actions）

`.github/workflows/build.yml` 执行 `scripts/build-i915-sriov.sh`：

- **手动触发**：运行 *Build i915 SR-IOV driver* 工作流。`i915_ref` 留空/`latest` 时使用上游 master HEAD，也可填指定 tag/分支/SHA；所有 ref 均先解析为精确 SHA，再用该 SHA 构建和校验，保留 tag 的 `v` 前缀。`kernel_release` 留空则自动取 [ich777/unraid_kernel](https://github.com/ich777/unraid_kernel) 最新内核
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

此前已发布的构建包含 **20261002**（strongtz master，含 VF/PF 加固）for **6.18.52 / 6.18.54-Unraid**（tag 与内核版本一致）。更新后的流程生成 `2026.10.02` 形式的包名，不会自动重命名已有 Release 资产。

历史构建还曾使用上游 tag 号（如 `2026.09.16`）。**紧凑日期与点分日期不能直接混用 `sort -V` 比较**：`20261002` 会排在 `2026.10.03` 后面。使用管理插件选择最新版时，应确认其先归一化两种日期格式；不能仅凭能解析文件名就保证排序正确。

## 离线测试

```bash
python3 -m unittest discover -s tests -v
```

测试日期归一化、UTC 来源、CI ref 固定及模拟构建的产物命名；不下载内核，不代表真实驱动编译或 Unraid 硬件验证。
