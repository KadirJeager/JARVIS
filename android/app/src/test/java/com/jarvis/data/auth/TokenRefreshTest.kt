package com.jarvis.data.auth

import com.jarvis.data.net.AuthInterceptor
import com.jarvis.data.net.TokenAuthenticator
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import okhttp3.Request
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/**
 * Exercises the 401 silent-retry wiring against a fake refresher — no real Credential
 * Manager (that is an emulator/HITL concern, Task 7).
 */
class TokenRefreshTest {

    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.close()
    }

    private fun client(refresh: () -> String?): OkHttpClient =
        OkHttpClient.Builder()
            .addInterceptor(AuthInterceptor { "old" })
            .authenticator(TokenAuthenticator(refresh))
            .build()

    private fun call(client: OkHttpClient): Int =
        client.newCall(Request.Builder().url(server.url("/")).build()).execute()
            .use { it.code }

    @Test
    fun on401_refreshesOnce_andRetriesWithNewToken() {
        server.enqueue(MockResponse.Builder().code(401).build())
        server.enqueue(MockResponse.Builder().code(200).body("{}").build())
        var refreshCalls = 0

        val code = call(client { refreshCalls++; "fresh" })

        assertEquals(200, code)
        assertEquals("Bearer old", server.takeRequest().headers["Authorization"])
        assertEquals("Bearer fresh", server.takeRequest().headers["Authorization"])
        assertEquals(1, refreshCalls)
    }

    @Test
    fun onPersistent401_givesUpAfterSingleRetry_noInfiniteLoop() {
        server.enqueue(MockResponse.Builder().code(401).build())
        server.enqueue(MockResponse.Builder().code(401).build())
        var refreshCalls = 0

        val code = call(client { refreshCalls++; "fresh" })

        assertEquals(401, code)
        assertEquals(1, refreshCalls)
    }

    @Test
    fun whenRefreshReturnsNull_givesUpImmediately() {
        server.enqueue(MockResponse.Builder().code(401).build())
        var refreshCalls = 0

        val code = call(client { refreshCalls++; null })

        assertEquals(401, code)
        assertEquals(1, refreshCalls)
    }
}
