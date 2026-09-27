---
name: ios-xcode-build
description: This skill should be used when building iOS apps without a local Mac — i.e. compiling an Xcode project for an iOS app on Linux/Windows via GitHub Actions, generating unsigned device IPAs, writing or debugging project.pbxproj by hand, setting up simulator XCTest in CI, or troubleshooting iOS CI build failures (pbxproj crashes, Info.plist duplicate outputs, Swift concurrency/actor-isolation errors, SDK API signature mismatches, test-bundle link errors). Encodes hard-won pitfalls from a real project that reached a green unsigned-IPA pipeline on macos-15/Xcode 26.3.
---

# iOS 无 Mac 构建（GitHub Actions 未签名 IPA）

## 目的

在没有本地 Mac 的条件下，把 iOS App（SwiftUI/Xcode 工程）通过 GitHub Actions macOS runner
编译为**未签名真机 IPA**，并跑通模拟器 XCTest。本 skill 固化一条已验证跑通的链路，
以及沿途所有会浪费数小时迭代的问题的确定性解法。

## 何时使用

- 从零（或从 Swift 源码目录）搭建可 CI 构建的 iOS 工程，尤其是**手写 project.pbxproj** 时
- CI 构建失败，需要按已知坑清单快速定位
- 需要把 IPA 发布到 GitHub Release（避免占用 Actions 存储额度）

## 工作流

1. **工程结构**：`ios/<App>.xcodeproj` + 源码目录 + `xcshareddata/xcschemes/<App>.xcscheme`（CI 模板会检查该路径存在）。手写 pbxproj 采用 objectVersion 77 + `PBXFileSystemSynchronizedRootGroup`（整目录同步，无需逐文件登记）。
2. **推送前本地自检**：运行 `scripts/check_pbxproj.py ios/<App>.xcodeproj/project.pbxproj`，拦截对象 ID 冲突、未定义引用、括号失配——这是手写 pbxproj 最高频的致命错误。
3. **CI 工作流**：按 `references/ci-workflow.md` 搭建（固定 DEVELOPER_DIR → 模拟器测试 → 无签名 archive → IPA 包装校验 → Release 发布）。
4. **失败迭代**：编译错误用 `gh run view <run-id> --log-failed | grep -E "error:" | sort -u` 提取；或下载 work/ci 证据产物。按 `references/pitfalls.md` 对照修复，一轮只修一批，重推再迭代。
5. **产物去向**：IPA 发 GitHub Release（Release 不计入 Actions 存储额度；artifact 按额度计费，保留期建议 3 天）。

## 关键红线（先读 pitfalls 再写代码）

- pbxproj 中**对象 ID 全局唯一**——重复 ID 不会报配置错误，而是 xcodebuild 直接崩溃（`remoteContainerItem: unrecognized selector`）。
- 同步组内的 Info.plist 必须用 `PBXFileSystemSynchronizedBuildFileExceptionSet` 排除，否则 "Multiple commands produce Info.plist"。
- 单元测试要访问 `@testable import <App>`，测试 target **必须**配置 `TEST_HOST` + `BUNDLE_LOADER`，否则链接期 Undefined symbols。
- 不要假设任何 Apple API 签名；以当前 SDK 实际报错为准（详见 pitfalls §4 的真实案例）。

## 资源

- `scripts/check_pbxproj.py` — 推送前校验 pbxproj（ID 冲突/悬空引用/括号配平）
- `references/pitfalls.md` — 全部踩坑记录与确定性解法（按错误信息索引）
- `references/ci-workflow.md` — 已验证的 Actions 工作流要点与 Release 发布步骤
