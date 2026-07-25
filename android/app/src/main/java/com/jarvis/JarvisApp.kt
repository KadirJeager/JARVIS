package com.jarvis

import android.app.Application
import android.content.Context
import com.jarvis.data.auth.AndroidBiometricGate
import com.jarvis.data.auth.AuthClient
import com.jarvis.data.auth.AuthManager
import com.jarvis.data.auth.AuthStateStore
import com.jarvis.data.auth.BiometricGate
import com.jarvis.data.auth.DataStoreAuthStateStore
import com.jarvis.data.chat.ChatRepository
import com.jarvis.data.chat.DataStoreSessionStore
import com.jarvis.data.net.ApiSet
import com.jarvis.data.net.NetworkModule
import com.jarvis.data.voice.VoiceProfileRepository
import kotlinx.coroutines.runBlocking

/**
 * Hand-rolled DI container (no Hilt — YAGNI for this slice). Wires the deployed backend
 * client with the auth token provider and the 401 silent-refresh path.
 *
 * [authManager], [biometricGate] and [apis] are constructor params, not hardcoded fields:
 * an instrumented test installs fakes onto [JarvisApp.container] before `MainActivity`
 * launches, so the real biometric-gate wiring inside the Activity can be pinned by a test
 * instead of only by the manual HITL checklist.
 *
 * [apis] is swappable for a second reason: without it a test that installs a fake
 * `AuthClient` puts the app in a signed-in state that then fires REAL requests at the
 * deployed backend. Read-only and 401-bound, but a test suite must not talk to
 * production at all.
 */
class AppContainer(
    context: Context,
    val authManager: AuthClient = AuthManager(context.applicationContext),
    val biometricGate: BiometricGate = AndroidBiometricGate(context.applicationContext),
    // A default expression may reference earlier parameters, so `authManager` is in scope
    // here and the real wiring stays the default rather than something callers assemble.
    private val apis: ApiSet = NetworkModule.createApis(
        tokenProvider = { authManager.currentToken() },
        // Runs on OkHttp's background thread, so blocking here is fine.
        tokenRefresher = { runBlocking { authManager.silentSignIn().getOrNull() } },
    ),
    // Swappable so a test can say "this device has signed in before" and assert the boot
    // path actually skips the splash — otherwise only the ViewModel would be pinned, and
    // deleting the call from MainActivity would leave the suite green.
    val authStateStore: AuthStateStore = DataStoreAuthStateStore(context.applicationContext),
) {
    private val appContext = context.applicationContext

    val chatRepository = ChatRepository(apis.chat, DataStoreSessionStore(appContext))
    val voiceProfileRepository = VoiceProfileRepository(apis.voice)
}

class JarvisApp : Application() {
    /**
     * No `private set`: an instrumented test needs to install a container built with
     * fake collaborators before `MainActivity`'s `onCreate` reads it, which happens on
     * process-wide `Application` singleton this class already is.
     */
    lateinit var container: AppContainer

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
    }
}
