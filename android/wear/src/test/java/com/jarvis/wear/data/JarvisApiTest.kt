package com.jarvis.wear.data

import kotlinx.coroutines.test.runTest
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * The chat contract at the level of the bytes production actually sends — same style as
 * the phone module's wire tests (e.g. `FcmWireTest`, `ApprovalWireTest`): drive the REAL
 * chain ([Net.buildApi]'s OkHttp client, its `Json`, its Retrofit converter) against a
 * local [MockWebServer] rather than a hand-written fake that never sees an HTTP request.
 */
class JarvisApiTest {

    private fun withServer(block: suspend (MockWebServer, JarvisApi) -> Unit) = runTest {
        MockWebServer().use { server ->
            server.start()
            val api = Net.buildApi(server.url("/").toString()) { "jdt_test" }
            block(server, api)
        }
    }

    private fun enqueue(code: Int, body: String) = MockResponse.Builder()
        .code(code)
        .addHeader("Content-Type", "application/json")
        .body(body)
        .build()

    @Test fun `chat posts session and message with the bearer and returns reply`() =
        withServer { server, api ->
            server.enqueue(enqueue(200, """{"reply":"Merhaba Kadir"}"""))
            val reply = api.chat("wear-1", "selam")
            assertEquals("Merhaba Kadir", reply)
            val req = server.takeRequest()
            assertEquals("/api/chat", req.url.encodedPath)
            assertEquals("Bearer jdt_test", req.headers["Authorization"])
            val body = checkNotNull(req.body) { "istek gövdesiz gitti" }.utf8()
            assertTrue(body.contains(""""session_id":"wear-1""""))
            assertTrue(body.contains(""""message":"selam""""))
        }

    @Test fun `401 raises UnauthorizedException`() =
        withServer { server, api ->
            server.enqueue(enqueue(401, """{"detail":"Geçersiz oturum"}"""))
            try {
                api.chat("wear-1", "selam")
                fail("UnauthorizedException bekleniyordu")
            } catch (expected: UnauthorizedException) { }
        }

    @Test fun `5xx surfaces a Turkish error message`() =
        withServer { server, api ->
            server.enqueue(enqueue(502, """{"detail":"altyapı"}"""))
            try {
                api.chat("wear-1", "selam")
                fail("JarvisApiException bekleniyordu")
            } catch (e: JarvisApiException) {
                assertTrue(e.userMessage.isNotBlank())
            }
        }
}
