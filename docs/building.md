# 构建与维护 Intel 驱动包

本页用于在 Linux 开发环境构建驱动包，或维护 GitHub Actions 的构建目标。它不在当前宿主安装驱动。

## 本地构建

准备 GCC/G++、make、bc、bison、flex、libelf 开发文件、curl、Git、tar、xz、kmod 和 Python 3。CI 的具体依赖与当前编译器版本见 [build.yml](../.github/workflows/build.yml)。

以下使用已发布的 `6.18.60-Unraid` 作为示例。替换内核时，应同时核对对应源码归档和校验和：

```bash
git clone https://github.com/hellomrli/my-i915-sriov-driver.git
cd my-i915-sriov-driver
KERNEL_RELEASE=6.18.60-Unraid scripts/build-i915-sriov.sh
```

xe 的独立入口为 `scripts/build-xe-sriov.sh`。两者最终调用 [build-intel-sriov.sh](../scripts/build-intel-sriov.sh)，只打包选中的驱动和 `intel_sriov_compat`。输出默认写入 `out/`，下载与编译目录分别是 `downloads/`、`build/`。

主要参数可通过环境变量设置：`KERNEL_RELEASE`、`KERNEL_ARCHIVE_URL`、`KERNEL_ARCHIVE_SHA256`、`I915_SRIOV_REF`、`I915_SRIOV_COMMIT`、`JOBS`、`PACKAGE_BUILD`、`OUT_DIR`。本地构建号必须是正整数，CI 会先将 `auto` 解析成具体值。

## 版本和构建目标

版本是上游固定 commit 的 UTC 日期，规范化为 `YYYY.MM.DD`，不是本地编译日期。CI 比较各驱动有效源码、配方、内核和编译器；共享依赖或兼容补丁变更会更新受影响的包，纯文档变化不会导致无效重建。

手动运行“Build Intel SR-IOV drivers for Unraid”，选择 `i915`、`xe` 或 `both`。构建号默认自动递增；手工指定的值必须高于现有构建。每天 UTC 03:30 也会检查目标。

## 检查工具

以下是仓库已有的离线检查，编译探针需要 GCC：

```bash
python3 -m unittest discover -s tests -v
```

Shell 文件可单独用 `bash -n` 检查语法。真实构建会下载较大的内核源码树和上游驱动，不属于离线测试。

## 核对产物

输出包格式是 `i915-sriov-日期-完整内核-构建号.txz` 或 `xe-sriov-日期-完整内核-构建号.txz`，旁边提供 `.md5`。i915 和 xe 使用独立 Release 标签，旧资产保留，发布工具检查完整包与版本记录。

返回[文档索引](index.md)。
