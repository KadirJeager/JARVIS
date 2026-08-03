package com.jarvis.data.approvals

/**
 * The card's view of an approval status.
 *
 * [UNKNOWN] is the fail-closed landing spot for anything this build does not recognise —
 * a status a newer backend introduces, or a row whose `status` never arrived. It is NOT
 * [PENDING]: guessing "pending" would put an "Onayla" button on something the server may
 * already have decided, which is the one mistake the whole approval centre exists to
 * prevent (spec §4.2). A card in [UNKNOWN] shows a badge and no decision buttons.
 */
enum class ApprovalStatus { PENDING, APPROVED, REJECTED, EXPIRED, FAILED, UNKNOWN }

/**
 * One red-zone action waiting on Kadir. The strict-domain side of the tolerant-wire
 * boundary: [id] and [title] are non-null here precisely because a row missing either
 * one is dropped at the boundary rather than drawn half-rendered.
 */
data class Approval(
    val id: String,
    val title: String,
    val detail: String,
    val status: ApprovalStatus,
    val toolName: String? = null,
    val toolArgs: Map<String, String> = emptyMap(),
    val createdAt: String? = null,
    val expiresAt: String? = null,
    val outcome: String? = null,
) {
    /** Only a pending approval can be decided; everything else is already history. */
    val decidable: Boolean get() = status == ApprovalStatus.PENDING
}

/**
 * The server's verdict on a decision — `{status, outcome, already}`, not a full approval.
 *
 * [already] true means this call did NOT make the decision: another touch (a push
 * notification and a queue sync racing, spec §4.2) or the clock (§4.1) got there first,
 * and [status] is what actually happened rather than what the button asked for.
 */
data class ApprovalDecision(
    val status: ApprovalStatus,
    val outcome: String?,
    val already: Boolean,
)
