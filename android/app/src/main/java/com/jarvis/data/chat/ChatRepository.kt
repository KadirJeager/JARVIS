package com.jarvis.data.chat

import com.jarvis.data.net.ChatRequest
import com.jarvis.data.net.JarvisApi

/**
 * Chat data access over [JarvisApi], keyed by the persistent [SessionStore] id. Maps the
 * wire models to [UiMessage]; the backend owns conversation context/rehydration.
 */
class ChatRepository(
    private val api: JarvisApi,
    private val session: SessionStore,
) {
    /**
     * `kind`/`meta` ride through untouched (spec §7); deciding what an unknown kind MEANS
     * belongs to [UiMessage.isApprovalCard], not here. `meta.approval_id` is lifted into a
     * named field so no screen has to know the wire key.
     */
    suspend fun loadHistory(): List<UiMessage> =
        api.history(session.sessionId()).messages.map {
            UiMessage(
                role = it.role,
                text = it.text,
                kind = it.kind,
                approvalId = it.meta?.get("approval_id")?.takeIf { id -> id.isNotBlank() },
            )
        }

    suspend fun send(text: String): UiMessage {
        val reply = api.chat(ChatRequest(session.sessionId(), text)).reply
        return UiMessage(role = "model", text = reply)
    }
}
