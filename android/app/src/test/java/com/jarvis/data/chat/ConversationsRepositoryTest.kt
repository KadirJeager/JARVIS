package com.jarvis.data.chat

import com.jarvis.data.net.ConversationDeletedResponse
import com.jarvis.data.net.ConversationDto
import com.jarvis.data.net.ConversationsApi
import com.jarvis.data.net.ConversationsResponse
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationsRepositoryTest {

    private class FakeApi(var rows: List<ConversationDto> = emptyList()) : ConversationsApi {
        var deleted: String? = null
        override suspend fun list() = ConversationsResponse(rows)
        override suspend fun delete(sessionId: String): ConversationDeletedResponse {
            deleted = sessionId
            return ConversationDeletedResponse(sessionId)
        }
    }

    /** In-memory stand-in for DataStore, with the same switch/mint semantics. */
    private class FakeSessionStore(private var current: String = "s-current") : SessionStore {
        var mintCount = 0
        override suspend fun sessionId() = current
        override suspend fun startNew(): String {
            mintCount++
            current = "new-$mintCount"
            return current
        }
        override suspend fun switchTo(sessionId: String) { current = sessionId }
    }

    @Test
    fun list_mapsTheBackendRows() = runBlocking {
        val api = FakeApi(listOf(ConversationDto("s1", "ilk konuşma", "t2", 4)))
        val rows = ConversationsRepository(api, FakeSessionStore()).list()

        assertEquals(1, rows.size)
        assertEquals("s1", rows[0].sessionId)
        assertEquals("ilk konuşma", rows[0].title)
        assertEquals(4, rows[0].messageCount)
    }

    /** A conversation opened with a blank first message must not render as an empty row. */
    @Test
    fun list_givesABlankTitleAReadablePlaceholder() = runBlocking {
        val api = FakeApi(listOf(ConversationDto("s1", "   ", "t", 1)))
        assertEquals("Başlıksız sohbet", ConversationsRepository(api, FakeSessionStore()).list()[0].title)
    }

    @Test
    fun startNew_mintsAFreshCurrentConversation() = runBlocking {
        val store = FakeSessionStore()
        val repo = ConversationsRepository(FakeApi(), store)
        val before = repo.current()

        val fresh = repo.startNew()

        assertNotEquals(before, fresh)
        assertEquals(fresh, repo.current())
    }

    @Test
    fun open_switchesTheCurrentConversation() = runBlocking {
        val store = FakeSessionStore()
        val repo = ConversationsRepository(FakeApi(), store)
        repo.open("s-old")
        assertEquals("s-old", repo.current())
    }

    /**
     * Deleting the conversation you are sitting in must not leave you inside a
     * conversation the server no longer has — every later send would 404 or silently
     * resurrect it.
     */
    @Test
    fun deletingTheCurrentConversation_movesYouToAFreshOne() = runBlocking {
        val api = FakeApi()
        val store = FakeSessionStore(current = "s-current")
        val repo = ConversationsRepository(api, store)

        val replaced = repo.delete("s-current")

        assertTrue(replaced)
        assertEquals("s-current", api.deleted)
        assertNotEquals("s-current", repo.current())
    }

    @Test
    fun deletingAnotherConversation_leavesYouWhereYouAre() = runBlocking {
        val api = FakeApi()
        val store = FakeSessionStore(current = "s-current")
        val repo = ConversationsRepository(api, store)

        val replaced = repo.delete("s-other")

        assertFalse(replaced)
        assertEquals("s-other", api.deleted)
        assertEquals("s-current", repo.current())
    }
}
