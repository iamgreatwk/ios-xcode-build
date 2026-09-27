# 已验证的 CI 工作流要点（macos-15 + Xcode 26.3）

完整工作流参考实施仓库：`github.com/iamgreatwk/ios-realtime-translator` 的
`.github/workflows/ios-unsigned.yml`。以下是结构要点与关键片段。

## 步骤骨架

```yaml
name: iOS unsigned IPA
on:
  workflow_dispatch:
  push:
    branches: [main]
    paths: ['ios/**', '.github/workflows/ios-unsigned.yml']
  pull_request:
    paths: ['ios/**', '.github/workflows/ios-unsigned.yml']

permissions:
  contents: write   # 发布 Release 需要；fork PR 下 token 自动降为只读

jobs:
  ios:
    runs-on: macos-15
    timeout-minutes: 40
    env:
      DEVELOPER_DIR: /Applications/Xcode_26.3.app/Contents/Developer
      IOS_PROJECT: ios/<App>.xcodeproj
      IOS_SCHEME: <App>
      IOS_MIN_VERSION: '18.0'
```

## 关键步骤

1. **工程与工具链校验**：`test -d "$DEVELOPER_DIR"`、`test -f project.pbxproj`、
   `test -f xcshareddata/xcschemes/$IOS_SCHEME.xcscheme`、`xcodebuild -list`。
2. **选模拟器**：`xcrun simctl list devices available -j`，解析 runtime ≥ 最低版本，
   取最新 iPhone，`simctl boot`，UDID 写入 `$GITHUB_ENV`。
3. **模拟器逻辑测试**：

```yaml
xcodebuild test \
  -project "$IOS_PROJECT" -scheme "$IOS_SCHEME" -configuration Debug \
  -destination "platform=iOS Simulator,id=$SIMULATOR_UDID" \
  -resultBundlePath work/ci/SimulatorTests.xcresult \
  IPHONEOS_DEPLOYMENT_TARGET="$IOS_MIN_VERSION" \
  CODE_SIGN_IDENTITY=- CODE_SIGNING_ALLOWED=YES CODE_SIGNING_REQUIRED=NO DEVELOPMENT_TEAM=
```

4. **无签名真机 archive**：

```yaml
xcodebuild archive \
  -project "$IOS_PROJECT" -scheme "$IOS_SCHEME" -configuration Release \
  -sdk iphoneos -destination 'generic/platform=iOS' \
  -archivePath "$RUNNER_TEMP/App.xcarchive" \
  CURRENT_PROJECT_VERSION="$GITHUB_RUN_NUMBER" \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY= DEVELOPMENT_TEAM=
```

5. **IPA 包装与校验**（python 内联）：
   - archive `Products/Applications` 恰好一个 `.app`；
   - `CFBundleSupportedPlatforms` 含 `iPhoneOS`；`MinimumOSVersion` 与配置一致；
   - 权限说明存在（如 `NSMicrophoneUsageDescription`）、`UIBackgroundModes` 含 `audio`（按需）；
   - 无 `embedded.mobileprovision`、无 `.appex`；
   - `lipo -archs` 含 arm64；`vtool -show-build` platform=IOS 且无 IOSSIMULATOR；
   - 生成 `build-info.json`（commit/run/Xcode/bundle id/架构，标注
     `BUILT_FOR_LOCAL_SIGNING_NOT_DEVICE_VERIFIED`、`local_sign_install: NOT_RUN`）。
   - `ditto` 拷贝到 `Payload/`，`zip -qry` 打包，`shasum -a 256` 出校验值。

## Release 发布（不占 Actions 存储额度）

```yaml
      - name: Publish to Release (main only)
        if: github.event_name != 'pull_request' && github.ref == 'refs/heads/main'
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          set -euo pipefail
          TAG="ios-latest"
          if gh release view "$TAG" -R "$GITHUB_REPOSITORY" >/dev/null 2>&1; then
            gh release upload "$TAG" dist/App-unsigned.ipa dist/SHA256SUMS.txt dist/build-info.json \
              --clobber -R "$GITHUB_REPOSITORY"
          else
            gh release create "$TAG" dist/App-unsigned.ipa dist/SHA256SUMS.txt dist/build-info.json \
              -R "$GITHUB_REPOSITORY" \
              --title "未签名 IPA (build $GITHUB_RUN_NUMBER)" \
              --notes "commit $GITHUB_SHA；未签名，供 iLoader 等本地工具签名安装；每次 main 构建覆盖。"
          fi
```

要点：
- workflow 顶层 `permissions: contents: write`（fork PR 自动只读，无提权风险）。
- 固定 tag `ios-latest` 覆盖发布，用户永远只记一个下载地址。
- artifact 保留期缩到 3 天（供 PR 验证与失败排查），正式产物走 Release。

## iLoader 本地签名（构建方之外的最后一环）

用户在本地桌面 iLoader 导入 IPA → Apple 账号签名 → 安装。
免费 Personal Team 限制：10 个 App ID / 3 台设备 / profile 7 天过期，需保留未签名 IPA 供重签。
