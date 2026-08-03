package com.jarvis.data.net

import com.jarvis.data.approvals.ApprovalRepository
import com.jarvis.data.approvals.ApprovalStatus
import kotlinx.coroutines.runBlocking
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * The approval contract at the level of the bytes production actually sends.
 *
 * A hand-written fake `ApprovalApi` sees Kotlin objects, never the HTTP request — so it
 * cannot notice a wrong path, a GET where the server wants a POST, or a field name that
 * does not match `brain/app/approvals.py::_project`. That is the 3d-3 lesson restated: a
 * mutation only carries weight when the object it produces is proven to be the one
 * production uses. So this drives the REAL chain — [NetworkModule.createApis]'s OkHttp
 * client, its `Json`, its Retrofit converter, the real [ApprovalRepository] — against a
 * local server and asserts the request line as well as the decoded result.
 *
 * `approvals-tick` is absent on purpose: it is the scheduler's endpoint (require_scheduler),
 * not the client's. An [ApprovalApi] method for it would be a way for the phone to call it.
 */
class ApprovalWireTest {

    private lateinit var server: MockWebServer
    private lateinit var repo: ApprovalRepository

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val apis = NetworkModule.createApis(
            tokenProvider = { "test-token" },
            baseUrl = server.url("/").toString(),
        )
        repo = ApprovalRepository(apis.approvals)
    }

    @After
    fun tearDown() = server.close()

    private fun enqueue(body: String) = server.enqueue(
        MockResponse.Builder()
            .code(200)
            .addHeader("Content-Type", "application/json")
            .body(body)
            .build(),
    )

    /** One row exactly as `approvals._project` writes it. */
    private val oneApproval = """
        {"id":"a1","user_id":"kadir@example.com","kind":"tool_call",
         "title":"'cancel_reminder' çalıştırılsın mı?",
         "detail":"Kırmızı bölge eylemi: cancel_reminder(reminder_id=r7).",
         "tool_name":"cancel_reminder","tool_args":{"reminder_id":"r7"},
         "zone":"red","session_id":"s-1","status":"pending",
         "created_at":"2026-08-03T10:00:00+00:00","expires_at":"2026-08-03T11:00:00+00:00",
         "decided_at":null,"decided_by":null,"outcome":null}
    """.trimIndent()

    @Test
    fun listPending_getsTheQueueEndpoint_andDecodesTheRow() = runBlocking {
        enqueue("""{"approvals":[$oneApproval]}""")

        val queue = repo.pending()

        val request = server.takeRequest()
        assertEquals("GET", request.method)
        assertEquals("/api/approvals", request.url.encodedPath)

        assertEquals(1, queue.size)
        assertEquals("a1", queue[0].id)
        assertEquals(ApprovalStatus.PENDING, queue[0].status)
        assertEquals("cancel_reminder", queue[0].toolName)
        assertTrue(queue[0].title.contains("cancel_reminder"))
        assertEquals("r7", queue[0].toolArgs["reminder_id"])
    }

    /** Spec §7: the badge is NOT baked into the transcript — the card re-reads this. */
    @Test
    fun get_readsTheSingleApprovalPath() = runBlocking {
        enqueue(oneApproval.replace("\"status\":\"pending\"", "\"status\":\"approved\""))

        val approval = repo.get("a1")

        val request = server.takeRequest()
        assertEquals("GET", request.method)
        assertEquals("/api/approvals/a1", request.url.encodedPath)
        assertEquals(ApprovalStatus.APPROVED, approval!!.status)
    }

    @Test
    fun approve_postsToTheApprovePath_andReturnsTheServersStatus() = runBlocking {
        enqueue("""{"status":"approved","outcome":"Hatırlatma iptal edildi.","already":false}""")

        val decision = repo.approve("a1")

        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/approvals/a1/approve", request.url.encodedPath)

        assertEquals(ApprovalStatus.APPROVED, decision.status)
        assertEquals("Hatırlatma iptal edildi.", decision.outcome)
        assertFalse(decision.already)
    }

    @Test
    fun reject_postsToTheRejectPath() = runBlocking {
        enqueue("""{"status":"rejected","outcome":null,"already":false}""")

        val decision = repo.reject("a1")

        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/approvals/a1/reject", request.url.encodedPath)
        assertEquals(ApprovalStatus.REJECTED, decision.status)
        assertNull(decision.outcome)
    }

    /**
     * A second decision is the server's to describe (claim document, spec §4.2). The
     * client must carry `already` through untouched rather than inventing a verdict.
     */
    @Test
    fun approve_carriesTheServersAlreadyFlagAndItsFinalStatus() = runBlocking {
        enqueue("""{"status":"expired","outcome":"Onay süresi doldu; eylem çalıştırılmadı.","already":true}""")

        val decision = repo.approve("a1")

        assertEquals(ApprovalStatus.EXPIRED, decision.status)
        assertTrue(decision.already)
    }
}
