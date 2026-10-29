# my-i915-sriov-driver

为 **Unraid** 编译的 **Intel i915 SR-IOV 驱动**，基于 [strongtz/i915-sriov-dkms](https://github.com/strongtz/i915-sriov-dkms) **master 最新快照**构建，打包为可直接安装到 Unraid 服务器的 Slackware `.txz` 格式。

配合 [my-unraid-vgpu-manager](https://github.com/hellomrli/my-unraid-vgpu-manager) 插件使用——插件负责下载本包、安装并管理 VF（虚拟功能）供虚拟机直通。

自动构建始终跟踪上游 **master**，不固定在 release tag，也不为兼容内核而回退到旧驱动分支。当前上游快照为 **2026.10.28**，模块版本为 `2026.10.28-sriov`，上游 `dkms.conf` 与 README 的内核范围均为 `6.18.x ~ 7.3.x`。

打包方式参照 [giganode/unraid-i915-sriov](https://github.com/giganode/unraid-i915-sriov)：**只走 i915 路线**。

## 功能

- 在 Intel iGPU（支持 SR-IOV 的型号）上启用虚拟功能（VF）
- 每个 VF 可作为一个独立 GPU 直通给虚拟机
- 跟踪 strongtz 上游 **master HEAD**（而非 release tag），第一时间包含最新补丁（如 VF/PF 安全加固、内核同步）
- 包含 Unraid 6.x 内核的 slab 兼容补丁与 DRM client-event 接口编译探测

## 编译的模块（i915-only）

- `i915.ko` — 带 SR-IOV 支持的 i915 驱动
- `intel_sriov_compat.ko` — SR-IOV 兼容层

从构建中剔除 `xe.ko`（对 MTL/LNL 支持不佳且与 i915 路线冲突），也不分发 `kvmgt.ko`（GVT-g，本项目不使用）。包内仅包含上面两个模块。

## 版本号规则

包版本号默认取**实际检出的上游提交的 committer 日期（UTC）**，格式为 `YYYY.MM.DD`，例如：

```
i915-sriov-2026.10.02-6.18.58-Unraid-1.txz   (+ .md5)
```

即「基于 strongtz 2026-10-02（UTC）的源码快照、面向 6.18.58-Unraid 内核」。月份和日期保留前导零；使用同一份源码在其他日期重新编译，默认版本号不变。需要区分同日重打包时可增加 `PACKAGE_BUILD`。

包日期不是上游 DKMS/模块的版本号。例如 2026-10-02 的源码仍可能声明 `2026.09.16-sriov`，本项目保留该模块版本，只规范包名中的日期。精确源码 SHA、UTC 日期及包版本会分别记录到 `out/build-i915.log` 和 `out/i915-installed-modules.txt`。

显式设置 `PACKAGE_VERSION` 可以覆盖包日期，接受 `20261002`、`2026-10-02`、`2026.10.02` 三种写法，统一输出 `2026.10.02`；非法日历日期或其他版本字符串会使构建失败。覆盖值不会改变记录的真实源码日期。

## 云编译（GitHub Actions）

`.github/workflows/build.yml` 执行 `scripts/build-i915-sriov.sh`：

- **手动触发**：运行 *Build i915 SR-IOV driver* 工作流。驱动始终解析当前上游 `master` HEAD 为精确 SHA 后构建和校验，不提供旧分支或 tag 选择项。`kernel_release` 留空则自动取 [ich777/unraid_kernel](https://github.com/ich777/unraid_kernel) 最新内核，同时始终构建 **`6.18.58-Unraid`**；显式指定 6.18.58 时会去重，只构建一次
- **每日自动检查**：每天 03:30（UTC）解析一次上游 master HEAD，供最新内核与 `6.18.58-Unraid` 两个构建共享。每个内核分别通过对应 Release 正文记下已构建的精确上游 SHA，**只有同一 SHA 的包和 MD5 均存在时才跳过**；一个内核构建失败不会取消另一个
- 工具链使用固定的官方 **`gcc:16.2.0`** 镜像；先运行离线回归测试，再构建模块
- 自动下载 ich777 预编译内核源码树（已知版本校验 SHA256），应用 Unraid slab 兼容补丁；DRM 接口优先使用上游已有探测，旧快照仍应用本地补丁，剔除 xe 后再编译

构建产物附加到 **tag 等于内核版本** 的 Release（如 `6.18.58-Unraid`）。

`6.18.58-Unraid` 已回移 `drm_client_dev_suspend/resume()` 的单参数接口，不能仅用 `>= 6.19` 判断签名。上游 2026.10.28 已加入 `drm_client_dev_suspend_one_arg` 探测，并把调用移动到 `display/intel_display_driver.c`，因此旧 DRM 补丁不能再无条件应用。脚本确认探测注册、宏生成逻辑和新调用位置后，跳过旧补丁；未识别的源码仍严格校验补丁，不忽略应用失败。旧快照继续使用分别探测两个接口的本地补丁，不更换驱动分支。

内核自身由 GCC 14.2 构建时，使用 GCC 16.2 编译外部模块仍会显示编译器不一致警告；该警告不等同于编译失败，实际编译器版本会记录到构建日志和模块清单。

## 本地构建

```bash
# 需要 Linux 环境、GCC >= 14 及 make/git/curl/tar/xz/kmod 等构建依赖
# 默认取上游 master HEAD，面向 6.18.58-Unraid：
KERNEL_RELEASE=6.18.58-Unraid ./scripts/build-i915-sriov.sh
```

可用环境变量：`KERNEL_RELEASE`、`PACKAGE_BUILD`、`PACKAGE_VERSION`（覆盖日期版本号）等。默认始终构建 strongtz 最新 `master`；若要复现某一已审查的历史 master 提交，可用 `I915_SRIOV_REF` / `I915_SRIOV_COMMIT` 传入精确 SHA，不要切换旧内核分支。

## Release

此前已发布的构建包含 **20261002**（strongtz master，含 VF/PF 加固）for **6.18.52 / 6.18.54-Unraid**（tag 与内核版本一致）。更新后的流程生成 `2026.10.02` 形式的包名，不会自动重命名已有 Release 资产。

历史构建还曾使用上游 tag 号（如 `2026.09.16`）。**紧凑日期与点分日期不能直接混用 `sort -V` 比较**：`20261002` 会排在 `2026.10.03` 后面。使用管理插件选择最新版时，应确认其先归一化两种日期格式；不能仅凭能解析文件名就保证排序正确。

## 离线测试

```bash
python3 -m unittest discover -s tests -v
```

测试日期归一化、UTC 来源、CI ref 固定、双内核矩阵及去重、模拟构建的产物命名，以及真实 GCC 编译探测下的 DRM 单参数/双参数接口（包含 `6.18.58/59` 回移与 `7.3` 接口）。同时验证新版上游跳过旧 DRM 补丁、旧快照继续应用补丁，以及不完整探测不会被静默忽略。DRM 测试需要 GCC，缺失时会跳过；不下载内核，不代表完整驱动编译或 Unraid 硬件验证。
