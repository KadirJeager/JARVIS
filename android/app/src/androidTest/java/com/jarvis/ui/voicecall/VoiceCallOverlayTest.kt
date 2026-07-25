package com.jarvis.ui.voicecall

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.jarvis.data.chat.UiMessage
import com.jarvis.data.voice.protocol.TranscriptLine
import com.jarvis.data.voice.session.VoicePhase
import com.jarvis.data.voice.session.VoiceUiState
import com.jarvis.ui.chat.ChatScreen
import com.jarvis.ui.chat.ChatUiState
import com.jarvis.ui.theme.JarvisTheme
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class VoiceCallOverlayTest {

    @get:Rule
    val rule = createComposeRule()

    private fun show(state: VoiceUiState, onStop: () -> Unit = {}, onDismissError: () -> Unit = {}) {
        rule.setContent {
            JarvisTheme {
                VoiceCallOverlay(state = state, onStop = onStop, onDismissError = onDismissError)
            }
        }
    }

    @Test
    fun connecting_showsConnectingLabel() {
        show(VoiceUiState(phase = VoicePhase.CONNECTING))
        rule.onNodeWithText("Bağlanıyor…").assertIsDisplayed()
    }

    @Test
    fun listening_showsListeningLabel() {
        show(VoiceUiState(phase = VoicePhase.LISTENING))
        rule.onNodeWithText("Dinliyorum").assertIsDisplayed()
    }

    @Test
    fun speaking_showsSpeakingLabel() {
        show(VoiceUiState(phase = VoicePhase.SPEAKING))
        rule.onNodeWithText("Konuşuyor…").assertIsDisplayed()
    }

    @Test
    fun transcriptLines_areRendered() {
        show(
            VoiceUiState(
                phase = VoicePhase.LISTENING,
                transcript = listOf(
                    TranscriptLine("user", "merhaba jarvis"),
                    TranscriptLine("model", "buyur kadir"),
                ),
            ),
        )
        rule.onNodeWithText("merhaba jarvis").assertIsDisplayed()
        rule.onNodeWithText("buyur kadir").assertIsDisplayed()
    }

    @Test
    fun endButton_firesOnStop() {
        var stopped = false
        show(VoiceUiState(phase = VoicePhase.LISTENING), onStop = { stopped = true })
        rule.onNodeWithTag("voice_end_button").performClick()
        assertTrue(stopped)
    }

    @Test
    fun error_showsMessage_andCloseFiresDismiss() {
        var dismissed = false
        show(
            VoiceUiState(phase = VoicePhase.ERROR, errorMessage = "Bağlantı hatası: test"),
            onDismissError = { dismissed = true },
        )
        rule.onNodeWithText("Bağlantı hatası: test").assertIsDisplayed()
        rule.onNodeWithTag("voice_error_close").performClick()
        assertTrue(dismissed)
    }

    @Test
    fun chatScreen_micButton_firesOnStartVoice() {
        var started = false
        rule.setContent {
            JarvisTheme {
                ChatScreen(
                    state = ChatUiState(messages = listOf(UiMessage("user", "selam"))),
                    onInput = {},
                    onSend = {},
                    onRetry = {},
                    onStartVoice = { started = true },
                )
            }
        }
        rule.onNodeWithTag("voice_call_button").performClick()
        assertTrue(started)
    }
}
