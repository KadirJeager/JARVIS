package com.jarvis.data.net

import kotlinx.serialization.Serializable
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.Path

/**
 * Conversation index (backend `brain/app/conversations.py`). Separate from [JarvisApi]
 * for the same reason [VoiceApi] is: several tests hand-implement `JarvisApi` as a fake,
 * and widening that interface breaks all of them for reasons unrelated to what they test.
 *
 * `messages` stays the append-only transcript; this is the index over it that makes
 * "list my conversations" possible without scanning every message ever sent.
 */
@Serializable
data class ConversationDto(
    val session_id: String,
    val title: String = "",
    val last_ts: String? = null,
    val message_count: Int = 0,
)

@Serializable
data class ConversationsResponse(
    val conversations: List<ConversationDto> = emptyList(),
)

@Serializable
data class ConversationDeletedResponse(val deleted: String)

interface ConversationsApi {
    @GET("api/conversations")
    suspend fun list(): ConversationsResponse

    @DELETE("api/conversations/{id}")
    suspend fun delete(@Path("id") sessionId: String): ConversationDeletedResponse
}
