package com.jarvis.data.net

import com.jarvis.data.push.FcmRegistration
import com.jarvis.data.push.FcmTokenRegistrar
import kotlinx.coroutines.runBlocking
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Device-token registration at the level of the bytes production actually sends.
 *
 * This is the test the previous slice did not have, and its absence is measurable: the
 * backend has been pushing since Faz Y2.4, yet `fcm_tokens` held ZERO documents in
 * production, because no code on the phone ever called `POST /api/fcm/register`. Every
 * "live FCM test" therefore exercised the chat fallback
 * (`{'reason':'no_fcm_tokens','fallback':'chat_reminders'}`) and nobody noticed.
 *
 * A hand-written fake [FcmApi] would not have caught that either — it sees a Kotlin
 * object, never the HTTP request, so it cannot notice a wrong path or a GET where the
 * server wants a POST. So this drives the REAL chain, exactly as [ApprovalWireTest] and
 * [VoicePatchWireTest] do: [NetworkModule.createApis]'s OkHttp client, its `Json`, its
 * Retrofit converter, the real [FcmTokenRegistrar] — against a local server.
 *
 * The "nothing was sent" assertions carry as much weight as the positive one. `require_user`
 * guards the endpoint, so a registration attempted without a session is a round trip to a
 * certain 401; `requestCount` is the only thing that can tell "correctly skipped" apart
 * from "sent and silently rejected".
 */
class FcmWireTest {

    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() = server.close()

    /** The real production chain, pointed at the local server. */
    private fun registrar(
        signedIn: Boolean = true,
        deviceToken: String? = "device-token-1",
    ): FcmTokenRegistrar {
        val apis = NetworkModule.createApis(
            tokenProvider = { "test-token".takeIf { signedIn } },
            baseUrl = server.url("/").toString(),
        )
        return FcmTokenRegistrar(
            api = apis.fcm,
            isSignedIn = { signedIn },
            currentToken = { deviceToken },
        )
    }

    private fun enqueueOk() = server.enqueue(
        MockResponse.Builder()
            .code(200)
            .addHeader("Content-Type", "application/json")
            .body("""{"ok":true}""")
            .build(),
    )

    private fun enqueue401() = server.enqueue(
        MockResponse.Builder()
            .code(401)
            .addHeader("Content-Type", "application/json")
            .body("""{"detail":"Kimlik doğrulanamadı"}""")
            .build(),
    )

    @Test
    fun register_postsTheTokenToTheRegisterEndpoint() = runBlocking {
        enqueueOk()

        assertEquals(FcmRegistration.REGISTERED, registrar().register("abc-123"))

        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/fcm/register", request.url.encodedPath)
        // The endpoint is require_user; a body without the Bearer header can only 401.
        assertEquals("Bearer test-token", request.headers["Authorization"])

        val body = checkNotNull(request.body) { "kayıt isteği gövdesiz gitti" }.utf8()
        assertTrue("gövde token taşımalı, gövde: $body", body.contains("\"token\":\"abc-123\""))
    }

    /**
     * The app-open path — the one that matters, because `onNewToken` fires only when the
     * token CHANGES and never again on a device whose token predates this build.
     */
    @Test
    fun registerCurrentToken_sendsTheTokenTheDeviceAlreadyHolds() = runBlocking {
        enqueueOk()

        assertEquals(
            FcmRegistration.REGISTERED,
            registrar(deviceToken = "already-minted").registerCurrentToken(),
        )

        val body = checkNotNull(server.takeRequest().body).utf8()
        assertTrue(body.contains("\"already-minted\""))
    }

    @Test
    fun signedOut_neverTouchesTheServer() = runBlocking {
        assertEquals(
            FcmRegistration.SKIPPED_SIGNED_OUT,
            registrar(signedIn = false).register("abc-123"),
        )
        assertEquals("oturum yokken istek gitmemeli", 0, server.requestCount)
    }

    @Test
    fun noDeviceToken_neverTouchesTheServer() = runBlocking {
        val subject = registrar(deviceToken = null)

        assertEquals(FcmRegistration.SKIPPED_NO_TOKEN, subject.registerCurrentToken())
        assertEquals(FcmRegistration.SKIPPED_NO_TOKEN, subject.register("   "))
        assertEquals("token yokken istek gitmemeli", 0, server.requestCount)
    }

    /**
     * A 401 must degrade, not crash: this runs inside `MainActivity`'s boot coroutine and
     * inside Firebase's `onNewToken` callback, and an exception in either would take down
     * something far more important than a push registration.
     */
    @Test
    fun aRejectedRegistration_reportsFailure_andIsRetriedNextTime() = runBlocking {
        enqueue401()
        enqueueOk()
        val subject = registrar()

        assertEquals(FcmRegistration.FAILED, subject.register("abc-123"))
        // The failure must NOT be remembered as "already registered", or a token that lost
        // one race would never be sent again for the life of the process.
        assertEquals(FcmRegistration.REGISTERED, subject.register("abc-123"))
        assertEquals(2, server.requestCount)
    }

    /**
     * The server keys the document on sha256(token), so a repeat is harmless — but a POST
     * on every Activity recreation is still waste, and `LaunchedEffect(Unit)` re-runs on
     * every rotation.
     */
    @Test
    fun theSameTokenIsNotReRegisteredWithinOneProcess() = runBlocking {
        enqueueOk()
        val subject = registrar()

        assertEquals(FcmRegistration.REGISTERED, subject.register("abc-123"))
        assertEquals(FcmRegistration.SKIPPED_ALREADY, subject.register("abc-123"))
        assertEquals(1, server.requestCount)
    }

    /** A genuinely NEW token (Firebase rotated it) is not swallowed by that guard. */
    @Test
    fun aRotatedTokenIsRegisteredAgain() = runBlocking {
        enqueueOk()
        enqueueOk()
        val subject = registrar()

        assertEquals(FcmRegistration.REGISTERED, subject.register("abc-123"))
        assertEquals(FcmRegistration.REGISTERED, subject.register("def-456"))
        assertEquals(2, server.requestCount)
    }
}
