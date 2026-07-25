package com.jarvis.ui.voice

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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.jarvis.data.voice.SampleSource
import com.jarvis.data.voice.VoiceSample
import com.jarvis.ui.theme.JarvisBg
import com.jarvis.ui.theme.JarvisCyan
import com.jarvis.ui.theme.JarvisError
import com.jarvis.ui.theme.JarvisSurface
import com.jarvis.ui.theme.JarvisSurfaceHigh
import com.jarvis.ui.theme.JarvisTextMuted
import com.jarvis.ui.theme.JarvisTextPrimary
import com.jarvis.ui.theme.JarvisViolet

/**
 * Speaker-identity management screen (spec §9). Stateless — every decision is hoisted
 * to [VoiceProfileViewModel], and every RULE lives on the server.
 *
 * The content is gated: while [GatePhase.CHECKING] or [GatePhase.DENIED] nothing about
 * the profile renders at all. That gate is a real protection against a real threat (an
 * unlocked phone in someone else's hand) — and it is NEVER reported to the server, which
 * applies its own brakes independently (spec §7).
 */
@Composable
fun VoiceProfileScreen(
    state: VoiceProfileUiState,
    onBack: () -> Unit,
    onRetryUnlock: () -> Unit,
    onRetryLoad: () -> Unit,
    onSetLabel: (String, String?) -> Unit,
    onDeleteSample: (String) -> Unit,
    onConfirm: (String) -> Unit,
    onReject: (String) -> Unit,
    onDeleteProfile: () -> Unit,
    onDismissError: () -> Unit,
) {
    Column(
        Modifier.fillMaxSize().background(JarvisBg).systemBarsPadding(),
    ) {
        TopBar(onBack)

        when (state.gate) {
            GatePhase.CHECKING -> Locked(message = "Kimliğin doğrulanıyor...")
            GatePhase.DENIED -> Denied(state.error, onRetryUnlock)
            GatePhase.UNLOCKED -> Unlocked(
                state = state,
                onRetryLoad = onRetryLoad,
                onSetLabel = onSetLabel,
                onDeleteSample = onDeleteSample,
                onConfirm = onConfirm,
                onReject = onReject,
                onDeleteProfile = onDeleteProfile,
                onDismissError = onDismissError,
            )
        }
    }
}

@Composable
private fun TopBar(onBack: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TextButton(onClick = onBack, modifier = Modifier.testTag("voice_back")) {
            Text("‹ Geri", color = JarvisCyan)
        }
        Spacer(Modifier.size(4.dp))
        Text("Ses kimliğim", style = MaterialTheme.typography.titleLarge, color = JarvisTextPrimary)
    }
}

@Composable
private fun Locked(message: String) {
    Box(Modifier.fillMaxSize().testTag("voice_locked"), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            CircularProgressIndicator(color = JarvisCyan, modifier = Modifier.size(28.dp))
            Spacer(Modifier.size(14.dp))
            Text(message, color = JarvisTextMuted, style = MaterialTheme.typography.bodyLarge)
        }
    }
}

@Composable
private fun Denied(error: String?, onRetryUnlock: () -> Unit) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                error ?: "Kilit açılamadı.",
                color = JarvisError,
                style = MaterialTheme.typography.bodyLarge,
            )
            Spacer(Modifier.size(12.dp))
            TextButton(onClick = onRetryUnlock, modifier = Modifier.testTag("voice_retry_unlock")) {
                Text("Tekrar dene", color = JarvisCyan)
            }
        }
    }
}

@Composable
private fun Unlocked(
    state: VoiceProfileUiState,
    onRetryLoad: () -> Unit,
    onSetLabel: (String, String?) -> Unit,
    onDeleteSample: (String) -> Unit,
    onConfirm: (String) -> Unit,
    onReject: (String) -> Unit,
    onDeleteProfile: () -> Unit,
    onDismissError: () -> Unit,
) {
    val profile = state.profile
    Column(Modifier.fillMaxSize()) {
        state.error?.let { ErrorBanner(it, onDismissError) }

        if (profile == null) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                if (state.loading) {
                    CircularProgressIndicator(color = JarvisCyan, modifier = Modifier.size(28.dp))
                } else {
                    TextButton(onClick = onRetryLoad) { Text("Tekrar dene", color = JarvisCyan) }
                }
            }
            return@Column
        }

        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item { state.summary?.let { StatusCard(it, state.profile.counts.total) } }

            if (profile.isEmpty) {
                item { EmptyState() }
            } else {
                item { SectionTitle("Ses örneklerim (${profile.counts.total})") }
                items(profile.samples, key = { it.id }) { sample ->
                    SampleRow(
                        sample = sample,
                        busy = state.mutatingId != null,
                        onSetLabel = onSetLabel,
                        onDelete = onDeleteSample,
                    )
                }
            }

            item { Placeholders() }
            // Task 8 adds: history section + danger zone.
        }
    }
}

@Composable
private fun StatusCard(summary: QualitySummary, sampleCount: Int) {
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp))
            .background(JarvisSurface).padding(16.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                summary.headline,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                color = JarvisTextPrimary,
                modifier = Modifier.testTag("voice_status_headline"),
            )
            Spacer(Modifier.size(8.dp))
            Text(trendGlyph(summary.trend), color = trendColor(summary.trend))
        }
        Spacer(Modifier.size(6.dp))
        Text(
            summary.detail,
            style = MaterialTheme.typography.bodyMedium,
            color = JarvisTextMuted,
            modifier = Modifier.testTag("voice_status_detail"),
        )
    }
}

private fun trendGlyph(t: TrendDirection) = when (t) {
    TrendDirection.UP -> "↑"
    TrendDirection.DOWN -> "↓"
    TrendDirection.FLAT -> "→"
    TrendDirection.UNKNOWN -> ""
}

@Composable
private fun trendColor(t: TrendDirection) = when (t) {
    TrendDirection.UP -> JarvisCyan
    TrendDirection.DOWN -> JarvisError
    else -> JarvisTextMuted
}

@Composable
private fun SectionTitle(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.titleSmall,
        color = JarvisTextPrimary,
        modifier = Modifier.padding(top = 8.dp),
    )
}

@Composable
private fun SampleRow(
    sample: VoiceSample,
    busy: Boolean,
    onSetLabel: (String, String?) -> Unit,
    onDelete: (String) -> Unit,
) {
    var menuOpen by remember { mutableStateOf(false) }
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp))
            .background(JarvisSurface).padding(14.dp).testTag("voice_sample_${sample.id}"),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Badge(sourceBadge(sample.source), sample.source)
            Spacer(Modifier.size(8.dp))
            Text(
                sample.deviceHint ?: "bilinmiyor",
                style = MaterialTheme.typography.bodyMedium,
                color = JarvisTextPrimary,
            )
            Spacer(Modifier.weight(1f))
            Text(
                shortDate(sample.ts),
                style = MaterialTheme.typography.bodySmall,
                color = JarvisTextMuted,
            )
        }
        Spacer(Modifier.size(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box {
                TextButton(
                    onClick = { menuOpen = true },
                    enabled = !busy,
                    modifier = Modifier.testTag("voice_sample_label_${sample.id}"),
                ) {
                    Text(
                        sample.label?.let { labelDisplayName(it) } ?: "Etiket ekle",
                        color = if (sample.label == null) JarvisTextMuted else JarvisViolet,
                    )
                }
                DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                    LABEL_VALUES.forEach { value ->
                        DropdownMenuItem(
                            text = { Text(labelDisplayName(value)) },
                            modifier = Modifier.testTag("voice_label_option_$value"),
                            onClick = {
                                menuOpen = false
                                // The ASCII wire value goes out, never the display name.
                                onSetLabel(sample.id, value)
                            },
                        )
                    }
                    if (sample.label != null) {
                        DropdownMenuItem(
                            text = { Text("Etiketi kaldır") },
                            modifier = Modifier.testTag("voice_label_option_none"),
                            onClick = {
                                menuOpen = false
                                onSetLabel(sample.id, null)
                            },
                        )
                    }
                }
            }
            Spacer(Modifier.weight(1f))
            TextButton(
                onClick = { onDelete(sample.id) },
                enabled = !busy,
                modifier = Modifier.testTag("voice_sample_delete_${sample.id}"),
            ) {
                Text("Sil", color = JarvisError)
            }
        }
        sample.note?.takeIf { it.isNotBlank() }?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = JarvisTextMuted)
        }
    }
}

@Composable
private fun Badge(text: String, source: SampleSource) {
    val tint = when (source) {
        SampleSource.ENROLL -> JarvisCyan
        SampleSource.MANUAL -> JarvisViolet
        SampleSource.AUTO -> JarvisTextMuted
    }
    Text(
        text,
        style = MaterialTheme.typography.labelMedium,
        color = tint,
        modifier = Modifier.clip(RoundedCornerShape(8.dp)).background(JarvisSurfaceHigh)
            .padding(horizontal = 8.dp, vertical = 3.dp),
    )
}

@Composable
private fun EmptyState() {
    Box(
        Modifier.fillMaxWidth().padding(vertical = 32.dp).testTag("voice_empty"),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            "Kayıtlı ses örneğin yok.",
            color = JarvisTextMuted,
            style = MaterialTheme.typography.bodyLarge,
        )
    }
}

/**
 * Spec §9: the screen is built expecting 3b's actions from the start, so adding them
 * later is filling in an onClick rather than re-laying out the page. Disabled on
 * purpose — an enabled button that does nothing is worse than a visibly pending one.
 */
@Composable
private fun Placeholders() {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        TextButton(
            onClick = {},
            enabled = false,
            modifier = Modifier.testTag("voice_placeholder_record"),
        ) { Text("Ses kaydet (yakında)") }
        TextButton(
            onClick = {},
            enabled = false,
            modifier = Modifier.testTag("voice_placeholder_retrain"),
        ) { Text("Yeniden eğit (yakında)") }
    }
}

@Composable
private fun ErrorBanner(message: String, onDismiss: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp).testTag("voice_error"),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            message,
            color = JarvisError,
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.weight(1f),
        )
        TextButton(onClick = onDismiss) { Text("Tamam", color = JarvisCyan) }
    }
}

/** ISO-8601 timestamps arrive from the server; only the date part is useful here, and
 *  a null ts is normal for pre-3d anchors. Substring beats a date parser for this. */
internal fun shortDate(ts: String?): String = ts?.take(10) ?: "—"
