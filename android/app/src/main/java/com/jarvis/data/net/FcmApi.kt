package com.jarvis.data.net

import kotlinx.serialization.Serializable
import retrofit2.http.Body
import retrofit2.http.POST

/**
 * Device-token registration for push (`POST /api/fcm/register`, brain/app/main.py).
 *
 * Separate from [JarvisApi] for the same reason [VoiceApi], [ConversationsApi] and
 * [ApprovalApi] are: several tests hand-implement those interfaces as fakes, and widening
 * one breaks every fake for reasons unrelated to what it tests.
 *
 * There is deliberately no `unregister`: the server has no such route, and the token
 * document is keyed by the token's own SHA-256 (`fcm.doc_id`), so re-posting the same
 * token is a no-op write rather than a duplicate row.
 */
interface FcmApi {
    @POST("api/fcm/register")
    suspend fun register(@Body req: FcmTokenRequest): FcmRegisterResponse
}

/** `FcmRegisterRequest` in main.py — one required field, no defaults, always written. */
@Serializable
data class FcmTokenRequest(val token: String)

/** `fcm.register_token` returns `{"ok": true}`; anything else arrives as an HTTP error. */
@Serializable
data class FcmRegisterResponse(val ok: Boolean = false)
