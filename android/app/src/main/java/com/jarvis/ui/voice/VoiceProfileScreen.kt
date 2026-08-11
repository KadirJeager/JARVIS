package com.jarvis.ui.voice

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.jarvis.data.voice.Correction
import com.jarvis.data.voice.HistoryRow
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
import java.util.Locale

/**
 * Speaker-identity management screen (spec §9). Stateless — every decision is hoisted
 * to [VoiceProfileViewModel], and every RULE lives on the server.
 *
 * The content is gated: while [GatePhase.CHECKING] or [GatePhase.DENIED] nothing about
 * the profile renders at all. That gate is a real protection against a real threat (an
 * unlocked phone in someone else's hand) — and it is NEVER reported to the server, which
 * applies its own brakes independently (spec §7).
 *
 * Redesign (user feedback: "karışık, hata yapmaya müsait, çok veri görünüyor"): the
 * default view answers ONE question — does it recognise me? — with a plain-language
 * status card and a short recent-activity list. Everything else (the full sample
 * gallery, per-sample deletion, the 3b placeholders, the danger zone) lives behind a
 * single collapsed "Ses örneklerini yönet" section, collapsed by default. Both
 * destructive one-tap affordances (sample delete, history "ben değildim") are now
 * gated behind an explicit confirm step — a dialog for sample deletion, and a
 * tap-to-open correction dialog for history rows — instead of firing on the first tap.
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
    enrollState: EnrollState,
    onStartEnrollDevice: () -> Unit,
    onProceedToRecording: () -> Unit,
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
                enrollState = enrollState,
                onStartEnrollDevice = onStartEnrollDevice,
                onProceedToRecording = onProceedToRecording,
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

/** Only the most recent few history rows show by default; "daha fazla" reveals the rest. */
private const val RECENT_HISTORY_LIMIT = 5

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
    enrollState: EnrollState,
    onStartEnrollDevice: () -> Unit,
    onProceedToRecording: () -> Unit,
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

        // Both declared at this level (not inside a lazy item), so they are NOT reset by
        // scrolling — only leaving the screen (which re-arms the gate, spec's
        // onGateRequested) resets them, which is exactly why "collapsed by default"
        // holds on every fresh entry.
        var historyExpanded by rememberSaveable { mutableStateOf(false) }
        var manageExpanded by rememberSaveable { mutableStateOf(false) }

        LazyColumn(
            modifier = Modifier.fillMaxSize().testTag("voice_list"),
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item { state.summary?.let { StatusCard(it) } }

            if (profile.isEmpty) {
                item { EmptyState() }
            }

            if (profile.history.isNotEmpty()) {
                item { SectionTitle("Son söyleyişler (${profile.history.size})") }
                val visibleHistory = if (historyExpanded) {
                    profile.history
                } else {
                    profile.history.take(RECENT_HISTORY_LIMIT)
                }
                items(visibleHistory, key = { it.id }) { row ->
                    HistoryRowView(
                        row = row,
                        busy = state.mutatingId != null,
                        onConfirm = onConfirm,
                        onReject = onReject,
                    )
                }
                if (profile.history.size > RECENT_HISTORY_LIMIT) {
                    item {
                        TextButton(
                            onClick = { historyExpanded = !historyExpanded },
                            modifier = Modifier.testTag("voice_history_show_more"),
                        ) {
                            Text(
                                if (historyExpanded) "Daha az göster" else "Daha fazla göster",
                                color = JarvisCyan,
                            )
                        }
                    }
                }
            }

            item {
                ManageSectionHeader(
                    expanded = manageExpanded,
                    onToggle = { manageExpanded = !manageExpanded },
                )
            }

            if (manageExpanded) {
                if (state.summary != null && hasQualityBreakdown(state.summary)) {
                    item { QualityDetailCard(state.summary) }
                }
                if (!profile.isEmpty) {
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
                item {
                    EnrollDeviceSection(
                        enrollState = enrollState,
                        onStart = onStartEnrollDevice,
                        onProceedToRecording = onProceedToRecording,
                    )
                }
                item { Placeholders() }
                item { DangerZone(busy = state.mutatingId != null, onDeleteProfile = onDeleteProfile) }
            }
        }
    }
}

@Composable
private fun StatusCard(summary: QualitySummary) {
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
            statusSentence(summary),
            style = MaterialTheme.typography.bodyMedium,
            color = JarvisTextMuted,
            modifier = Modifier.testTag("voice_status_detail"),
        )
    }
}

/**
 * One plain-Turkish sentence for the default view (user feedback: "çok veri görünüyor").
 * [QualitySummary.detail] already carries the raw score/percentage breakdown (mean,
 * fail rate, per-label scores) computed server-side — that number-dense string still
 * exists and is shown in the "Ses örneklerini yönet" section (see [QualityDetailCard]),
 * just not in the default view.
 *
 * Matched on the headline text rather than adding a field to QualitySummary: this
 * screen is the one file in scope for this redesign, and the three headline strings
 * are already part of the contract other tests assert on verbatim, so they are a
 * stable switch key.
 */
private fun statusSentence(summary: QualitySummary): String = when (summary.headline) {
    "Tanınma güçlü" -> "Sesini büyük çoğunlukla doğru tanıyor."
    "Tanınma orta" -> "Sesini çoğu zaman tanıyor, ama zaman zaman karıştırıyor."
    "Tanınma zayıf" -> "Sesini sık sık tanıyamıyor; birkaç temiz kayıt eklemek yardımcı olabilir."
    // "Ses kimliği yok" / "Henüz ölçüm yok": summary.detail is already one plain
    // sentence with no score/percentage breakdown, so it is shown as-is.
    else -> summary.detail
}

/** True only for the three headlines whose [QualitySummary.detail] is the raw,
 *  number-dense breakdown — those are the ones worth surfacing again in the
 *  "manage" section; the other two headlines' detail is already plain and would
 *  just duplicate [statusSentence]. */
private fun hasQualityBreakdown(summary: QualitySummary): Boolean = when (summary.headline) {
    "Tanınma güçlü", "Tanınma orta", "Tanınma zayıf" -> true
    else -> false
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

/** The collapsed-by-default section holding everything that used to sit on the main
 *  view: the sample gallery, the 3b placeholders, and the danger zone (user feedback:
 *  "çok veri görünüyor" — most visits need none of this). */
@Composable
private fun ManageSectionHeader(expanded: Boolean, onToggle: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp))
            .background(JarvisSurface)
            .clickable(onClick = onToggle)
            .padding(14.dp)
            .testTag("voice_manage_toggle"),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            "Ses örneklerini yönet",
            style = MaterialTheme.typography.titleSmall,
            color = JarvisTextPrimary,
        )
        Spacer(Modifier.weight(1f))
        Text(if (expanded) "▾" else "▸", color = JarvisTextMuted)
    }
}

@Composable
private fun QualityDetailCard(summary: QualitySummary) {
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp))
            .background(JarvisSurface).padding(14.dp).testTag("voice_quality_detail"),
    ) {
        Text("Kalite ayrıntıları", style = MaterialTheme.typography.titleSmall, color = JarvisTextPrimary)
        Spacer(Modifier.size(6.dp))
        Text(summary.detail, style = MaterialTheme.typography.bodySmall, color = JarvisTextMuted)
    }
}

@Composable
private fun SampleRow(
    sample: VoiceSample,
    busy: Boolean,
    onSetLabel: (String, String?) -> Unit,
    onDelete: (String) -> Unit,
) {
    var menuOpen by remember { mutableStateOf(false) }
    // Sample deletion degrades recognition (it can remove an anchor) and used to fire
    // on a single tap in a dense scrolling list. This is a confirm STEP, not the typed
    // "SIL" gate — that gate is reserved for whole-profile deletion (spec: don't reuse
    // it here, it would be disproportionate for one sample).
    var deleteConfirmOpen by remember { mutableStateOf(false) }
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
                    // Muted while busy for the same reason as the correction buttons: an
                    // explicit colour overrides Material3's disabled alpha, so without this
                    // the control keeps its full-strength look while it is inert.
                    Text(
                        sample.label?.let { labelDisplayName(it) } ?: "Etiket ekle",
                        color = when {
                            busy || sample.label == null -> JarvisTextMuted
                            else -> JarvisViolet
                        },
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
                onClick = { deleteConfirmOpen = true },
                enabled = !busy,
                modifier = Modifier.testTag("voice_sample_delete_${sample.id}"),
            ) {
                Text("Sil", color = if (busy) JarvisTextMuted else JarvisError)
            }
        }
        sample.note?.takeIf { it.isNotBlank() }?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = JarvisTextMuted)
        }
    }

    if (deleteConfirmOpen) {
        AlertDialog(
            onDismissRequest = { deleteConfirmOpen = false },
            modifier = Modifier.testTag("voice_sample_delete_dialog_${sample.id}"),
            title = { Text("Bu örneği sil?") },
            text = {
                Text(
                    "Bu ses örneğini silmek anında uygulanır ve geri alınamaz.",
                    color = JarvisTextMuted,
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        deleteConfirmOpen = false
                        onDelete(sample.id)
                    },
                    modifier = Modifier.testTag("voice_sample_delete_confirm_${sample.id}"),
                ) { Text("Evet, sil", color = JarvisError) }
            },
            dismissButton = {
                TextButton(
                    onClick = { deleteConfirmOpen = false },
                    modifier = Modifier.testTag("voice_sample_delete_cancel_${sample.id}"),
                ) { Text("Vazgeç", color = JarvisTextMuted) }
            },
            containerColor = JarvisSurface,
            titleContentColor = JarvisTextPrimary,
            textContentColor = JarvisTextMuted,
        )
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

/**
 * "Bu cihazı tanıt" (Task 9): a new device's microphone is a different acoustic channel
 * than the gallery was built on, and the gallery cannot learn one on its own (the adapt
 * gate scores against immutable anchors -- see [EnrollDeviceViewModel][com.jarvis.ui.voice.EnrollDeviceViewModel]).
 * Enrollment needs a live voice call: the server speaks the liveness code only over an
 * open bridge, hence the supporting line below rather than a silent disabled button.
 */
@Composable
private fun EnrollDeviceSection(
    enrollState: EnrollState,
    onStart: () -> Unit,
    onProceedToRecording: () -> Unit,
) {
    val busy = enrollState !is EnrollState.Idle &&
        enrollState !is EnrollState.Done &&
        enrollState !is EnrollState.Failed
    Column(Modifier.fillMaxWidth().padding(top = 8.dp)) {
        Text(
            "Yeni telefonun mikrofonu farklı ses bırakır; tanıtmadan Jarvis seni bu " +
                "cihazda daha zor tanır.",
            style = MaterialTheme.typography.bodySmall,
            color = JarvisTextMuted,
        )
        enrollState.label()?.let {
            Spacer(Modifier.size(6.dp))
            Text(
                it,
                style = MaterialTheme.typography.bodyMedium,
                color = if (enrollState is EnrollState.Failed) JarvisError else JarvisCyan,
                modifier = Modifier.testTag("enroll_device_status"),
            )
        }
        Spacer(Modifier.size(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(
                onClick = onStart,
                enabled = !busy,
                modifier = Modifier.testTag("enroll_device"),
            ) { Text("Bu cihazı tanıt", color = if (busy) JarvisTextMuted else JarvisCyan) }
            if (enrollState is EnrollState.WaitingForSpokenCode) {
                TextButton(
                    onClick = onProceedToRecording,
                    modifier = Modifier.testTag("enroll_device_proceed"),
                ) { Text("Kodu söyledim, devam et", color = JarvisCyan) }
            }
        }
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

/** Locale.ROOT: a Turkish-locale device would otherwise render "0,74" and break parity
 *  with the scores logged server-side. */
private fun scoreText(score: Double): String = String.format(Locale.ROOT, "%.2f", score)

/**
 * ASCII on purpose. Turkish case folding is locale-dependent (I<->ı, İ<->i), so a
 * dotted "SİL" lowercased on a Turkish-locale device yields a combining sequence that
 * silently never matches: the user types exactly what is on screen and the button stays
 * dead. This word is compared with NO case transformation.
 */
const val DELETE_CONFIRM_WORD = "SIL"

/** Plain-language correction label shown under a history row. The exact wording
 *  ("bendim" / "ben değildim") intentionally matches the dialog's button copy so the
 *  row and the dialog it opens read as the same decision. */
private fun correctionLabel(correction: Correction): String? = when (correction) {
    Correction.CONFIRMED -> "bendim"
    Correction.REJECTED -> "ben değildim"
    Correction.NONE -> null
}

/**
 * A history row is now a single tap target: it shows the plain verdict, device and
 * date only (the numeric score moved into the detail dialog — spec: "the numeric
 * score belongs in the detail, not the list"). Tapping it opens [CorrectionDialog],
 * replacing the old always-live "Bendim"/"Ben değildim" pair — a single mis-tap in a
 * dense scrolling list used to delete the gallery sample that utterance became.
 */
@Composable
private fun HistoryRowView(
    row: HistoryRow,
    busy: Boolean,
    onConfirm: (String) -> Unit,
    onReject: (String) -> Unit,
) {
    var dialogOpen by remember { mutableStateOf(false) }

    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp))
            .background(JarvisSurface)
            .clickable { dialogOpen = true }
            .padding(14.dp).testTag("voice_history_${row.id}"),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                if (row.verified) "tanındı" else "tanınmadı",
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.SemiBold,
                color = if (row.verified) JarvisCyan else JarvisError,
            )
            Spacer(Modifier.size(10.dp))
            row.deviceHint?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = JarvisTextMuted)
            }
            Spacer(Modifier.weight(1f))
            Text(shortDate(row.ts), style = MaterialTheme.typography.bodySmall, color = JarvisTextMuted)
        }
        correctionLabel(row.correction)?.let {
            Spacer(Modifier.size(4.dp))
            Text(it, style = MaterialTheme.typography.labelMedium, color = JarvisTextMuted)
        }
    }

    if (dialogOpen) {
        CorrectionDialog(
            row = row,
            busy = busy,
            onDismiss = { dialogOpen = false },
            onConfirm = {
                dialogOpen = false
                onConfirm(row.id)
            },
            onReject = {
                dialogOpen = false
                onReject(row.id)
            },
        )
    }
}

/**
 * The "small sheet/dialog" that replaces the two always-live per-row buttons. Reversal
 * in BOTH directions stays legal (spec §6: changing your mind is legitimate) — only the
 * action matching the row's CURRENT verdict is disabled.
 *
 * The colour is computed from `enabled` rather than passed as a constant: a literal
 * `color = JarvisCyan` on the Text OVERRIDES the alpha Material3 applies to a disabled
 * button's content, so the already-ruled action would render at full brightness and
 * look tappable. Semantics said disabled and the tests agreed; only a screenshot showed
 * the user could not tell. A live-looking button that does nothing is the thing this
 * dialog was built to avoid.
 */
@Composable
private fun CorrectionDialog(
    row: HistoryRow,
    busy: Boolean,
    onDismiss: () -> Unit,
    onConfirm: () -> Unit,
    onReject: () -> Unit,
) {
    val canConfirm = !busy && row.correction != Correction.CONFIRMED
    val canReject = !busy && row.correction != Correction.REJECTED
    AlertDialog(
        onDismissRequest = onDismiss,
        modifier = Modifier.testTag("voice_history_dialog_${row.id}"),
        title = { Text("Bu söyleyiş sen miydin?") },
        text = {
            Text(
                "${shortDate(row.ts)} · ${row.deviceHint ?: "bilinmiyor"} · skor ${scoreText(row.score)}",
                color = JarvisTextMuted,
            )
        },
        confirmButton = {
            TextButton(
                onClick = onConfirm,
                enabled = canConfirm,
                modifier = Modifier.testTag("voice_confirm_${row.id}"),
            ) { Text("Bendim", color = if (canConfirm) JarvisCyan else JarvisTextMuted) }
        },
        dismissButton = {
            TextButton(
                onClick = onReject,
                enabled = canReject,
                modifier = Modifier.testTag("voice_reject_${row.id}"),
            ) { Text("Ben değildim", color = if (canReject) JarvisError else JarvisTextMuted) }
        },
        containerColor = JarvisSurface,
        titleContentColor = JarvisTextPrimary,
        textContentColor = JarvisTextMuted,
    )
}

@Composable
private fun DangerZone(busy: Boolean, onDeleteProfile: () -> Unit) {
    // rememberSaveable, not remember: this is a lazy item. Scroll it out of composition
    // (e.g. to look at a history row again) and back, and plain `remember` would reset
    // silently — a user who typed SIL, scrolled away, and scrolled back would find the
    // form collapsed with no indication anything was lost.
    var open by rememberSaveable { mutableStateOf(false) }
    var typed by rememberSaveable { mutableStateOf("") }

    Column(Modifier.fillMaxWidth().padding(top = 20.dp, bottom = 32.dp)) {
        Text("Tehlikeli bölge", style = MaterialTheme.typography.titleSmall, color = JarvisError)
        Spacer(Modifier.size(6.dp))
        Text(
            "Profili silmek ses örneklerini VE doğrulama geçmişini birlikte kaldırır. Geri alınamaz.",
            style = MaterialTheme.typography.bodySmall,
            color = JarvisTextMuted,
        )
        Spacer(Modifier.size(10.dp))
        if (!open) {
            TextButton(
                onClick = { open = true },
                enabled = !busy,
                modifier = Modifier.testTag("voice_danger_open"),
            ) { Text("Ses kimliğimi sil", color = JarvisError) }
        } else {
            Text(
                "Onaylamak için $DELETE_CONFIRM_WORD yaz:",
                style = MaterialTheme.typography.bodyMedium,
                color = JarvisTextPrimary,
            )
            Spacer(Modifier.size(6.dp))
            OutlinedTextField(
                value = typed,
                onValueChange = { typed = it },
                singleLine = true,
                modifier = Modifier.fillMaxWidth().testTag("voice_danger_input"),
                colors = TextFieldDefaults.colors(
                    focusedContainerColor = JarvisSurface,
                    unfocusedContainerColor = JarvisSurface,
                    focusedTextColor = JarvisTextPrimary,
                    unfocusedTextColor = JarvisTextPrimary,
                    cursorColor = JarvisCyan,
                ),
            )
            Spacer(Modifier.size(8.dp))
            Row {
                TextButton(onClick = { open = false; typed = "" }) {
                    Text("Vazgeç", color = JarvisTextMuted)
                }
                Spacer(Modifier.weight(1f))
                // Exact match, no lowercase()/uppercase() anywhere: see
                // DELETE_CONFIRM_WORD for why case folding is unsafe here.
                val canDelete = !busy && typed.trim() == DELETE_CONFIRM_WORD
                TextButton(
                    enabled = canDelete,
                    onClick = onDeleteProfile,
                    modifier = Modifier.testTag("voice_danger_confirm"),
                ) {
                    // Full-strength red on an inert irreversible action is the worst case
                    // of this whole class: it reads as armed before the user has typed the
                    // confirmation word at all.
                    Text("Kalıcı olarak sil", color = if (canDelete) JarvisError else JarvisTextMuted)
                }
            }
        }
    }
}
