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
    suspend fun loadHistory(): List<UiMessage> =
        api.history(session.sessionId()).messages.map { UiMessage(it.role, it.text) }

    suspend fun send(text: String): UiMessage {
        val reply = api.chat(ChatRequest(session.sessionId(), text)).reply
        return UiMessage(role = "model", text = reply)
    }
}
