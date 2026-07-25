package com.jarvis.data.chat

import com.jarvis.data.net.ChatRequest
import com.jarvis.data.net.ChatResponse
import com.jarvis.data.net.HistoryMessage
import com.jarvis.data.net.HistoryResponse
import com.jarvis.data.net.JarvisApi
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
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
