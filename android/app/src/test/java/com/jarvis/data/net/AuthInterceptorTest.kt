package com.jarvis.data.net

import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import okhttp3.Request
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

class AuthInterceptorTest {

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

    private fun clientWith(token: String?): OkHttpClient =
        OkHttpClient.Builder().addInterceptor(AuthInterceptor { token }).build()

    private fun hit(client: OkHttpClient) {
        client.newCall(Request.Builder().url(server.url("/")).build()).execute().close()
    }

    @Test
    fun addsBearerHeader_whenTokenPresent() {
        server.enqueue(MockResponse.Builder().code(200).body("{}").build())
        hit(clientWith("tok"))
        val recorded = server.takeRequest()
        assertEquals("Bearer tok", recorded.headers["Authorization"])
    }

    @Test
    fun omitsHeader_whenTokenNull() {
        server.enqueue(MockResponse.Builder().code(200).body("{}").build())
        hit(clientWith(null))
        val recorded = server.takeRequest()
        assertNull(recorded.headers["Authorization"])
    }
}
