package com.jarvis.data.chat

import com.jarvis.data.net.ConversationsApi

/** One row of the conversation list, as the UI wants it. */
data class Conversation(
    val sessionId: String,
    val title: String,
    val lastTs: String?,
    val messageCount: Int,
)

/**
 * The conversation index. Thin on purpose: the backend owns ordering (newest first),
 * the bound on how many are returned, and title derivation — re-deriving any of that
 * here would put one rule in two places.
 */
class ConversationsRepository(
    private val api: ConversationsApi,
    private val session: SessionStore,
) {
    suspend fun list(): List<Conversation> =
        api.list().conversations.map {
            Conversation(
                sessionId = it.session_id,
                // A conversation whose first message was blank/whitespace would have no
                // title; show something rather than an empty row.
                title = it.title.ifBlank { "Başlıksız sohbet" },
                lastTs = it.last_ts,
                messageCount = it.message_count,
            )
        }

    suspend fun startNew(): String = session.startNew()

    suspend fun open(sessionId: String) = session.switchTo(sessionId)

    suspend fun current(): String = session.sessionId()

    /**
     * Deletes server-side, then makes sure the user is not left sitting in a conversation
     * that no longer exists: if they deleted the one they were in, move to a fresh one.
     * Returns true when the current conversation was replaced.
     */
    suspend fun delete(sessionId: String): Boolean {
        api.delete(sessionId)
        return if (session.sessionId() == sessionId) {
            session.startNew()
            true
        } else {
            false
        }
    }
}
