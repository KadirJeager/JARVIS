package com.jarvis.data.net

import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SerializationTest {

    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun chatRequest_serializesWithBackendFieldNames() {
        val encoded = json.encodeToString(ChatRequest(session_id = "s-1", message = "selam"))
        assertTrue("session_id alanı bekleniyor: $encoded", encoded.contains("\"session_id\""))
        assertTrue("message alanı bekleniyor: $encoded", encoded.contains("\"message\""))
    }

    @Test
    fun historyResponse_roundTripsRoleTextTs() {
        val raw = """{"messages":[{"role":"user","text":"selam","ts":"2026-01-01T00:00:00Z"}]}"""
        val decoded = json.decodeFromString<HistoryResponse>(raw)
        assertEquals(1, decoded.messages.size)
        assertEquals("user", decoded.messages[0].role)
        assertEquals("selam", decoded.messages[0].text)
        assertEquals("2026-01-01T00:00:00Z", decoded.messages[0].ts)
    }

    @Test
    fun chatResponse_decodesReply() {
        val decoded = json.decodeFromString<ChatResponse>("""{"reply":"merhaba"}""")
        assertEquals("merhaba", decoded.reply)
    }

    @Test
    fun historyResponse_ignoresUnknownFields() {
        // Backend may add fields; the client must not break.
        val raw = """{"messages":[{"role":"model","text":"ok","ts":"t","extra":1}],"cursor":"x"}"""
        val decoded = json.decodeFromString<HistoryResponse>(raw)
        assertEquals("model", decoded.messages[0].role)
    }
}
