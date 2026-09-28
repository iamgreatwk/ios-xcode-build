# iOS CI 编译踩坑实录（Xcode 26.3 / macos-15 实测）

以下每条都在真实项目中触发过并已验证解法。按错误信息或场景索引。

## 1. 手写 project.pbxproj

### 1.1 对象 ID 冲突 → xcodebuild 直接崩溃（最难定位的一类）

现象：构建在 "Run simulator logic tests" 等任意阶段报
`** INTERNAL ERROR: Uncaught exception while building ** Uncaught Exception: -[PBXSourcesBuildPhase remoteContainerItem]: unrecognized selector sent to instance`。
没有任何指向 pbxproj 的错误提示。

根因：两个 section 使用了同一个 24 位十六进制 ID（本例：测试 target 的
`PBXSourcesBuildPhase` 与 `PBXContainerItemProxy` 共用 `…C1`）。
xcodebuild 解析时把后者当另一种对象，调用崩溃。

解法：
- 写 pbxproj 时用**明显分段的前缀**分配 ID（如 App=target A0 系列、build phase C/D 系列、proxy 单独段）。
- 推送前运行 `scripts/check_pbxproj.py`：扫描所有顶层定义 ID 的重复与悬空引用。

### 1.2 同步组 Info.plist 重复产出

现象：`error: Multiple commands produce '.../Interpreter.app/Info.plist'`。

根因：objectVersion 77 的 `PBXFileSystemSynchronizedRootGroup` 会把目录内所有文件当资源，
Info.plist 既被 INFOPLIST_FILE 处理又被当资源拷贝。

解法：在 pbxproj 增加异常集：

```
/* Begin PBXFileSystemSynchronizedBuildFileExceptionSet section */
		<NEW_ID> /* Exceptions */ = {
			isa = PBXFileSystemSynchronizedBuildFileExceptionSet;
			membershipExceptions = ( Info.plist, );
			target = <APP_TARGET_ID>;
		};
/* End ... */
```
并在对应 root group 的 `exceptions` 里引用该异常集。

### 1.3 CI 工程存在性检查

工作流模板通常 `test -f "$IOS_PROJECT/project.pbxproj"` 与
`test -f "$IOS_PROJECT/xcshareddata/xcschemes/<Scheme>.xcscheme"`。
共享 scheme 必须提交，TestAction 内要包含测试 target 的 TestableReference。

## 2. 测试 target 链接

### 2.1 @testable 链接 Undefined symbols

现象：编译全过，链接测试 bundle时报
`Undefined symbol: <Module>.<Type>.<member>, referenced from: ...Tests.o`。

根因：standalone 单元测试 bundle（无宿主）根本不链接主 App 模块，
`@testable import` 的符号无处解析。

解法：测试 target 两个 build settings（Debug/Release 都要）：

```
BUNDLE_LOADER = "$(TEST_HOST)";
TEST_HOST = "$(BUILT_PRODUCTS_DIR)/<App>.app/$(BUNDLE_EXECUTABLE_FOLDER_PATH)/<App>";
```

### 2.2 模拟器测试不要依赖真机硬件

测试只放纯逻辑（解析器/账本/规则）。AVAudio/Speech 在模拟器行为不同且无麦克风。

## 3. Swift 并发隔离（Swift 5 模式同样强制）

- `@MainActor` 类的存储属性，从 `nonisolated` 方法/回调（如音频 tap 回调）访问会直接报
  `main actor-isolated property ... can not be referenced from a nonisolated context`。
  解法：标记 `nonisolated(unsafe) private var`，自管锁（NSLock）保护；
  或把状态装进非隔离的小对象。
- 测试里同步调用 `@MainActor` 类的静态方法会报隔离错误 → 该静态函数标 `nonisolated static func`。
- `static let` 常量（Sendable 类型）可跨隔离访问；`static var` 不行。
- 非 `@Sendable` 闭包继承外层隔离域，`withCheckedThrowingContinuation` 体内可安全触碰 MainActor 状态。

## 4. SDK API 签名与文档/记忆不符（Xcode 26.3 实测）

| 你以为的写法 | 实际编译结果 | 正确写法 |
| --- | --- | --- |
| `LanguageAvailability(source:target:)` | `argument passed to call that takes no arguments` | `LanguageAvailability()` + `await availability.status(from: Locale.Language, to: Locale.Language?)`（Status: installed/supported/unsupported） |
| `TranslationSession.Configuration(source: Locale?, target: Locale?)` | `cannot convert 'Locale' to 'Locale.Language'` | 传 `Locale.Language(identifier:)` |
| `SFSpeechAudioBufferRecognitionRequest.endAudioInput()` | `has no member 'endAudioInput'` | `SFSpeechRecognitionTask.finish()` |
| `try file.read(into: buf)` 取返回帧数 | 返回 `Void`，`no exact matches in call to initializer` | 读后用 `buf.frameLength` 累计 |
| `AVAudioSession output?.channels` 当 Int 用 | `channels` 是 `[AVAudioSessionChannelDescription]?` | `outputs.first.map { $0.channels?.count ?? 0 }` |
| `.translationTask(...)` 直接可用 | `has no member 'translationTask'` | 文件加 `import Translation` |
| `Picker.tag(String?.none)` / `.tag(String?)` | `type 'String?.Type' cannot conform to 'Hashable'` | `.tag(nil as String?)` / `.tag(x as String?)` |

核实 API 的快速通道：`curl -s https://developer.apple.com/tutorials/data/documentation/<framework>/<symbol>.json`
（Apple 文档 JSON 端点，无需 JS，可直接 grep 签名 fragments）。

## 5. CI 工作流

- 固定 `DEVELOPER_DIR: /Applications/Xcode_<ver>.app/Contents/Developer`；runner 镜像升级移除该 Xcode 时应显式失败，不要静默换默认工具链。
- 模拟器测试用 `CODE_SIGN_IDENTITY=- CODE_SIGNING_ALLOWED=YES CODE_SIGNING_REQUIRED=NO`（免 Apple 账号）。
- 真机 archive 用 `CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY= DEVELOPMENT_TEAM=`，`-destination 'generic/platform=iOS'`。
- 包装 IPA：复制 archive 内 `.app` 到 `Payload/` 再 zip；校验 `CFBundleSupportedPlatforms` 含 iPhoneOS、`lipo -archs` 含 arm64、`vtool` platform=IOS（防把模拟器包当真机包）。
- Info.plist 需含 `NSMicrophoneUsageDescription`、`UIBackgroundModes: [audio]`（如用后台音频），工作流脚本会检查。
- `permissions:` 默认 read；发布 Release 需 `contents: write`。
- **bash 脚本里变量后紧跟全角标点**（如中文分号 `$GITHUB_RUN_NUMBER；`）会把全角字符并入变量名，
  `set -u` 下报 `unbound variable`。变量一律写 `${VAR}` 大括号形式。

## 6. 产物存储额度

- **Artifacts 计入 Actions 存储额度并按 14 天默认保留**；Release 不计费。
- 实践：IPA/构建信息发布到固定 tag 的 Release（如 `ios-latest`，`gh release upload --clobber` 覆盖），
  不使用 Artifacts，正式产物走 Release（SKILL 已同步无额度占用方案）。
- Release 步骤仅 main 触发（`if: github.event_name != 'pull_request' && github.ref == 'refs/heads/main'`）。

## 7. 迭代方法

无本地 Xcode 时每轮循环 = 改一批 → push → `gh run list` → `gh run view <id> --log-failed` 取错误 → 对照本文修复。
一次提交修一批同类错误，不要赌"应该能过"。真实项目从首提交到全绿共 8 轮。
