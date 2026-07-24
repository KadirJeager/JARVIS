# Katman 2b — Android Native Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. Frontend/UI tasks additionally load superpowers:frontend-design at execution time.

**Goal:** Native Kotlin + Jetpack Compose Android app — Gemini-benzeri tek sohbet yüzeyi + kalıcı oturum (Credential Manager silent re-auth) — consuming the already-deployed `jarvis-brain` `/api/chat` + `/api/history`.

**Architecture:** Clean-ish layering under `android/app/src/main/java/com/jarvis/`: `data/auth` (Credential Manager), `data/net` (Retrofit + Bearer interceptor), `data/chat` (repository + DataStore session), `ui/chat` + `ui/auth` (Compose + ViewModel). Backend is unchanged; the app is a pure client. Manual DI (no Hilt — YAGNI for this slice).

**Tech Stack:** Kotlin 2.x, Jetpack Compose (BOM), AGP 9.x, Gradle, Retrofit + OkHttp + kotlinx.serialization, Coroutines/Flow, DataStore, androidx.credentials + googleid. Test: JUnit + Robolectric + kotlinx-coroutines-test (JVM), Compose UI test on headless emulator.

## Global Constraints

- **JDK 21** for the Android build (AGP 9.x is optimized for/requires JDK 21; the machine's JDK 26 is NOT compatible). Install `jdk17-openjdk`/`jdk21-openjdk` and point Gradle at JDK 21 (`org.gradle.java.home` or `JAVA_HOME`). Verify AGP↔Gradle↔JDK against the official AGP release notes before pinning.
- **Auth:** Credential Manager (`androidx.credentials` + `credentials-play-services-auth` + `com.google.android.libraries.identity.googleid:googleid`). The legacy `GoogleSignInClient` is FORBIDDEN.
- **`WEB_CLIENT_ID = "000000000000-tmu4im1mba53dmqj1gbhgba55v3i6hmb.apps.googleusercontent.com"`** — passed as `setServerClientId(...)`; it is the backend's token audience (`brain/app/config.py OAUTH_CLIENT_ID`). Do NOT invent a different client id.
- **`BASE_URL = "https://jarvis-brain-000000000000.europe-west1.run.app"`**.
- **API contract (fixed by backend):** `POST /api/chat` body `{"session_id","message"}` → `{"reply"}`; `GET /api/history?session_id=` → `{"messages":[{"role","text","ts"},...]}`; header `Authorization: Bearer <Google ID token>`; `401` on expired/invalid, `400` on bad session_id, `502` on infra. `role` ∈ `{"user","model"}`.
- **`session_id`:** a per-install persistent UUID stored in DataStore; must match backend regex `^[A-Za-z0-9._-]{1,128}$` (a bare UUID does).
- **Package:** `com.jarvis` (confirm with Kadir before the SHA-1 OAuth-client step; the package name is baked into the Android OAuth client).
- **UI:** Gemini app 2026 as the visual/interaction reference, brand **Jarvis** (LogoKJ, `LogoKJ.jpg` at repo root). UI tasks load **superpowers:frontend-design**. No Gemini logo/name/verbatim assets.
- **User-facing strings Turkish, code/identifiers English.**
- **Verification:** JVM unit tests run headless via `./gradlew test`. Instrumented/Compose-UI tests run on a **headless emulator** (`-no-window -gpu swiftshader_indirect -no-audio -no-boot-anim`; KVM is available). Prefer JVM unit tests for logic; reserve instrumented tests for real UI/integration.
- **Dependency versions** are pinned in `gradle/libs.versions.toml`; resolve exact latest-stable versions from official docs / `gradle` at implementation time (this plan gives the coordinates and APIs, not frozen version numbers, except the two identifiers above).
- **Commit per task**, trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. `android/` is a new tree at repo root; `git` from repo root.

---

### Task 0: Android toolchain (headless SDK + JDK 21 + emulator)

Ops task — no app code. Deliverable: `sdkmanager`, `avdmanager`, `gradle`, and a bootable headless AVD all work, and JDK 21 is available for the build.

**Files:** none committed (environment setup). Optionally document the setup in `android/README.md` (Step 6).

- [ ] **Step 1: Install JDK 21** (if absent): `pacman -S jdk21-openjdk` (CachyOS/Arch). Confirm: `archlinux-java status` lists java-21. Do NOT change the system default (JDK 26 stays default for other tooling); Gradle will be pointed at JDK 21 explicitly in Task 1.

- [ ] **Step 2: Install Android command-line tools** into `~/Android/Sdk`:
```bash
mkdir -p ~/Android/Sdk/cmdline-tools
# Download the latest "commandlinetools-linux-*_latest.zip" from https://developer.android.com/studio#command-line-tools-only
# unzip so the tools live at ~/Android/Sdk/cmdline-tools/latest/bin/sdkmanager
export ANDROID_HOME=~/Android/Sdk
export PATH="$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
```
Persist `ANDROID_HOME` + PATH in the shell profile (fish: `set -Ux ANDROID_HOME ~/Android/Sdk`).

- [ ] **Step 3: Install SDK packages + accept licenses:**
```bash
yes | sdkmanager --licenses
sdkmanager "platform-tools" "emulator" "build-tools;35.0.0" "platforms;android-35" "system-images;android-35;google_apis;x86_64"
```
(Confirm the current stable API level — android-35/36 — against developer.android.com; use the same level for `compileSdk`/`targetSdk` in Task 1.)

- [ ] **Step 4: Create a headless AVD:**
```bash
echo "no" | avdmanager create avd -n jarvis_avd -k "system-images;android-35;google_apis;x86_64" -d pixel_7
```

- [ ] **Step 5: Boot it headless and confirm it reaches `sys.boot_completed`:**
```bash
emulator @jarvis_avd -no-window -gpu swiftshader_indirect -no-audio -no-boot-anim -no-snapshot &
adb wait-for-device
# poll until boot completes:
until [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; do sleep 2; done
adb devices   # shows emulator-5554  device
```
Expected: `emulator-5554  device`, boot_completed=1. This proves the KVM-accelerated headless path works end-to-end before any app exists.

- [ ] **Step 6:** Write `android/README.md` documenting the exact SDK packages, `ANDROID_HOME`, JDK 21 requirement, and the AVD boot command (so the environment is reproducible). Commit:
```bash
git add android/README.md
git commit -m "chore(android): document headless SDK + JDK 21 + emulator toolchain (Katman 2b)"
```

---

### Task 1: Gradle + Compose skeleton ("Hello Jarvis")

A buildable, installable, launchable empty Compose app. Locks the toolchain wiring (AGP/Gradle/JDK21/Compose) before any feature code.

**Files:**
- Create: `android/settings.gradle.kts`, `android/build.gradle.kts`, `android/gradle/libs.versions.toml`, `android/gradle.properties`, `android/app/build.gradle.kts`, `android/app/src/main/AndroidManifest.xml`, `android/app/src/main/java/com/jarvis/MainActivity.kt`, `android/app/src/main/java/com/jarvis/ui/theme/Theme.kt`
- Create: `android/app/src/androidTest/java/com/jarvis/SmokeTest.kt`

**Interfaces:**
- Produces: a `com.jarvis` app module, `compileSdk`/`targetSdk` = current stable (e.g. 35), `minSdk` = 26 (Credential Manager needs 23+; 26 is a safe modern floor), `MainActivity` hosting a Compose `setContent { JarvisTheme { Text("Jarvis") } }`.

- [ ] **Step 1: Gradle wiring.** `gradle.properties` sets `org.gradle.java.home=/usr/lib/jvm/java-21-openjdk` (the JDK 21 path from Task 0). `libs.versions.toml` declares AGP 9.x, Kotlin 2.x, Compose BOM, `androidx.activity:activity-compose`, `core-ktx`. `settings.gradle.kts` uses `pluginManagement`/`dependencyResolutionManagement` with `google()`/`mavenCentral()`. `app/build.gradle.kts` applies `com.android.application` + `org.jetbrains.kotlin.android` + `org.jetbrains.kotlin.plugin.compose`, sets `buildFeatures { compose = true }`, `namespace = "com.jarvis"`, the SDK levels above, and `packaging`/`compileOptions` for JDK 21 (`jvmTarget = "21"`).

- [ ] **Step 2: Minimal app.** `AndroidManifest.xml` with one `MainActivity` (exported, LAUNCHER intent-filter). `MainActivity.kt`:
```kotlin
package com.jarvis

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.Text
import com.jarvis.ui.theme.JarvisTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { JarvisTheme { Text("Jarvis") } }
    }
}
```
`ui/theme/Theme.kt`: a minimal Material3 `JarvisTheme { content }` wrapper (full palette comes in Task 6 via frontend-design).

- [ ] **Step 3: Build (proves AGP/JDK21/Compose wiring).**
Run: `cd android && ./gradlew assembleDebug`
Expected: `BUILD SUCCESSFUL`, an APK at `app/build/outputs/apk/debug/app-debug.apk`. If it fails on JDK, fix `org.gradle.java.home` before proceeding — do not work around with the wrong JDK.

- [ ] **Step 4: Instrumented smoke test.** `SmokeTest.kt` uses `createAndroidComposeRule<MainActivity>()` and asserts `onNodeWithText("Jarvis").assertIsDisplayed()`.

- [ ] **Step 5: Run it on the headless emulator** (booted per Task 0 Step 5):
Run: `cd android && ./gradlew connectedDebugAndroidTest`
Expected: 1 test passes on `emulator-5554`. This proves the full build→install→run loop works headless.

- [ ] **Step 6: Commit.**
```bash
git add android/
git commit -m "feat(android): Gradle+Compose skeleton, buildable & emulator-verified (Katman 2b)"
```

---

### Task 2: Networking — Retrofit API + models + Bearer interceptor

**Files:**
- Create: `android/app/src/main/java/com/jarvis/data/net/JarvisApi.kt`, `.../net/Models.kt`, `.../net/AuthInterceptor.kt`, `.../net/NetworkModule.kt`
- Create: `android/app/src/test/java/com/jarvis/data/net/AuthInterceptorTest.kt`, `.../net/SerializationTest.kt`
- Modify: `app/build.gradle.kts` (retrofit, okhttp, kotlinx-serialization, converter)

**Interfaces:**
- Produces:
  - `data class ChatRequest(val session_id: String, val message: String)`, `data class ChatResponse(val reply: String)`, `data class HistoryMessage(val role: String, val text: String, val ts: String)`, `data class HistoryResponse(val messages: List<HistoryMessage>)` — all `@Serializable`, field names matching the JSON exactly.
  - `interface JarvisApi { @POST("api/chat") suspend fun chat(@Body req: ChatRequest): ChatResponse; @GET("api/history") suspend fun history(@Query("session_id") sessionId: String): HistoryResponse }`
  - `class AuthInterceptor(private val tokenProvider: () -> String?) : Interceptor` — adds `Authorization: Bearer <token>` when a token exists.
  - `NetworkModule.create(tokenProvider): JarvisApi` — builds OkHttp (with the interceptor) + Retrofit (BASE_URL, kotlinx.serialization converter).

- [ ] **Step 1: Add deps** to `app/build.gradle.kts` / `libs.versions.toml`: `retrofit`, `retrofit2-kotlinx-serialization-converter`, `okhttp` (+`logging-interceptor` for debug), `kotlinx-serialization-json`; apply `org.jetbrains.kotlin.plugin.serialization`.

- [ ] **Step 2: Write failing tests.** `SerializationTest.kt`: round-trip `ChatRequest`/`HistoryResponse` through `Json` and assert field names (`session_id`, `messages[0].role/text/ts`). `AuthInterceptorTest.kt`: use OkHttp `MockWebServer`; with `tokenProvider = { "tok" }`, assert the recorded request has header `Authorization: Bearer tok`; with `tokenProvider = { null }`, assert no `Authorization` header.

- [ ] **Step 3: Run, verify fail.** `cd android && ./gradlew :app:testDebugUnitTest --tests "com.jarvis.data.net.*"` → FAIL (classes not defined).

- [ ] **Step 4: Implement** `Models.kt`, `JarvisApi.kt`, `AuthInterceptor.kt`, `NetworkModule.kt`:
```kotlin
// AuthInterceptor.kt
class AuthInterceptor(private val tokenProvider: () -> String?) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val token = tokenProvider()
        val req = if (token != null)
            chain.request().newBuilder().addHeader("Authorization", "Bearer $token").build()
        else chain.request()
        return chain.proceed(req)
    }
}
```
`NetworkModule` builds `Retrofit.Builder().baseUrl("$BASE_URL/").client(okhttp).addConverterFactory(Json{ignoreUnknownKeys=true}.asConverterFactory("application/json".toMediaType())).build().create(JarvisApi::class.java)`.

- [ ] **Step 5: Run, verify pass.** Same command → PASS.

- [ ] **Step 6: Commit.** `git add android/app && git commit -m "feat(android): Retrofit JarvisApi + models + Bearer interceptor (Katman 2b)"`

---

### Task 3: Auth — AuthManager (Credential Manager) + 401 silent-retry

**Files:**
- Create: `android/app/src/main/java/com/jarvis/data/auth/AuthManager.kt`
- Modify: `.../net/AuthInterceptor.kt` (401 silent re-auth + single retry) OR add a `TokenAuthenticator` — see Step 4
- Create: `android/app/src/test/java/com/jarvis/data/auth/TokenRefreshTest.kt`
- Modify: `app/build.gradle.kts` (credentials deps)

**Interfaces:**
- Consumes: `WEB_CLIENT_ID`.
- Produces:
  - `class AuthManager(context)` with `suspend fun signIn(): Result<String>` (interactive, `filterByAuthorizedAccounts=false`), `suspend fun silentSignIn(): Result<String>` (`filterByAuthorizedAccounts=true` + `autoSelectEnabled=true`), `fun currentToken(): String?` (cached), `fun clear()`. Each returns the Google ID token.
  - Wiring so a `401` from any API call triggers `silentSignIn()` once and retries.

- [ ] **Step 1: Add deps:** `androidx.credentials:credentials`, `androidx.credentials:credentials-play-services-auth`, `com.google.android.libraries.identity.googleid:googleid` (latest stable).

- [ ] **Step 2: Implement `AuthManager`** using the verified API:
```kotlin
class AuthManager(private val context: Context) {
    private val cm = CredentialManager.create(context)
    @Volatile private var token: String? = null
    fun currentToken(): String? = token
    fun clear() { token = null }

    suspend fun signIn(): Result<String> = get(filterAuthorized = false, autoSelect = false)
    suspend fun silentSignIn(): Result<String> = get(filterAuthorized = true, autoSelect = true)

    private suspend fun get(filterAuthorized: Boolean, autoSelect: Boolean): Result<String> = try {
        val option = GetGoogleIdOption.Builder()
            .setServerClientId(WEB_CLIENT_ID)
            .setFilterByAuthorizedAccounts(filterAuthorized)
            .setAutoSelectEnabled(autoSelect)
            .build()
        val request = GetCredentialRequest.Builder().addCredentialOption(option).build()
        val response = cm.getCredential(context, request)
        val cred = response.credential
        if (cred is CustomCredential && cred.type == GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL) {
            val idToken = GoogleIdTokenCredential.createFrom(cred.data).idToken
            token = idToken
            Result.success(idToken)
        } else Result.failure(IllegalStateException("Beklenmeyen kimlik türü"))
    } catch (e: NoCredentialException) {
        Result.failure(e)        // silent: no authorized account -> caller falls back to interactive
    } catch (e: GetCredentialException) {
        Result.failure(e)
    }
}
```
(`context` for `getCredential` should be an Activity context at call sites; `AuthManager` may hold the application context for `CredentialManager.create` and take an Activity in the sign-in call if needed — resolve the exact context threading against the current androidx.credentials API when implementing.)

- [ ] **Step 3: 401 handling.** Prefer an OkHttp `Authenticator` (runs on 401): `TokenAuthenticator` calls `runBlocking { authManager.silentSignIn() }`, and on success returns the request with the new Bearer header; returns `null` (give up) if it already retried once or silent fails. Add it via `OkHttpClient.Builder().authenticator(...)` in `NetworkModule`. Guard against infinite loop by checking `responseCount(response) <= 1`.

- [ ] **Step 4: Test (JVM, no real Google).** `TokenRefreshTest.kt` with `MockWebServer`: queue a `401` then a `200`; inject a fake `AuthManager`-like token source whose `silentSignIn()` returns a fresh token; assert the second (retried) request carries the new Bearer and the call ultimately succeeds; assert only ONE retry (a persistent 401 gives up, no infinite loop). Test the `TokenAuthenticator`/interceptor logic against the fake — do NOT call real Credential Manager in a unit test.

- [ ] **Step 5: Run tests** `./gradlew :app:testDebugUnitTest --tests "com.jarvis.data.auth.*"` → PASS.

- [ ] **Step 6: Commit.** `git commit -m "feat(android): Credential Manager auth + 401 silent re-auth retry (Katman 2b)"`

> NOTE: the real interactive/silent sign-in against Google can only be verified on the emulator with a signed-in Google account and (if required) a registered Android OAuth client SHA-1. That is Task 7's integration step / a HITL check, not this unit task.

---

### Task 4: SessionStore (DataStore) + ChatRepository

**Files:**
- Create: `.../data/chat/SessionStore.kt`, `.../data/chat/ChatRepository.kt`, `.../data/chat/UiMessage.kt`
- Create: `android/app/src/test/java/com/jarvis/data/chat/ChatRepositoryTest.kt`
- Modify: `app/build.gradle.kts` (datastore-preferences)

**Interfaces:**
- Consumes: `JarvisApi` (Task 2).
- Produces:
  - `data class UiMessage(val role: String, val text: String)` (role `"user"|"model"`).
  - `class SessionStore(context)` `suspend fun sessionId(): String` — reads a persisted UUID from DataStore, generating + storing one on first call (matches `^[A-Za-z0-9._-]{1,128}$`).
  - `class ChatRepository(api: JarvisApi, session: SessionStore)` with `suspend fun loadHistory(): List<UiMessage>` (maps `HistoryResponse` → `UiMessage`) and `suspend fun send(text: String): UiMessage` (calls `chat`, returns the model reply as a `UiMessage`).

- [ ] **Step 1: Add** `androidx.datastore:datastore-preferences`.

- [ ] **Step 2: Failing test.** `ChatRepositoryTest.kt`: fake `JarvisApi` (a hand-written test double implementing the interface) returning a canned `HistoryResponse` and `ChatResponse`; a fake `SessionStore` returning a fixed id. Assert `loadHistory()` maps roles/text correctly and `send("selam")` returns `UiMessage("model", <reply>)` and passed the fixed session_id to `chat`.

- [ ] **Step 3: Verify fail** → PASS after Step 4. `./gradlew :app:testDebugUnitTest --tests "com.jarvis.data.chat.*"`.

- [ ] **Step 4: Implement.** `SessionStore` uses a `preferencesDataStore("jarvis")` + a `stringPreferencesKey("session_id")`; on read, if absent, `UUID.randomUUID().toString()` (bare UUID passes the backend regex) and persist. `ChatRepository.loadHistory()` = `api.history(session.sessionId()).messages.map { UiMessage(it.role, it.text) }`; `send()` = `api.chat(ChatRequest(session.sessionId(), text)).reply.let { UiMessage("model", it) }`.

- [ ] **Step 5: Verify pass.**

- [ ] **Step 6: Commit.** `git commit -m "feat(android): persistent SessionStore + ChatRepository (Katman 2b)"`

---

### Task 5: ChatViewModel (StateFlow)

**Files:**
- Create: `.../ui/chat/ChatViewModel.kt`, `.../ui/chat/ChatUiState.kt`
- Create: `android/app/src/test/java/com/jarvis/ui/chat/ChatViewModelTest.kt`
- Modify: `app/build.gradle.kts` (lifecycle-viewmodel-compose, kotlinx-coroutines-test for tests)

**Interfaces:**
- Consumes: `ChatRepository`, `AuthManager`.
- Produces:
  - `data class ChatUiState(val messages: List<UiMessage> = emptyList(), val input: String = "", val sending: Boolean = false, val loading: Boolean = false, val error: String? = null, val signedIn: Boolean = false)`
  - `class ChatViewModel(repo: ChatRepository, auth: AuthManager)` exposing `val state: StateFlow<ChatUiState>` and `fun onInputChange(s)`, `fun send()`, `fun refreshHistory()`, `fun onSignedIn()`.

- [ ] **Step 1: Failing test.** `ChatViewModelTest.kt` with `StandardTestDispatcher` (`Dispatchers.setMain`). Fake repo. Assert: `send()` optimistically appends the user `UiMessage`, sets `sending=true`, then appends the model reply and clears `sending`; a repo exception sets a Turkish `error` and does not lose the typed input; `refreshHistory()` populates `messages` and toggles `loading`.

- [ ] **Step 2: Verify fail** → PASS after Step 3. `./gradlew :app:testDebugUnitTest --tests "com.jarvis.ui.chat.*"`.

- [ ] **Step 3: Implement** with `viewModelScope.launch`, a `MutableStateFlow<ChatUiState>`, immutable `copy()` updates. On `send()`: append user message + `sending=true`, call `repo.send`, append reply / on failure set `error = "Gönderilemedi: ..."`. On `refreshHistory()`: `loading=true`, `repo.loadHistory()`, populate.

- [ ] **Step 4: Verify pass.**

- [ ] **Step 5: Commit.** `git commit -m "feat(android): ChatViewModel state machine (Katman 2b)"`

---

### Task 6: Compose UI — ChatScreen + SignInScreen (Gemini-referenced)

**Load superpowers:frontend-design before this task.** Visual language references Gemini app 2026 (minimalist, immersive single thread, soft gradient, large readable text) with the Jarvis palette + LogoKJ; no Gemini branding.

**Files:**
- Create: `.../ui/chat/ChatScreen.kt`, `.../ui/auth/SignInScreen.kt`, `.../ui/theme/Color.kt`+`Type.kt` (Jarvis palette/typography)
- Create: `android/app/src/androidTest/java/com/jarvis/ui/ChatScreenTest.kt`
- Add: `LogoKJ` as a drawable resource (`app/src/main/res/drawable/`)

**Interfaces:**
- Consumes: `ChatUiState`, `ChatViewModel` callbacks.
- Produces: `@Composable fun ChatScreen(state: ChatUiState, onInput, onSend, onRetry)` — a scrolling message list (user bubbles right, model bubbles left, Gemini-like spacing) + an input bar with a send button; `@Composable fun SignInScreen(onSignIn)` — Jarvis logo + a "Google ile giriş" button.

- [ ] **Step 1: Theme.** `Color.kt`/`Type.kt` define the Jarvis palette (dark-first, gradient accents) and type scale per frontend-design guidance; `JarvisTheme` (from Task 1) consumes them.

- [ ] **Step 2: Failing UI test.** `ChatScreenTest.kt` (`createComposeRule`): render `ChatScreen` with a fixed `ChatUiState` containing one user + one model message; assert both texts are displayed; type into the input and assert `onInput` fired; tap send and assert `onSend` fired. (Pure Compose test, no ViewModel/emulator dependency beyond the Compose runtime — runs on the emulator via `connectedAndroidTest`.)

- [ ] **Step 3: Verify fail** → PASS after Step 4.

- [ ] **Step 4: Implement** `ChatScreen` (`LazyColumn` of message bubbles + `Row` input bar with `TextField` + `IconButton`), `SignInScreen`. Keep composables stateless (state hoisted to the ViewModel); user-facing strings Turkish. Apply the Gemini-referenced layout from frontend-design.

- [ ] **Step 5: Run on headless emulator.** `cd android && ./gradlew connectedDebugAndroidTest --tests "com.jarvis.ui.ChatScreenTest"` → PASS. Capture a screenshot (`adb exec-out screencap -p > /tmp/jarvis-chat.png`) for visual confirmation of the Gemini-referenced look.

- [ ] **Step 6: Commit.** `git commit -m "feat(android): Gemini-referenced ChatScreen + SignInScreen (Katman 2b)"`

---

### Task 7: MainActivity wiring + manual DI + end-to-end integration

**Files:**
- Modify: `.../MainActivity.kt`
- Create: `.../JarvisApp.kt` (manual DI container), `.../ui/Nav.kt` (auth ↔ chat switch)
- Create: `android/app/src/androidTest/java/com/jarvis/EndToEndTest.kt`

**Interfaces:**
- Consumes: everything above.
- Produces: app boot flow — on launch `silentSignIn()`; if a token exists show `ChatScreen` (+ load history), else `SignInScreen`; sign-in success transitions to chat.

- [ ] **Step 1: Manual DI.** `JarvisApp` (Application subclass or a plain object) constructs the singletons: `AuthManager`, `NetworkModule.create { authManager.currentToken() }` → `JarvisApi`, `SessionStore`, `ChatRepository`. `AuthInterceptor`/`TokenAuthenticator` read the token via `authManager.currentToken()`/`silentSignIn()`.

- [ ] **Step 2: MainActivity** builds a `ChatViewModel` from the container and renders `Nav(state)` — `SignInScreen` when `!state.signedIn`, else `ChatScreen`. On first composition, launch `silentSignIn()`; update `signedIn` accordingly; on chat entry call `refreshHistory()`.

- [ ] **Step 3: Failing e2e test.** `EndToEndTest.kt` on the emulator: with a fake API layer injected into the container (a debug/test flavor or a test-only `JarvisApp` override serving canned history + reply — NO real network in the automated test), assert: launching lands on chat (simulate a present token), history bubbles render, typing + send appends a reply. Real Google sign-in + real backend is the HITL step below, not the automated test.

- [ ] **Step 4: Verify** `./gradlew connectedDebugAndroidTest` → all instrumented tests PASS on the headless emulator. Full JVM suite: `./gradlew testDebugUnitTest` → PASS.

- [ ] **Step 5: HITL — real sign-in + live backend** (Kadir + controller): install on the emulator/a real device (`./gradlew installDebug`), sign in with the allowlisted Google account, send a message, kill+reopen the app and confirm the session persists and history loads from the live `jarvis-brain`. If sign-in fails with a SHA-1 / audience error, register the Android OAuth client (package `com.jarvis` + `./gradlew signingReport` debug SHA-1) in Google Cloud Console — the plan's one expected HITL. Then the token-authenticated `/api/history` backend smoke (deferred from the backend plan) is also satisfied.

- [ ] **Step 6: Commit.** `git commit -m "feat(android): app wiring + auth/chat nav + e2e emulator test (Katman 2b)"`

---

## Self-Review notes (coverage vs spec)

- Kalıcı oturum: Credential Manager silent re-auth (Task 3) + persistent session_id (Task 4) + boot-time silentSignIn (Task 7) → covers spec's "giriş hatırlanır".
- Kalıcı geçmiş: `loadHistory()` from backend `/api/history` (Task 4) + render (Task 6) + boot refresh (Task 7) → "önceki mesajlar görünür".
- Kalıcı bağlam: handled entirely by the backend (rehydration, already deployed) — the client just uses a stable session_id.
- Backend unchanged: only `WEB_CLIENT_ID` as `serverClientId` + `Bearer` token; no backend edits.
- Out of scope (later slices): voice + **speaker ID (Kadir'i sesinden tanıma)**, multi-conversation drawer, ASSIST/power-button, approval center.

## Open items to resolve at implementation time (research-first, no guessing)
- Exact pinned versions (AGP/Gradle/Kotlin/Compose BOM/credentials/retrofit) from official docs + `./gradlew` — especially the AGP↔JDK 21 matrix.
- Precise `androidx.credentials` context threading for `getCredential` (Activity vs application context) against the installed version.
- Current stable `compileSdk`/system-image API level (35 vs 36).
- `com.jarvis` package name confirmation with Kadir (affects the Android OAuth client).
