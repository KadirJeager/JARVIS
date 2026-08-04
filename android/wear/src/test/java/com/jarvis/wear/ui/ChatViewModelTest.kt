package com.jarvis.wear.ui

import com.jarvis.wear.data.JarvisApiException
import com.jarvis.wear.data.UnauthorizedException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class ChatViewModelTest {
    private val dispatcher = StandardTestDispatcher()

    @Before fun setUp() { Dispatchers.setMain(dispatcher) }
    @After fun tearDown() { Dispatchers.resetMain() }

    private class FakeApi(var result: suspend () -> String = { "cevap" }) {
        val calls = mutableListOf<Pair<String, String>>()
        suspend fun chat(sessionId: String, message: String): String {
            calls += sessionId to message; return result()
        }
    }

    private fun vm(api: FakeApi) = ChatViewModel(chat = api::chat, sessionId = "wear-test")

    @Test fun `send goes through the api and lands in state`() = runTest {
        val api = FakeApi()
        val v = vm(api)
        v.send("durum raporu")
        advanceUntilIdle()
        assertEquals(listOf("wear-test" to "durum raporu"), api.calls)
        assertEquals("cevap", v.state.value.lastReply)
        assertEquals("durum raporu", v.state.value.lastQuestion)
        assertFalse(v.state.value.busy)
        assertNull(v.state.value.error)
    }

    @Test fun `blank input is ignored`() = runTest {
        val api = FakeApi()
        vm(api).send("   ")
        advanceUntilIdle()
        assertTrue(api.calls.isEmpty())
    }

    @Test fun `401 flips needsPairing`() = runTest {
        val api = FakeApi { throw UnauthorizedException() }
        val v = vm(api)
        v.send("selam"); advanceUntilIdle()
        assertTrue(v.state.value.needsPairing)
    }

    @Test fun `api error shows the Turkish message and clears busy`() = runTest {
        val api = FakeApi { throw JarvisApiException("Jarvis'e ulaşılamadı.") }
        val v = vm(api)
        v.send("selam"); advanceUntilIdle()
        assertEquals("Jarvis'e ulaşılamadı.", v.state.value.error)
        assertFalse(v.state.value.busy)
    }

    @Test fun `a second send while busy is dropped`() = runTest {
        val api = FakeApi()
        val v = vm(api)
        v.send("bir"); v.send("iki")     // ilki daha bitmedi (dispatcher bekliyor)
        advanceUntilIdle()
        assertEquals(1, api.calls.size)
    }
}
