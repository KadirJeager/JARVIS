# JARVIS Android (Katman 2b — Native Chat)

Native Kotlin + Jetpack Compose client for the deployed `jarvis-brain` service.
Single Gemini-referenced chat surface + persistent session (Credential Manager
silent re-auth). Backend is unchanged; this app is a pure client.

This README documents the **reproducible toolchain** (Task 0). App build config
lands in Task 1 (`settings.gradle.kts` etc.).

## Verified toolchain (2026-07-24, CachyOS/Arch)

| Component | Version / value |
|-----------|-----------------|
| JDK for the build | **Java 21** — `/usr/lib/jvm/java-21-openjdk` (`jdk21-openjdk` 21.0.12). System default stays JDK 26; Gradle is pointed at 21 explicitly. |
| `ANDROID_HOME` | `~/Android/Sdk` |
| cmdline-tools | `sdkmanager` 22.0 (build 15859902), installed at `~/Android/Sdk/cmdline-tools/latest/bin` |
| platform-tools | 37.0.0 |
| emulator | 36.6.11 |
| build-tools | `build-tools;36.0.0` |
| platform | `platforms;android-36` (Android 16 "Baklava", **API 36**) |
| system image | `system-images;android-36;google_apis;x86_64` |
| AVD | `jarvis_avd` (pixel_7, android-36 google_apis x86_64) |
| Acceleration | KVM (`/dev/kvm`), software GPU (`swiftshader_indirect`) |

**API level = 36** → `compileSdk` / `targetSdk` = 36 in Task 1. `minSdk` = 26.

## One-time setup

### 1. JDK 21 (keep system default = 26)
```bash
sudo pacman -S jdk21-openjdk        # CachyOS/Arch
archlinux-java status               # lists java-21-openjdk (do NOT set it default)
```

### 2. Android command-line tools
Download the latest `commandlinetools-linux-*_latest.zip` from
<https://developer.android.com/studio#command-line-tools-only> and place it so
`sdkmanager` lives at `~/Android/Sdk/cmdline-tools/latest/bin/sdkmanager`:
```bash
mkdir -p ~/Android/Sdk/cmdline-tools
unzip commandlinetools-linux-*_latest.zip -d /tmp/cmdline
mv /tmp/cmdline/cmdline-tools ~/Android/Sdk/cmdline-tools/latest
```

### 3. Persistent environment (fish)
```fish
set -Ux ANDROID_HOME ~/Android/Sdk
fish_add_path $ANDROID_HOME/cmdline-tools/latest/bin \
              $ANDROID_HOME/platform-tools \
              $ANDROID_HOME/emulator
```
`sdkmanager` must run under **JDK 21** (`JAVA_HOME=/usr/lib/jvm/java-21-openjdk`),
not the system default 26.

### 4. SDK packages + licenses
```bash
yes | sdkmanager --licenses
sdkmanager "platform-tools" "emulator" "build-tools;36.0.0" \
           "platforms;android-36" "system-images;android-36;google_apis;x86_64"
```

### 5. Headless AVD
```bash
echo "no" | avdmanager create avd -n jarvis_avd \
  -k "system-images;android-36;google_apis;x86_64" -d pixel_7
# The "Could not load devices.xml" line is a cosmetic warning; the AVD is created.
```

## Boot the headless emulator (needed for instrumented/Compose-UI tests)
```bash
emulator @jarvis_avd -no-window -gpu swiftshader_indirect \
         -no-audio -no-boot-anim -no-snapshot &
adb wait-for-device
until [ "$(adb shell getprop sys.boot_completed | tr -d '\r')" = "1" ]; do sleep 2; done
adb devices     # expect: emulator-5554  device
```
Verified: `emulator-5554 device`, `sys.boot_completed=1`, Android 16 / API 36.

## Build & test (from Task 1 onward)
```bash
cd android
./gradlew assembleDebug              # JDK 21 via org.gradle.java.home
./gradlew testDebugUnitTest          # JVM unit tests (headless)
./gradlew connectedDebugAndroidTest  # instrumented tests on the booted AVD
```
