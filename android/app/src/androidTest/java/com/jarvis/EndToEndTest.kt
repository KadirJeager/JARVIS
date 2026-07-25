package com.jarvis

import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import com.jarvis.data.chat.ChatRepository
import com.jarvis.data.chat.SessionStore
import com.jarvis.data.net.ChatRequest
import com.jarvis.data.net.ChatResponse
import com.jarvis.data.net.HistoryMessage
import com.jarvis.data.net.HistoryResponse
import com.jarvis.data.net.JarvisApi
import com.jarvis.ui.Nav
import com.jarvis.ui.Route
import com.jarvis.ui.VoiceActions
import com.jarvis.ui.chat.ChatViewModel
import com.jarvis.ui.theme.JarvisTheme
import com.jarvis.ui.voice.VoiceProfileUiState
import org.junit.Rule
import org.junit.Test

/**
 * End-to-end of the wiring with a fake backend (no Credential Manager / no network — those
 * are the Task 7 Step 5 HITL). Drives the real Nav + ChatViewModel + repository + screens:
 * sign-in -> chat, history renders, typing + send appends the reply.
 */
class EndToEndTest {

    @get:Rule
    val rule = createComposeRule()

    private class FakeSessionStore : SessionStore {
        override suspend fun sessionId(): String = "s-e2e"
    }

    private class FakeApi : JarvisApi {
        override suspend fun chat(req: ChatRequest): ChatResponse = ChatResponse("yeni cevap")
        override suspend fun history(sessionId: String): HistoryResponse =
            HistoryResponse(listOf(HistoryMessage("model", "geçmiş mesaj", "t1")))
    }

    @Test
    fun signIn_landsOnChat_rendersHistory_andSendAppendsReply() {
        val vm = ChatViewModel(ChatRepository(FakeApi(), FakeSessionStore()))
        rule.setContent {
            JarvisTheme {
                val state by vm.state.collectAsState()
                Nav(
                    state = state,
                    // This test never opens the voice screen; it only needs Nav's
                    // wiring to compile after Task 9 added the route parameters.
                    route = Route.CHAT,
                    voiceState = VoiceProfileUiState(),
                    voiceActions = VoiceActions(
                        onRetryUnlock = {}, onRetryLoad = {}, onSetLabel = { _, _ -> },
                        onDeleteSample = {}, onConfirm = {}, onReject = {},
                        onDeleteProfile = {}, onDismissError = {},
                    ),
                    onSignIn = { vm.onSignedIn() },
                    onInput = vm::onInputChange,
                    onSend = vm::send,
                    onRetry = vm::refreshHistory,
                    onOpenVoiceProfile = {},
                    onBack = {},
                )
            }
        }

        // Starts on the BOOT SPLASH, not the sign-in screen: silent re-auth has not
        // resolved yet, and offering "Google ile giriş" here is what made every warm
        // start look like the app had forgotten the session.
        rule.onNodeWithTag("boot_splash").assertIsDisplayed()
        rule.onNodeWithTag("signin_button").assertDoesNotExist()

        // Silent re-auth found nothing -> now the sign-in screen is correct.
        vm.onSilentSignInFailed()
        rule.waitForIdle()
        rule.onNodeWithTag("signin_button").assertIsDisplayed()

        // Sign in -> chat with loaded history.
        rule.onNodeWithTag("signin_button").performClick()
        rule.waitForIdle()
        rule.onNodeWithText("geçmiş mesaj").assertIsDisplayed()

        // Type and send -> user bubble + model reply.
        rule.onNodeWithTag("chat_input").performTextInput("selam")
        rule.onNodeWithTag("send_button").performClick()
        rule.waitForIdle()
        rule.onNodeWithText("selam").assertIsDisplayed()
        rule.onNodeWithText("yeni cevap").assertIsDisplayed()
    }
}
