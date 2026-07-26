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
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
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
) {
    // Surface, not a bare Column: Material3 Surface blocks touch propagation, so a tap
    // beside the hang-up button cannot fall through to the chat underneath and focus
    // the input bar mid-call (review Important #4, pinned by
    // overlay_swallowsTouches_underlyingUiNeverFires).
    Surface(
        color = JarvisBg,
        modifier = Modifier.fillMaxSize().testTag("voice_call_overlay"),
    ) {
        OverlayContent(state, onStop, onDismissError)
    }
}

@Composable
private fun OverlayContent(
    state: VoiceUiState,
    onStop: () -> Unit,
    onDismissError: () -> Unit,
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
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.padding(vertical = 10.dp),
            ) {
                if (state.phase == VoicePhase.CONNECTING) {
                    CircularProgressIndicator(
                        color = JarvisCyan,
                        modifier = Modifier.size(18.dp),
                    )
                    Spacer(Modifier.size(10.dp))
                }
                Text(
                    when (state.phase) {
                        VoicePhase.CONNECTING -> "Bağlanıyor…"
                        VoicePhase.LISTENING -> "Dinliyorum"
                        VoicePhase.SPEAKING -> "Konuşuyor…"
                        else -> ""
                    },
                    color = JarvisTextMuted,
                    style = MaterialTheme.typography.titleMedium,
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
