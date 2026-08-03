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

/**
 * One transcript row. [kind] and [meta] are OPTIONAL and that is the whole point:
 * `MessageStore._project` emits them only when the stored row carries them, so every row
 * written before Y3 — and every plain row written after it — arrives with exactly the
 * three keys it always had. Requiring either field would fail to decode the entire
 * history, not just the new rows.
 *
 * This is the tolerant-wire pin of spec §7. An unrecognised [kind] must read as plain
 * text (see [com.jarvis.data.chat.UiMessage.isApprovalCard]), so a backend that starts
 * sending a card type this build has never heard of degrades to a bubble instead of
 * crashing a screen.
 *
 * [meta] is string→string because its only producer is `main._approval_sink`, which
 * writes `{"approval_id": <id>}`. A future non-string meta value would fail to decode
 * here; if one is ever added server-side, this type widens with it.
 */
@Serializable
data class HistoryMessage(
    val role: String,
    val text: String,
    val ts: String,
    val kind: String? = null,
    val meta: Map<String, String>? = null,
)

@Serializable
data class HistoryResponse(val messages: List<HistoryMessage>)
