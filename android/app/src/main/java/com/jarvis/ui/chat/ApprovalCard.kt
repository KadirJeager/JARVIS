package com.jarvis.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.jarvis.data.approvals.Approval
import com.jarvis.data.approvals.ApprovalStatus
import com.jarvis.data.approvals.RejectReason
import com.jarvis.ui.theme.JarvisCyan
import com.jarvis.ui.theme.JarvisError
import com.jarvis.ui.theme.JarvisOnAccent
import com.jarvis.ui.theme.JarvisSurface
import com.jarvis.ui.theme.JarvisSurfaceHigh
import com.jarvis.ui.theme.JarvisTextMuted
import com.jarvis.ui.theme.JarvisTextPrimary
import com.jarvis.ui.theme.JarvisViolet

private fun statusColor(status: ApprovalStatus): Color = when (status) {
    ApprovalStatus.PENDING -> JarvisViolet
    ApprovalStatus.APPROVED -> JarvisCyan
    ApprovalStatus.FAILED -> JarvisError
    ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED, ApprovalStatus.UNKNOWN -> JarvisTextMuted
}

// Spec §5.6: approval fatigue is a security defect; extra prose that does not change a decision makes the card worse.

/**
 * A red-zone action waiting on Kadir, drawn inside the chat thread rather than on a
 * separate screen (spec §4.8 / §7).
 *
 * Stateless: [canDecide] and [deciding] both come from [ChatUiState], which derives them
 * from one place. That is what keeps the 10296e1 lesson honest — while a decision is in
 * flight the buttons are inert AND rendered inert (muted fill, muted label, a spinner and
 * "Karar gönderiliyor…" in place of the row). A button that is disabled but still looks
 * pressable reads as a broken app, and the user taps it again.
 *
 * There is no local "I tapped approve" state anywhere in here: the badge is whatever the
 * server last said, so an approve that came back `expired` shows "Süresi doldu".
 */
@Composable
fun ApprovalCard(
    approval: Approval,
    canDecide: Boolean,
    deciding: Boolean,
    onApprove: () -> Unit,
    onReject: () -> Unit,
    focused: Boolean = false,
    modifier: Modifier = Modifier,
    isRejecting: Boolean = false,
    rejectionReasons: List<RejectReason> = emptyList(),
    onConfirmReject: (String) -> Unit = {},
    onCancelReject: () -> Unit = {},
) {
    Column(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(18.dp))
            .background(JarvisSurface)
            // The card a notification pointed at is outlined, so tapping the push lands on
            // something visibly identifiable rather than "somewhere in the thread".
            .border(
                width = if (focused) 1.dp else 0.dp,
                color = if (focused) JarvisViolet else Color.Transparent,
                shape = RoundedCornerShape(18.dp),
            )
            .padding(horizontal = 16.dp, vertical = 14.dp)
            .testTag("approval_card_${approval.id}"),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        StatusBadge(approval.status)

        Text(
            text = approval.title,
            color = JarvisTextPrimary,
            style = MaterialTheme.typography.titleSmall,
        )
        approval.toolName?.takeIf { it.isNotBlank() }?.let { tool ->
            Text(
                text = tool,
                color = JarvisTextMuted,
                style = MaterialTheme.typography.bodySmall,
            )
        }

        approval.operand?.takeIf { it.isNotBlank() }?.let { op ->
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(
                    text = "Neye:",
                    color = JarvisTextMuted,
                    style = MaterialTheme.typography.labelSmall,
                )
                Text(
                    text = op,
                    color = JarvisTextPrimary,
                    style = MaterialTheme.typography.titleSmall,
                )
            }
        }

        if (approval.detail.isNotBlank()) {
            Text(
                text = approval.detail,
                color = JarvisTextMuted,
                style = MaterialTheme.typography.bodyMedium,
            )
        }

        approval.actor?.takeIf { it.isNotBlank() }?.let { act ->
            Text(
                text = "Kim istedi: $act",
                color = JarvisTextMuted,
                style = MaterialTheme.typography.bodySmall,
            )
        }

        val trustText = approvalTrustLabel(approval.trustLevel)
        val causeText = approvalCauseLabel(approval.cause)
        val trustAndCauseLine = listOfNotNull(trustText, causeText).joinToString(" · ")
        if (trustAndCauseLine.isNotBlank()) {
            Text(
                text = trustAndCauseLine,
                color = JarvisTextMuted,
                style = MaterialTheme.typography.bodySmall,
            )
        }

        val revLabel = approvalReversibilityLabel(approval.reversible)
        if (revLabel != null) {
            if (approval.reversible == false) {
                Text(
                    text = revLabel,
                    color = JarvisOnAccent,
                    style = MaterialTheme.typography.labelMedium,
                    modifier = Modifier
                        .clip(RoundedCornerShape(8.dp))
                        .background(JarvisError)
                        .padding(horizontal = 8.dp, vertical = 3.dp),
                )
            } else {
                Text(
                    text = revLabel,
                    color = JarvisTextMuted,
                    style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier
                        .border(width = 1.dp, color = JarvisTextMuted, shape = RoundedCornerShape(8.dp))
                        .padding(horizontal = 8.dp, vertical = 3.dp),
                )
            }
        }

        // The tool's own words after it ran (or the reason it did not). Spec §4.5: an
        // error is an observation, not something to hide from Kadir.
        approval.outcome?.takeIf { it.isNotBlank() }?.let {
            Text(
                text = it,
                color = if (approval.status == ApprovalStatus.FAILED) JarvisError else JarvisTextMuted,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.testTag("approval_outcome_${approval.id}"),
            )
        }

        when {
            deciding -> DecidingRow(approval.id)
            approval.decidable && isRejecting -> RejectStepTwoSection(
                approvalId = approval.id,
                canDecide = canDecide,
                rejectionReasons = rejectionReasons,
                onApprove = onApprove,
                onConfirmReject = onConfirmReject,
                onCancelReject = onCancelReject,
            )
            approval.decidable -> DecisionRow(
                approvalId = approval.id,
                enabled = canDecide,
                onApprove = onApprove,
                onReject = onReject,
            )
            // A decided card keeps its place in the thread with no buttons at all: the
            // decision is the server's and it is already made (spec §4.2).
            else -> Unit
        }
    }
}

@Composable
private fun StatusBadge(status: ApprovalStatus) {
    val color = statusColor(status)
    Text(
        text = approvalStatusLabel(status),
        color = color,
        style = MaterialTheme.typography.labelMedium,
        modifier = Modifier
            .clip(RoundedCornerShape(10.dp))
            .background(JarvisSurfaceHigh)
            .padding(horizontal = 10.dp, vertical = 4.dp)
            .testTag("approval_status_${status.name.lowercase()}"),
    )
}

@Composable
private fun DecisionRow(
    approvalId: String,
    enabled: Boolean,
    onApprove: () -> Unit,
    onReject: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Button(
            onClick = onApprove,
            enabled = enabled,
            shape = RoundedCornerShape(14.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = JarvisCyan,
                contentColor = JarvisOnAccent,
                // Explicit, not the Material default: the default disabled fill is derived
                // from the theme surface and reads almost identically to the live button
                // on this near-black palette.
                disabledContainerColor = JarvisSurfaceHigh,
                disabledContentColor = JarvisTextMuted,
            ),
            modifier = Modifier
                .testTag("approve_$approvalId")
                .semantics { contentDescription = "Onayla" },
        ) {
            Text("Onayla", style = MaterialTheme.typography.labelLarge)
        }
        Button(
            onClick = onReject,
            enabled = enabled,
            shape = RoundedCornerShape(14.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = JarvisSurfaceHigh,
                contentColor = JarvisTextPrimary,
                disabledContainerColor = JarvisSurfaceHigh,
                disabledContentColor = JarvisTextMuted,
            ),
            modifier = Modifier
                .testTag("reject_$approvalId")
                .semantics { contentDescription = "Reddet" },
        ) {
            Text("Reddet", style = MaterialTheme.typography.labelLarge)
        }
    }
}

@Composable
private fun RejectStepTwoSection(
    approvalId: String,
    canDecide: Boolean,
    rejectionReasons: List<RejectReason>,
    onApprove: () -> Unit,
    onConfirmReject: (String) -> Unit,
    onCancelReject: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .testTag("reject_reasons_$approvalId"),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Button(
            onClick = onApprove,
            enabled = canDecide,
            shape = RoundedCornerShape(14.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = JarvisCyan,
                contentColor = JarvisOnAccent,
                disabledContainerColor = JarvisSurfaceHigh,
                disabledContentColor = JarvisTextMuted,
            ),
            modifier = Modifier
                .testTag("approve_$approvalId")
                .semantics { contentDescription = "Onayla" },
        ) {
            Text("Onayla", style = MaterialTheme.typography.labelLarge)
        }

        rejectionReasons.forEachIndexed { index, reason ->
            Button(
                onClick = { onConfirmReject(reason.promptFill) },
                enabled = canDecide,
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = JarvisSurfaceHigh,
                    contentColor = JarvisTextPrimary,
                    disabledContainerColor = JarvisSurfaceHigh,
                    disabledContentColor = JarvisTextMuted,
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag("reason_chip_${approvalId}_$index"),
            ) {
                Text(reason.title, style = MaterialTheme.typography.bodyMedium)
            }
        }

        var reasonInput by remember { mutableStateOf("") }
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = reasonInput,
                onValueChange = { reasonInput = it },
                placeholder = { Text("Gerekçe yazın…", color = JarvisTextMuted) },
                singleLine = true,
                modifier = Modifier
                    .weight(1f)
                    .testTag("reason_input_$approvalId"),
                colors = TextFieldDefaults.colors(
                    focusedContainerColor = JarvisSurfaceHigh,
                    unfocusedContainerColor = JarvisSurfaceHigh,
                    focusedTextColor = JarvisTextPrimary,
                    unfocusedTextColor = JarvisTextPrimary,
                    cursorColor = JarvisCyan,
                ),
            )
            Button(
                onClick = {
                    val trimmed = reasonInput.trim()
                    if (trimmed.isNotEmpty()) {
                        onConfirmReject(trimmed)
                    }
                },
                enabled = canDecide && reasonInput.isNotBlank(),
                shape = RoundedCornerShape(14.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = JarvisCyan,
                    contentColor = JarvisOnAccent,
                    disabledContainerColor = JarvisSurfaceHigh,
                    disabledContentColor = JarvisTextMuted,
                ),
                modifier = Modifier.testTag("reason_send_$approvalId"),
            ) {
                Text("Gönder", style = MaterialTheme.typography.labelMedium)
            }
        }

        TextButton(
            onClick = onCancelReject,
            modifier = Modifier.testTag("reject_cancel_$approvalId"),
        ) {
            Text("Vazgeç", color = JarvisTextMuted, style = MaterialTheme.typography.labelMedium)
        }
    }
}

/**
 * What replaces the buttons while the server is deciding. Removing them outright — rather
 * than leaving two greyed rectangles — is the strongest possible "this is not tappable
 * right now", and the spinner says why.
 */
@Composable
private fun DecidingRow(approvalId: String) {
    Row(
        Modifier.fillMaxWidth().testTag("approval_deciding_$approvalId"),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        CircularProgressIndicator(color = JarvisCyan, strokeWidth = 2.dp, modifier = Modifier.size(16.dp))
        Spacer(Modifier.size(10.dp))
        Text(
            "Karar gönderiliyor…",
            color = JarvisTextMuted,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}
