package com.jarvis

import android.app.Application
import android.content.Context
import android.media.AudioManager
import android.os.Build
import android.util.Log
import com.jarvis.data.auth.AndroidBiometricGate
import com.jarvis.data.auth.AuthClient
import com.jarvis.data.auth.AuthManager
import com.jarvis.data.approvals.ApprovalRepository
import com.jarvis.data.auth.AuthStateStore
import com.jarvis.data.auth.BiometricGate
import com.jarvis.data.auth.DataStoreAuthStateStore
import com.jarvis.data.chat.ChatRepository
import com.jarvis.data.chat.ConversationsRepository
import com.jarvis.data.chat.DataStoreSessionStore
import com.jarvis.data.net.ApiSet
import com.jarvis.data.net.DeviceTokenRequest
import com.jarvis.data.net.NetworkModule
import com.jarvis.data.net.VOICE_WS_URL
import com.jarvis.data.push.FcmTokenRegistrar
import com.jarvis.data.push.PUSH_LOG_TAG
import com.jarvis.data.push.firebaseMessagingToken
import com.jarvis.data.voice.VoiceProfileRepository
import com.jarvis.data.voice.session.AndroidMicSource
import com.jarvis.data.voice.session.AndroidSpeechToText
import com.jarvis.data.voice.session.AndroidTextToSpeech
import com.jarvis.data.voice.session.OkHttpVoiceTransport
import com.jarvis.data.voice.session.VoiceSession
import com.jarvis.data.wear.WatchPairing
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.tasks.await

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
        // force=true: a 401 means the held token is bad no matter what its `exp`
        // claims, so this path must bypass the freshness reuse and actually mint.
        tokenRefresher = { runBlocking { authManager.silentSignIn(force = true).getOrNull() } },
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
            mic = AndroidMicSource(context.applicationContext.getSystemService(Context.AUDIO_SERVICE) as AudioManager),
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
    val approvalRepository = ApprovalRepository(apis.approvals)

    /**
     * Shared by BOTH producers of a token: `JarvisFCMService.onNewToken` and the app-open
     * path below. One instance, so its "already registered this token" guard actually
     * spans them instead of each holding its own idea of what was sent.
     */
    val fcmTokenRegistrar = FcmTokenRegistrar(
        api = apis.fcm,
        // The same signal AuthInterceptor uses to decide whether to attach a Bearer
        // header. Anything else here would let the two disagree about "signed in".
        isSignedIn = { authManager.currentToken() != null },
        currentToken = { firebaseMessagingToken() },
    )

    /**
     * Registers this device for push. Called on every app open once a session exists,
     * because `onNewToken` fires only when the token CHANGES and therefore never fires at
     * all on a device whose token predates this build.
     */
    suspend fun registerForPush() {
        val outcome = fcmTokenRegistrar.registerCurrentToken()
        Log.i(PUSH_LOG_TAG, "fcm: uygulama açılışı kayıt sonucu=$outcome")
    }

    /**
     * Watch pairing (Wear W1 Task 3): mints a durable device token over the SAME
     * authenticated Retrofit chain as every other call — [apis.deviceTokens] rides
     * [NetworkModule.createApis]'s `tokenProvider`/`tokenRefresher`, so this always
     * carries the phone's own fresh Google Bearer, never a device token (which the
     * endpoint would 403 anyway, and which the phone never holds). The minted token is
     * pushed to every connected watch node over the Wearable Data Layer and is never
     * logged or persisted here — see [WatchPairing].
     */
    val watchPairing = WatchPairing(
        mint = { device -> apis.deviceTokens.mint(DeviceTokenRequest(device)).token },
        listNodes = { Wearable.getNodeClient(appContext).connectedNodes.await().map { it.id } },
        sendTo = { nodeId, path, payload ->
            Wearable.getMessageClient(appContext).sendMessage(nodeId, path, payload).await()
        },
    )
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
        // The channel must exist BEFORE the first notification lands on it. A backgrounded
        // app's push is drawn by the system tray, which never calls JarvisFCMService — so
        // creating the channel only there would leave the production path (approval
        // arrives while the app is closed) posting onto a channel that does not exist.
        JarvisFCMService.ensureChannel(this)
    }
}
