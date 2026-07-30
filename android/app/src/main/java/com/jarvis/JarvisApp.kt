package com.jarvis

import android.app.Application
import android.content.Context
import android.os.Build
import com.jarvis.data.auth.AndroidBiometricGate
import com.jarvis.data.auth.AuthClient
import com.jarvis.data.auth.AuthManager
import com.jarvis.data.auth.AuthStateStore
import com.jarvis.data.auth.BiometricGate
import com.jarvis.data.auth.DataStoreAuthStateStore
import com.jarvis.data.chat.ChatRepository
import com.jarvis.data.chat.ConversationsRepository
import com.jarvis.data.chat.DataStoreSessionStore
import com.jarvis.data.net.ApiSet
import com.jarvis.data.net.NetworkModule
import com.jarvis.data.net.VOICE_WS_URL
import com.jarvis.data.voice.VoiceProfileRepository
import com.jarvis.data.voice.session.AndroidMicSource
import com.jarvis.data.voice.session.AndroidSpeechToText
import com.jarvis.data.voice.session.AndroidTextToSpeech
import com.jarvis.data.voice.session.OkHttpVoiceTransport
import com.jarvis.data.voice.session.VoiceSession
import kotlinx.coroutines.CoroutineScope
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
    // Swappable so the wiring test can drive a live call without a real socket or mic —
    // and so no instrumented test ever opens a WebSocket to the deployed voice gateway.
    val voiceSessionFactory: (CoroutineScope) -> VoiceSession = { scope ->
        VoiceSession(
            transport = OkHttpVoiceTransport(),
            // Mic PCM keeps flowing for server-side speaker-ID; the conversation itself
            // is text: on-device SpeechRecognizer up (user_text), on-device TTS down
            // (jarvis_text). No server audio is played back anymore (protocol v2).
            mic = AndroidMicSource(),
            stt = AndroidSpeechToText(context.applicationContext),
            tts = AndroidTextToSpeech(context.applicationContext),
            tokenProvider = { authManager.currentToken() },
            // Feeds the server's channel-adaptive speaker gallery (spec §6): the tablet
            // and the phone are different acoustic channels and should be labeled apart.
            deviceHint = "android-" + Build.MODEL,
            scope = scope,
            voiceUrl = VOICE_WS_URL,
        )
    },
) {
    private val appContext = context.applicationContext

    private val sessionStore = DataStoreSessionStore(appContext)

    val chatRepository = ChatRepository(apis.chat, sessionStore)
    val conversationsRepository = ConversationsRepository(apis.conversations, sessionStore)
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
