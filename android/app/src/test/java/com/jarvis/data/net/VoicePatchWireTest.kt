package com.jarvis.data.net

import com.jarvis.data.voice.VoiceProfileRepository
import kotlinx.coroutines.runBlocking
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * The ONE test that looks at the bytes production actually sends.
 *
 * Everything above this line can be green while the app is broken: a fake `VoiceApi` sees
 * the Kotlin object, never the JSON, so it cannot notice that `LabelPatch(label = null)`
 * used to serialize to `{}` — which the server (model_fields_set, voice_manage.py) reads
 * as "change nothing", returns 200 for, and which therefore made "Etiketi kaldır" a
 * silent no-op with no error anywhere.
 *
 * So this drives the REAL chain — [NetworkModule.createApis]'s OkHttp client, its
 * `Json`, its Retrofit converter, the real [VoiceProfileRepository] — against a local
 * server, and asserts the request body itself.
 *
 * The negative assertions matter as much as the positive ones: a body that mentions the
 * field the user did NOT touch tells the server to CLEAR it. A label edit that carried
 * `"note":null` would silently destroy the user's free-text note.
 */
class VoicePatchWireTest {

    private lateinit var server: MockWebServer
    private lateinit var repo: VoiceProfileRepository

    /** Any 200 with a valid sample body; the response is not what this test is about. */
    private fun enqueueSample() = server.enqueue(
        MockResponse.Builder()
            .code(200)
            .addHeader("Content-Type", "application/json")
            .body("""{"id":"s1","source":"manual","ts":null,"device_hint":null,"label":null,"note":null}""")
            .build(),
    )

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val apis = NetworkModule.createApis(
            tokenProvider = { "test-token" },
            baseUrl = server.url("/").toString(),
        )
        repo = VoiceProfileRepository(apis.voice)
    }

    @After
    fun tearDown() = server.close()

    private fun takeBody(): String {
        val request = server.takeRequest()
        assertEquals("PATCH", request.method)
        assertEquals("/api/voice/sample/s1", request.url.encodedPath)
        // `body` is nullable in mockwebserver3 — a PATCH that sent nothing at all is
        // itself the failure this test exists to catch, so surface it rather than skip.
        return checkNotNull(request.body) { "PATCH gövdesiz gitti" }.utf8()
    }

    @Test
    fun clearingTheLabel_sendsAnExplicitNull_andNeverMentionsTheNote() = runBlocking {
        enqueueSample()
        repo.setLabel("s1", null)

        val body = takeBody()
        assertTrue(
            "etiketi temizlemek açık null göndermeli, gövde: $body",
            body.contains("\"label\":null"),
        )
        assertFalse(
            "etiket düzenlemesi note alanına dokunmamalı, gövde: $body",
            body.contains("note"),
        )
    }

    @Test
    fun settingALabel_sendsTheValue_andNeverMentionsTheNote() = runBlocking {
        enqueueSample()
        repo.setLabel("s1", "yorgun")

        val body = takeBody()
        assertTrue(
            "etiket değeri gövdede olmalı, gövde: $body",
            body.contains("\"label\":\"yorgun\""),
        )
        assertFalse(
            "etiket düzenlemesi note alanına dokunmamalı, gövde: $body",
            body.contains("note"),
        )
    }

    @Test
    fun clearingTheNote_sendsAnExplicitNull_andNeverMentionsTheLabel() = runBlocking {
        enqueueSample()
        repo.setNote("s1", null)

        val body = takeBody()
        assertTrue(
            "notu temizlemek açık null göndermeli, gövde: $body",
            body.contains("\"note\":null"),
        )
        assertFalse(
            "not düzenlemesi label alanına dokunmamalı, gövde: $body",
            body.contains("label"),
        )
    }
}
