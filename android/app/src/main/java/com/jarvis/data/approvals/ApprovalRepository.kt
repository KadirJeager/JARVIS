package com.jarvis.data.approvals

import com.jarvis.data.net.ApprovalApi
import com.jarvis.data.net.ApprovalDecisionDto
import com.jarvis.data.net.ApprovalDto

/**
 * Data access for the approval centre (spec §9/§10).
 *
 * Every call is a plain pass-through: ownership, the timeout-is-a-rejection rule, the
 * claim document that makes a decision idempotent and the executor allowlist all live on
 * the server (spec §4, §6). Re-deriving any of them here would put one rule in two places
 * and the two would drift — the same reasoning as [com.jarvis.data.voice.VoiceProfileRepository].
 *
 * What this layer DOES own is the tolerant-wire / strict-domain boundary, and it applies
 * two opposite-looking rules on purpose:
 *
 * - **A row that cannot be rendered is dropped, and only that row.** One malformed
 *   approval must not blank the whole queue; a screen whose job is to show what is there
 *   should not go dark because of a single bad document.
 * - **An unrecognised status is NOT dropped and NOT read as pending** — it degrades to
 *   [ApprovalStatus.UNKNOWN], which draws a badge and no decision buttons. Fail-closed:
 *   dropping it would hide a red action from Kadir, and guessing "pending" would offer to
 *   approve something the server may already have decided.
 */
class ApprovalRepository(private val api: ApprovalApi) {

    /**
     * The queue: everything still awaiting a decision, newest first, expired rows already
     * filtered out server-side. This is what makes a lost push harmless — the app syncs
     * from here on every launch instead of trusting the notification to arrive (spec §4.3).
     */
    suspend fun pending(): List<Approval> = api.list().approvals.mapNotNull { it.toDomainOrNull() }

    /** The CURRENT state of one approval; null when it cannot be rendered. Spec §7. */
    suspend fun get(id: String): Approval? = api.get(id).toDomainOrNull()

    suspend fun approve(id: String): ApprovalDecision = api.approve(id).toDomain()

    suspend fun reject(id: String): ApprovalDecision = api.reject(id).toDomain()
}

/** Wire status strings are the ones `approvals.STATUS_*` writes; anything else is UNKNOWN. */
private fun String?.toApprovalStatus(): ApprovalStatus = when (this) {
    "pending" -> ApprovalStatus.PENDING
    "approved" -> ApprovalStatus.APPROVED
    "rejected" -> ApprovalStatus.REJECTED
    "expired" -> ApprovalStatus.EXPIRED
    "failed" -> ApprovalStatus.FAILED
    else -> ApprovalStatus.UNKNOWN
}

/**
 * Null when the row cannot be drawn: no id means there is nothing to approve, no title
 * means there is nothing to show. Everything else degrades rather than disappears.
 */
private fun ApprovalDto.toDomainOrNull(): Approval? {
    val approvalId = id?.takeIf { it.isNotBlank() } ?: return null
    val cardTitle = title?.takeIf { it.isNotBlank() } ?: return null
    return Approval(
        id = approvalId,
        title = cardTitle,
        detail = detail.orEmpty(),
        status = status.toApprovalStatus(),
        toolName = tool_name,
        toolArgs = tool_args,
        createdAt = created_at,
        expiresAt = expires_at,
        outcome = outcome,
    )
}

private fun ApprovalDecisionDto.toDomain() = ApprovalDecision(
    status = status.toApprovalStatus(),
    outcome = outcome,
    already = already,
)
