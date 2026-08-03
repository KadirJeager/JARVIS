package com.jarvis.data.chat

/** The one `kind` this build renders as something other than a bubble (spec §7). */
const val KIND_APPROVAL = "approval"

/**
 * A single chat bubble. [role] is "user" or "model" (matching the backend).
 *
 * [kind] and [approvalId] carry the optional transcript fields of spec §7. Both default
 * to null so every existing construction site — and every row the backend wrote before
 * Y3 — keeps meaning exactly what it meant.
 */
data class UiMessage(
    val role: String,
    val text: String,
    val kind: String? = null,
    val approvalId: String? = null,
) {
    /**
     * Draw this row as an approval card rather than a bubble.
     *
     * The tolerant-wire boundary lives in this one expression: an unknown [kind] — a card
     * type shipped by a newer backend — is false here and falls through to the plain
     * bubble the app already draws. So does an `approval` row whose meta carried no id:
     * without an id there is no server state to read and nothing to approve, and a card
     * with dead buttons is worse than the text the server already wrote into the row.
     */
    val isApprovalCard: Boolean get() = kind == KIND_APPROVAL && approvalId != null
}
