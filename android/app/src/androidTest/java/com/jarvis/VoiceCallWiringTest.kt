package com.jarvis

import android.app.ActivityManager
import android.content.Context
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.rule.GrantPermissionRule
import com.jarvis.data.auth.AuthClient
import com.jarvis.data.auth.AuthStateStore
import com.jarvis.data.net.ApiSet
import com.jarvis.data.net.ApprovalApi
import com.jarvis.data.net.ApprovalDecisionDto
import com.jarvis.data.net.ApprovalDto
import com.jarvis.data.net.ApprovalsResponse
import com.jarvis.data.net.ChatRequest
import com.jarvis.data.net.ChatResponse
import com.jarvis.data.net.ConfirmResponse
import com.jarvis.data.net.ConversationDeletedResponse
import com.jarvis.data.net.ConversationsApi
import com.jarvis.data.net.ConversationsResponse
import com.jarvis.data.net.HistoryResponse
import com.jarvis.data.net.JarvisApi
import com.jarvis.data.net.LabelPatch
import com.jarvis.data.net.NotePatch
import com.jarvis.data.net.ProfileDeletedResponse
import com.jarvis.data.net.RejectResponse
import com.jarvis.data.net.SampleDeletedResponse
import com.jarvis.data.net.VoiceApi
import com.jarvis.data.net.VoiceProfileResponse
import com.jarvis.data.net.VoiceSampleDto
import com.jarvis.data.voice.session.MicSource
import com.jarvis.data.voice.session.SpeechSynthesis
import com.jarvis.data.voice.session.SpeechSynthesisListener
import com.jarvis.data.voice.session.SpeechToText
import com.jarvis.data.voice.session.SpeechToTextListener
import com.jarvis.data.voice.session.VoiceSession
import com.jarvis.data.voice.session.VoiceTransport
import com.jarvis.data.voice.session.VoiceTransportListener
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Pins the production wiring no other test touches: does tapping the mic button in the
 * REAL [MainActivity] actually start a call through [AppContainer.voiceSessionFactory],
 * does the overlay take the screen, and does the hang-up button end it? VoiceCallViewModelTest
 * and VoiceCallOverlayTest both drive their layer directly — neither would notice if the
 * Activity stopped calling start() entirely (the "guard tested, production never calls
 * it" shape this repo has shipped four times).
 *
 * RECORD_AUDIO is granted by rule; the denied path cannot be driven from an instrumented
 * test (the system dialog is out of process), so it stays on the ViewModel test + HITL.
 */
@RunWith(AndroidJUnit4::class)
class VoiceCallWiringTest {

    @get:Rule
    val compose = createEmptyComposeRule()

    @get:Rule
    val micPermission: GrantPermissionRule = GrantPermissionRule.grant(
        android.Manifest.permission.RECORD_AUDIO,
        // Without this, startVoice() opens the system POST_NOTIFICATIONS dialog and
        // the whole class deadlocks behind it (the mic tap never reaches the session).
        android.Manifest.permission.POST_NOTIFICATIONS,
    )

    private class FakeAuthClient(private val token: String = "fake-token") : AuthClient {
        override fun currentToken(): String? = token
        override suspend fun signIn(activityContext: Context): Result<String> =
            Result.success(token)
        override suspend fun silentSignIn(): Result<String> = Result.success(token)
    }

    private class FakeAuthStateStore : AuthStateStore {
        override suspend fun hasSignedInBefore() = true
        override suspend fun markSignedIn() {}
        override suspend fun clearSignedIn() {}
    }

    /**
     * Approvals are wired into [AppContainer] like every other API, so this test has to
     * supply one. It answers "no approvals" rather than reaching the deployed backend: a
     * test suite must not talk to production at all.
     */
    private class FakeApprovalApi : ApprovalApi {
        override suspend fun list() = ApprovalsResponse(emptyList())
        override suspend fun get(id: String) = ApprovalDto(id = id, title = "t", status = "pending")
        override suspend fun approve(id: String) = ApprovalDecisionDto("approved", null, false)
        override suspend fun reject(id: String) = ApprovalDecisionDto("rejected", null, false)
    }

    private class FakeChatApi : JarvisApi {
        override suspend fun chat(req: ChatRequest) = ChatResponse("")
        override suspend fun history(sessionId: String) = HistoryResponse(emptyList())
    }

    private class FakeConversationsApi : ConversationsApi {
        override suspend fun list() = ConversationsResponse(emptyList())
        override suspend fun delete(sessionId: String) = ConversationDeletedResponse(sessionId)
    }

    private class FakeVoiceApi : VoiceApi {
        override suspend fun profile() = VoiceProfileResponse()
        override suspend fun patchLabel(id: String, req: LabelPatch) =
            VoiceSampleDto(id, "auto", label = req.label)
        override suspend fun patchNote(id: String, req: NotePatch) =
            VoiceSampleDto(id, "auto", note = req.note)
        override suspend fun deleteSample(id: String) = SampleDeletedResponse(id)
        override suspend fun confirm(id: String) = ConfirmResponse("s-new")
        override suspend fun reject(id: String) = RejectResponse(null)
        override suspend fun deleteProfile() = ProfileDeletedResponse(true)
    }

    private class FakeTransport : VoiceTransport {
        var connectCalls = 0
            private set
        @Volatile var listener: VoiceTransportListener? = null

        override fun connect(url: String, listener: VoiceTransportListener) {
            connectCalls++
            this.listener = listener
        }

        override fun sendText(text: String) = true
        override fun sendBinary(bytes: ByteArray) = true
        override fun close() {}
    }

    private class FakeMic : MicSource {
        override fun start(sampleRateHz: Int) {}
        override suspend fun readFrame(): ByteArray? = null
        override fun stop() {}
    }

    private class FakeStt : SpeechToText {
        override fun start(listener: SpeechToTextListener) {}
        override fun listen() {}
        override fun destroy() {}
    }

    private class FakeTts : SpeechSynthesis {
        override fun start(listener: SpeechSynthesisListener) {}
        override fun speak(text: String) {}
        override fun stop() {}
        override fun destroy() {}
    }

    @After
    fun tearDown() {
        val app = ApplicationProvider.getApplicationContext<JarvisApp>()
        app.container = AppContainer(app)
    }

    /** True while our mic foreground service is up — asserted by NAME so this test
     *  compiles (and stays RED) before the service class exists. */
    private fun micServiceForeground(): Boolean {
        val am = ApplicationProvider.getApplicationContext<JarvisApp>()
            .getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        @Suppress("DEPRECATION") // for the app's OWN services this still reports truthfully
        return am.getRunningServices(Int.MAX_VALUE).any {
            it.service.className == "com.jarvis.VoiceCallService" && it.foreground
        }
    }

    private fun waitUntil(timeoutMs: Long = 8_000, cond: () -> Boolean): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (cond()) return true
            Thread.sleep(200)
        }
        return cond()
    }

    /**
     * The field failure this slice exists for: the screen turning off (or another app
     * taking the foreground) killed a live call, because nothing held the process's
     * right to the microphone. A live call must hold a microphone-type foreground
     * service for exactly as long as it is live.
     */
    @Test
    fun liveCall_holdsMicForegroundService_untilHangUp() {
        val app = ApplicationProvider.getApplicationContext<JarvisApp>()
        val transport = FakeTransport()
        app.container = AppContainer(
            app,
            authManager = FakeAuthClient(),
            apis = ApiSet(FakeChatApi(), FakeVoiceApi(), FakeConversationsApi(), FakeApprovalApi()),
            authStateStore = FakeAuthStateStore(),
            voiceSessionFactory = { scope ->
                VoiceSession(
                    transport = transport,
                    mic = FakeMic(),
                    stt = FakeStt(),
                    tts = FakeTts(),
                    tokenProvider = { "fake-token" },
                    deviceHint = "android-test",
                    scope = scope,
                    voiceUrl = "wss://voice.example/ws/voice",
                )
            },
        )

        ActivityScenario.launch(MainActivity::class.java).use {
            compose.waitForIdle()
            org.junit.Assert.assertFalse(micServiceForeground())

            compose.onNodeWithTag("voice_call_button").performClick()
            compose.waitForIdle()
            transport.listener!!.onOpen()
            org.junit.Assert.assertTrue(
                "call is live but no mic foreground service is running",
                waitUntil { micServiceForeground() },
            )

            compose.onNodeWithTag("voice_end_button").performClick()
            compose.waitForIdle()
            org.junit.Assert.assertTrue(
                "call ended but the mic foreground service is still running",
                waitUntil { !micServiceForeground() },
            )
        }
    }

    /**
     * Regression pin (review #11c): rotating mid-call must not drop it. The ViewModel
     * (and the session in it) survives recreation, the new composition re-observes the
     * same state, and the idempotent service start must not tear anything down. A
     * second connect would mean the session got rebuilt — that's the failure this pins.
     */
    @Test
    fun rotationMidCall_keepsTheCallAndTheService_withoutReconnecting() {
        val app = ApplicationProvider.getApplicationContext<JarvisApp>()
        val transport = FakeTransport()
        app.container = AppContainer(
            app,
            authManager = FakeAuthClient(),
            apis = ApiSet(FakeChatApi(), FakeVoiceApi(), FakeConversationsApi(), FakeApprovalApi()),
            authStateStore = FakeAuthStateStore(),
            voiceSessionFactory = { scope ->
                VoiceSession(
                    transport = transport,
                    mic = FakeMic(),
                    stt = FakeStt(),
                    tts = FakeTts(),
                    tokenProvider = { "fake-token" },
                    deviceHint = "android-test",
                    scope = scope,
                    voiceUrl = "wss://voice.example/ws/voice",
                )
            },
        )

        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            compose.waitForIdle()
            compose.onNodeWithTag("voice_call_button").performClick()
            compose.waitForIdle()
            transport.listener!!.onOpen()
            compose.waitForIdle()
            compose.onNodeWithText("Dinliyorum").assertIsDisplayed()
            assertEquals(1, transport.connectCalls)

            scenario.recreate()
            compose.waitForIdle()

            compose.onNodeWithTag("voice_call_overlay").assertIsDisplayed()
            compose.onNodeWithText("Dinliyorum").assertIsDisplayed()
            assertEquals(1, transport.connectCalls) // same live socket, no redial
            org.junit.Assert.assertTrue(
                "mic foreground service must survive recreation",
                waitUntil { micServiceForeground() },
            )
        }
    }

    @Test
    fun micButton_startsRealCall_overlayShows_hangUpEndsIt() {
        val app = ApplicationProvider.getApplicationContext<JarvisApp>()
        val transport = FakeTransport()
        app.container = AppContainer(
            app,
            authManager = FakeAuthClient(),
            apis = ApiSet(FakeChatApi(), FakeVoiceApi(), FakeConversationsApi(), FakeApprovalApi()),
            authStateStore = FakeAuthStateStore(),
            voiceSessionFactory = { scope ->
                VoiceSession(
                    transport = transport,
                    mic = FakeMic(),
                    stt = FakeStt(),
                    tts = FakeTts(),
                    tokenProvider = { "fake-token" },
                    deviceHint = "android-test",
                    scope = scope,
                    voiceUrl = "wss://voice.example/ws/voice",
                )
            },
        )

        ActivityScenario.launch(MainActivity::class.java).use {
            compose.waitForIdle()
            compose.onNodeWithTag("voice_call_button").performClick()
            compose.waitForIdle()

            // The REAL factory seam was exercised and the overlay owns the screen.
            assertEquals(1, transport.connectCalls)
            compose.onNodeWithTag("voice_call_overlay").assertIsDisplayed()
            compose.onNodeWithText("Bağlanıyor…").assertIsDisplayed()

            // Server accepts: the session flips to live listening.
            transport.listener!!.onOpen()
            compose.waitForIdle()
            compose.onNodeWithText("Dinliyorum").assertIsDisplayed()

            // Hang up: the overlay leaves, the chat is back.
            compose.onNodeWithTag("voice_end_button").performClick()
            compose.waitForIdle()
            compose.onNodeWithTag("voice_call_overlay").assertDoesNotExist()
            compose.onNodeWithTag("chat_input").assertIsDisplayed()
        }
    }
}
