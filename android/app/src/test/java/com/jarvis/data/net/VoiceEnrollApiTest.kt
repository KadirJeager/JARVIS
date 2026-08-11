package com.jarvis.data.net

import kotlinx.coroutines.runBlocking
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.HttpException

/**
 * [VoiceApi.challenge] and [VoiceApi.enroll] against the real production chain
 * ([NetworkModule.createApis]'s OkHttp client, its `Json`, its Retrofit converter) and a
 * local server -- the same wire-level idiom as [VoicePatchWireTest] / [ApprovalWireTest].
 *
 * `/api/voice/enroll` has required a liveness grant since 6 Aug and no client could
 * mint one before this task, so these were, until now, dead endpoints. The 409/422 tests
 * exist because those two failures need DIFFERENT user actions: 409 means "no grant --
 * run the challenge first", 422 means "a submitted clip was flagged as spoofed --
 * retrying identically will not help, a new recording is required."
 */
class VoiceEnrollApiTest {

    private lateinit var server: MockWebServer
    private lateinit var api: VoiceApi

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val apis = NetworkModule.createApis(
            tokenProvider = { "test-token" },
            baseUrl = server.url("/").toString(),
        )
        api = apis.voice
    }

    @After
    fun tearDown() = server.close()

    private fun enqueue(code: Int, body: String) = server.enqueue(
        MockResponse.Builder()
            .code(code)
            .addHeader("Content-Type", "application/json")
            .body(body)
            .build(),
    )

    @Test
    fun challengePostsAndParsesCodeSpoken() = runBlocking {
        enqueue(200, """{"status":"challenge_created","code_spoken":true}""")

        val out = api.challenge()

        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/voice/challenge", request.url.encodedPath)
        assertEquals("challenge_created", out.status)
        assertTrue(out.code_spoken)
    }

    @Test
    fun enrollSendsClipsAndDeviceHint() = runBlocking {
        enqueue(200, """{"anchors":9}""")

        val out = api.enroll(EnrollRequest(listOf("AAEC", "AAED"), "android-Pixel 10 Pro"))

        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/voice/enroll", request.url.encodedPath)
        val body = checkNotNull(request.body) { "enroll gövdesiz gitti" }.utf8()
        assertTrue(body.contains("\"clips\""))
        assertTrue(body.contains("AAEC"))
        assertTrue(body.contains("AAED"))
        assertTrue(body.contains("device_hint"))
        assertTrue(body.contains("android-Pixel 10 Pro"))
        assertEquals(9, out.anchors)
    }

    /**
     * 422 is Task 1's "a clip was flagged as fake" -- the user must be told this
     * distinctly, not via a generic failure, because retrying identically will not help.
     */
    @Test
    fun enrollSurfacesSpoofRejectionDistinctly() = runBlocking {
        enqueue(422, """{"detail":"1. ses klibi sahte olarak işaretlendi, kayıt yapılmadı"}""")

        val err = runCatching { api.enroll(EnrollRequest(listOf("AAEC"), "x")) }.exceptionOrNull()

        assertTrue(err is HttpException && err.code() == 422)
    }

    /**
     * 409 = no liveness grant. The fix is "do the challenge first", a different user
     * action from 422.
     */
    @Test
    fun enrollSurfacesMissingGrantDistinctly() = runBlocking {
        enqueue(409, """{"detail":"..."}""")

        val err = runCatching { api.enroll(EnrollRequest(listOf("AAEC"), "x")) }.exceptionOrNull()

        assertTrue(err is HttpException && err.code() == 409)
    }
}
