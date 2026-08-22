package com.jarvis.data.net

import kotlinx.serialization.Serializable
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path

/**
 * Approval centre endpoints (backend `brain/app/approvals.py` + the five `/api/approvals*`
 * routes in `main.py`, spec §9). Separate from [JarvisApi] for the same reason [VoiceApi]
 * and [ConversationsApi] are: several tests hand-implement those interfaces as fakes, and
 * widening one breaks every fake for reasons unrelated to what it tests.
 *
 * `POST /api/jobs/approvals-tick` is deliberately NOT here. It is the Cloud Scheduler's
 * endpoint (`require_scheduler`), it is a cleanup path rather than a security boundary
 * (spec §4.1), and giving the phone a method for it would only be a way to call something
 * it will always be refused.
 */
interface ApprovalApi {
    @GET("api/approvals")
    suspend fun list(): ApprovalsResponse

    /**
     * The current state of ONE approval. Spec §7: the badge is never baked into the
     * transcript — the card re-reads it here, because the approval document is the single
     * source of truth for "was this approved".
     */
    @GET("api/approvals/{id}")
    suspend fun get(@Path("id") id: String): ApprovalDto

    /** Decide AND execute. A second call returns `already=true` and runs nothing (§4.2). */
    @POST("api/approvals/{id}/approve")
    suspend fun approve(@Path("id") id: String): ApprovalDecisionDto

    /**
     * Typed rejection (§5.9 P2a): the server REFUSES a blank reason with 422, so the
     * reason is a required parameter here too -- no default, no deprecated bridge.
     * Both methods stay ABSTRACT on purpose: Retrofit registers only abstract methods
     * as endpoints; a Kotlin default body turns the call into that body's code instead
     * of an HTTP request.
     */
    @POST("api/approvals/{id}/reject")
    suspend fun reject(
        @Path("id") id: String,
        @Body request: RejectRequest,
    ): ApprovalDecisionDto

    /** Preset rejection reasons for the card's picker (§5.9 P2c). */
    @GET("api/approvals/reasons")
    suspend fun reasons(): ReasonsResponse
}

/**
 * Request body for POST /api/approvals/{id}/reject.
 */
@Serializable
data class RejectRequest(
    val reason: String,
)

/**
 * One item from GET /api/approvals/reasons.
 */
@Serializable
data class RejectReasonDto(
    val id: String? = null,
    val title: String? = null,
    val prompt_fill: String? = null,
)

@Serializable
data class ReasonsResponse(
    val reasons: List<RejectReasonDto> = emptyList(),
)

/**
 * One approval exactly as `approvals._project` writes it.
 *
 * Every field is nullable with a default because `_project` builds the response with
 * `d.get(field)`: an absent field arrives as JSON null, and `decided_at`/`decided_by`/
 * `outcome` are null for the entire pending life of an approval — which is most of them.
 *
 * `user_id` is deliberately absent even though the server sends it. This is a
 * single-user app ([[kapsam-tek-kullanici]]) and ownership is enforced server-side (a
 * foreign approval 404s, spec §4.4); not declaring the field is the cheapest guarantee
 * that no screen ever starts branching on someone else's identity.
 */
@Serializable
data class ApprovalDto(
    val id: String? = null,
    val kind: String? = null,
    val title: String? = null,
    val detail: String? = null,
    val tool_name: String? = null,
    // Always string→string on the wire: approvals.request stringifies and truncates every
    // value to 500 chars before writing (the same rule policy.write_audit uses).
    val tool_args: Map<String, String> = emptyMap(),
    val zone: String? = null,
    val session_id: String? = null,
    val status: String? = null,
    val created_at: String? = null,
    val expires_at: String? = null,
    val decided_at: String? = null,
    val decided_by: String? = null,
    val outcome: String? = null,
    val actor: String? = null,
    val trust_level: String? = null,
    val cause: String? = null,
    val operand: String? = null,
    val reversible: Boolean? = null,
    val decision_reason: String? = null,
)

@Serializable
data class ApprovalsResponse(val approvals: List<ApprovalDto> = emptyList())

/**
 * What `approve`/`reject` return: `approvals._result` → `{status, outcome, already}`.
 *
 * This is NOT a full approval document, and the difference matters: `already=true` means
 * this call did not make the decision (someone else did, or the clock did), and `status`
 * is then whatever the server settled on — possibly `expired`, never what the button said.
 */
@Serializable
data class ApprovalDecisionDto(
    val status: String? = null,
    val outcome: String? = null,
    val already: Boolean = false,
)
