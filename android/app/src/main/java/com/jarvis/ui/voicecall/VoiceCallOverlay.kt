package com.jarvis.ui.voicecall

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.foundation.clickable
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.jarvis.data.voice.session.VoicePhase
import com.jarvis.data.voice.session.VoiceUiState
import com.jarvis.ui.theme.JarvisBg
import com.jarvis.ui.theme.JarvisCyan
import com.jarvis.ui.theme.JarvisError
import com.jarvis.ui.theme.JarvisGradient
import com.jarvis.ui.theme.JarvisOnAccent
import com.jarvis.ui.theme.JarvisSurface
import com.jarvis.ui.theme.JarvisTextMuted
import com.jarvis.ui.theme.JarvisTextPrimary

/** How long a dial may take before the "Bağlanıyor…" label appears at all
 *  (prod complaint: the flash was visible on every call, even sub-second dials). */
private const val CONNECTING_DEBOUNCE_MS = 1200L

/**
 * Full-screen live-call surface (Gemini live referenced): rolling transcript on top,
 * call status underneath, one red hang-up control. Stateless — phase transitions live in
 * [com.jarvis.data.voice.session.VoiceSession], lifecycle in [VoiceCallViewModel].
 */
@Composable
fun VoiceCallOverlay(
    state: VoiceUiState,
    onStop: () -> Unit,
    onDismissError: () -> Unit,
    onInterrupt: () -> Unit = {},
) {
    // Surface, not a bare Column: Material3 Surface blocks touch propagation, so a tap
    // beside the hang-up button cannot fall through to the chat underneath and focus
    // the input bar mid-call (review Important #4, pinned by
    // overlay_swallowsTouches_underlyingUiNeverFires).
    Surface(
        color = JarvisBg,
        modifier = Modifier.fillMaxSize().testTag("voice_call_overlay"),
    ) {
        OverlayContent(state, onStop, onDismissError, onInterrupt)
    }
}

@Composable
private fun OverlayContent(
    state: VoiceUiState,
    onStop: () -> Unit,
    onDismissError: () -> Unit,
    onInterrupt: () -> Unit,
) {
    Column(
        Modifier
            .fillMaxSize()
            .systemBarsPadding(),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        LazyColumn(
            modifier = Modifier.weight(1f).fillMaxWidth(),
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            items(state.transcript) { line ->
                val isUser = line.role == "user"
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
                ) {
                    Text(
                        text = line.text,
                        color = if (isUser) JarvisCyan else JarvisTextPrimary,
                        style = MaterialTheme.typography.bodyLarge,
                        modifier = Modifier
                            .widthIn(max = 320.dp)
                            .clip(RoundedCornerShape(14.dp))
                            .background(JarvisSurface)
                            .padding(horizontal = 14.dp, vertical = 9.dp),
                    )
                }
            }
            // The recognizer's interim hypothesis rides as a dimmed, italic user bubble
            // below the committed lines — visibly "still being heard", replaced by the
            // final user_text line when the utterance ends.
            state.partialText?.let { partial ->
                item {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                        Text(
                            text = partial,
                            color = JarvisTextMuted,
                            style = MaterialTheme.typography.bodyLarge,
                            fontStyle = androidx.compose.ui.text.font.FontStyle.Italic,
                            modifier = Modifier
                                .widthIn(max = 320.dp)
                                .clip(RoundedCornerShape(14.dp))
                                .background(JarvisSurface)
                                .padding(horizontal = 14.dp, vertical = 9.dp),
                        )
                    }
                }
            }
        }

        if (state.phase == VoicePhase.ERROR) {
            Text(
                state.errorMessage ?: "Bilinmeyen hata",
                color = JarvisError,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(horizontal = 24.dp, vertical = 8.dp),
            )
            TextButton(
                onClick = onDismissError,
                modifier = Modifier.testTag("voice_error_close").padding(bottom = 24.dp),
            ) {
                Text("Kapat", color = JarvisCyan)
            }
        } else {
            // Debounce the "Bağlanıyor…" label: a fast connect never flashes it --
            // the text only appears if the dial is STILL in progress after the
            // delay (prod complaint 2026-07-31: label visible on every call).
            val showConnecting by produceState(initialValue = false, key1 = state.phase) {
                if (state.phase == VoicePhase.CONNECTING) {
                    value = false
                    kotlinx.coroutines.delay(CONNECTING_DEBOUNCE_MS)
                    value = true
                } else {
                    value = false
                }
            }
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.padding(vertical = 10.dp),
            ) {
                if (showConnecting) {
                    CircularProgressIndicator(
                        color = JarvisCyan,
                        modifier = Modifier.size(18.dp),
                    )
                    Spacer(Modifier.size(10.dp))
                }
                // Tapping while Jarvis speaks is the ONLY way to cut him off now: voice
                // barge-in was removed because these devices' AEC cannot tell Kadir from
                // the loudspeaker (see VoiceSession's echo guard). A tap can't be
                // confused with an echo, so it works where the recognizer could not.
                Text(
                    when {
                        showConnecting -> "Bağlanıyor…"
                        state.phase == VoicePhase.CONNECTING -> ""  // debounce window: hide
                        state.phase == VoicePhase.LISTENING -> "Dinliyorum"
                        state.phase == VoicePhase.SPEAKING -> "Konuşuyor… • kesmek için dokun"
                        else -> ""
                    },
                    color = JarvisTextMuted,
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier
                        .testTag("voice_interrupt")
                        .semantics { contentDescription = "Jarvis'i kes" }
                        .clickable(enabled = state.phase == VoicePhase.SPEAKING) { onInterrupt() },
                )
            }
            Box(Modifier.padding(bottom = 32.dp)) {
                IconButton(
                    onClick = onStop,
                    modifier = Modifier
                        .size(64.dp)
                        .clip(RoundedCornerShape(32.dp))
                        .background(JarvisError)
                        .testTag("voice_end_button")
                        .semantics { contentDescription = "Aramayı bitir" },
                ) {
                    Text("✕", color = JarvisOnAccent, style = MaterialTheme.typography.titleLarge)
                }
            }
        }
    }
}
