package com.jarvis.ui.voicecall

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Button
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.click
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

    /**
     * Review Important #4: a backgrounded Column does not consume hits, so taps beside
     * the hang-up button fell through to the chat underneath (focusing the input, opening
     * the keyboard over a live call). The overlay must swallow every touch it doesn't
     * handle itself.
     */
    @Test
    fun overlay_swallowsTouches_underlyingUiNeverFires() {
        var underneathClicked = false
        val showOverlay = androidx.compose.runtime.mutableStateOf(true)
        rule.setContent {
            JarvisTheme {
                Box(Modifier.fillMaxSize().testTag("under_root")) {
                    Button(
                        onClick = { underneathClicked = true },
                        modifier = Modifier.fillMaxSize(),
                    ) {}
                    if (showOverlay.value) {
                        VoiceCallOverlay(
                            state = VoiceUiState(phase = VoicePhase.LISTENING),
                            onStop = {},
                            onDismissError = {},
                        )
                    }
                }
            }
        }
        // The vulnerable strip: bottom of the overlay BESIDE the hang-up button — plain
        // Column background there, no scrollable to consume the hit. Underneath in the
        // real app this is exactly where the chat InputBar sits.
        // The point is anchored to the end button's bounds, not an absolute fraction:
        // on Samsung gesture-nav devices the bottom corner-assistant swipe zones swallow
        // injected touches (SM-X526B eats x=10% below ~85% height), so an absolute point
        // is device-dependent and silently voids the control arm below.
        val endBounds = rule.onNodeWithTag("voice_end_button").fetchSemanticsNode().boundsInRoot
        val tapPoint = androidx.compose.ui.geometry.Offset(endBounds.left - 40f, endBounds.center.y)
        rule.onNodeWithTag("under_root").performTouchInput {
            click(tapPoint)
        }
        org.junit.Assert.assertFalse("tap fell through the overlay", underneathClicked)

        // Control: the SAME point with the overlay gone must reach the button —
        // otherwise the assertion above proves nothing about swallowing.
        rule.runOnUiThread { showOverlay.value = false }
        rule.waitForIdle()
        rule.onNodeWithTag("under_root").performTouchInput {
            click(tapPoint)
        }
        org.junit.Assert.assertTrue("control tap did not reach the button", underneathClicked)
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
