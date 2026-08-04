package com.jarvis.wear.ui

import com.jarvis.wear.data.JarvisApiException
import com.jarvis.wear.data.UnauthorizedException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
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

@OptIn(ExperimentalCoroutinesApi::class)
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

    @Test fun `reportInputError writes straight into the same error surface`() = runTest {
        // Task 6: cihaz-yerel hatalar (ör. ActivityNotFoundException, ChatScreen tarafında
        // yakalanır) ağ hatalarıyla AYNI `error` alanını kullanır -- ikinci bir hata
        // yüzeyi yok.
        val v = vm(FakeApi())
        v.reportInputError("Bu cihazda konuşma tanıma yok")
        assertEquals("Bu cihazda konuşma tanıma yok", v.state.value.error)
        assertFalse(v.state.value.busy)
    }

    @Test fun `cancellation is rethrown, not reported as a generic error`() = runTest {
        // JarvisApiTest'teki aynı sözleşmenin ChatViewModel karşılığı (Task 5 review fix):
        // CancellationException genel `catch (e: Exception)`e düşüp "Beklenmeyen hata"ya
        // dönüşmemeli. Yakalanıp state güncellenmeden yeniden fırlatıldığı için `busy` hiç
        // `false`'a düşmez (ViewModel zaten kapanma sürecinde -- bkz. MainActivity'nin
        // DisposableEffect'i) ve `error` asla set edilmez.
        val api = FakeApi { throw CancellationException("iptal") }
        val v = vm(api)
        v.send("selam"); advanceUntilIdle()
        assertNull(v.state.value.error)
        assertTrue(v.state.value.busy)
    }
}
