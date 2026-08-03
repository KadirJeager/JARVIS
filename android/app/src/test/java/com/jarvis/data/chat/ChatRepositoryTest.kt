package com.jarvis.data.chat

import com.jarvis.data.net.ChatRequest
import com.jarvis.data.net.ChatResponse
import com.jarvis.data.net.HistoryMessage
import com.jarvis.data.net.HistoryResponse
import com.jarvis.data.net.JarvisApi
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatRepositoryTest {

    private class FakeSessionStore(private var id: String) : SessionStore {
        override suspend fun sessionId(): String = id
        override suspend fun startNew(): String = "new".also { id = it }
        override suspend fun switchTo(sessionId: String) { id = sessionId }
    }

    /** Hand-written test double for the Retrofit interface — no network. */
    private class FakeApi(
        private val cannedHistory: HistoryResponse,
        private val cannedReply: String,
    ) : JarvisApi {
        var lastChatRequest: ChatRequest? = null
        override suspend fun chat(req: ChatRequest): ChatResponse {
            lastChatRequest = req
            return ChatResponse(cannedReply)
        }
        override suspend fun history(sessionId: String): HistoryResponse = cannedHistory
    }

    @Test
    fun loadHistory_mapsRoleAndText() = runBlocking {
        val api = FakeApi(
            cannedHistory = HistoryResponse(
                listOf(
                    HistoryMessage("user", "selam", "t1"),
                    HistoryMessage("model", "merhaba", "t2"),
                ),
            ),
            cannedReply = "x",
        )
        val repo = ChatRepository(api, FakeSessionStore("s-1"))

        val msgs = repo.loadHistory()

        assertEquals(listOf(UiMessage("user", "selam"), UiMessage("model", "merhaba")), msgs)
    }

    /**
     * Spec §7: an `approval` row becomes a card, and the id it needs is in `meta`. Any
     * OTHER kind — including one shipped by a future backend — must fall back to the
     * plain-text bubble this build already draws, which is what makes the wire tolerant.
     */
    @Test
    fun loadHistory_carriesTheApprovalIdOfAnApprovalRow() = runBlocking {
        val api = FakeApi(
            cannedHistory = HistoryResponse(
                listOf(
                    HistoryMessage(
                        role = "model",
                        text = "🔔 Onay bekliyor — 'cancel_reminder' çalıştırılsın mı?",
                        ts = "t1",
                        kind = "approval",
                        meta = mapOf("approval_id" to "a1"),
                    ),
                ),
            ),
            cannedReply = "x",
        )

        val msg = ChatRepository(api, FakeSessionStore("s-1")).loadHistory().single()

        assertEquals("approval", msg.kind)
        assertEquals("a1", msg.approvalId)
        assertTrue(msg.isApprovalCard)
    }

    @Test
    fun loadHistory_readsAnUnknownKindAsPlainText() = runBlocking {
        val api = FakeApi(
            cannedHistory = HistoryResponse(
                listOf(HistoryMessage("model", "gelecekten bir satır", "t", kind = "hologram")),
            ),
            cannedReply = "x",
        )

        val msg = ChatRepository(api, FakeSessionStore("s-1")).loadHistory().single()

        assertFalse("bilinmeyen kind kart olarak çizilmemeli", msg.isApprovalCard)
        assertEquals("gelecekten bir satır", msg.text)
    }

    /** An approval row with no id in `meta` has no card to draw; it stays plain text. */
    @Test
    fun loadHistory_readsAnApprovalRowWithNoIdAsPlainText() = runBlocking {
        val api = FakeApi(
            cannedHistory = HistoryResponse(
                listOf(HistoryMessage("model", "kart", "t", kind = "approval", meta = emptyMap())),
            ),
            cannedReply = "x",
        )

        assertFalse(ChatRepository(api, FakeSessionStore("s-1")).loadHistory().single().isApprovalCard)
    }

    @Test
    fun send_returnsModelReply_andForwardsSessionIdAndText() = runBlocking {
        val api = FakeApi(HistoryResponse(emptyList()), cannedReply = "cevap")
        val repo = ChatRepository(api, FakeSessionStore("s-42"))

        val result = repo.send("selam")

        assertEquals(UiMessage("model", "cevap"), result)
        assertEquals("s-42", api.lastChatRequest?.session_id)
        assertEquals("selam", api.lastChatRequest?.message)
    }
}
