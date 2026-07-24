package com.jarvis.data.net

import kotlinx.serialization.Serializable

/**
 * Wire models for the jarvis-brain HTTP contract. Field names MUST match the JSON
 * exactly (backend is fixed): POST /api/chat {session_id, message} -> {reply};
 * GET /api/history?session_id= -> {messages:[{role, text, ts}]}. role ∈ {"user","model"}.
 */
@Serializable
data class ChatRequest(val session_id: String, val message: String)

@Serializable
data class ChatResponse(val reply: String)

@Serializable
data class HistoryMessage(val role: String, val text: String, val ts: String)

@Serializable
data class HistoryResponse(val messages: List<HistoryMessage>)
