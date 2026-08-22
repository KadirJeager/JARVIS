package com.jarvis.data.approvals

import com.jarvis.data.net.ApprovalApi
import com.jarvis.data.net.ApprovalDecisionDto
import com.jarvis.data.net.ApprovalDto
import com.jarvis.data.net.ApprovalsResponse
import com.jarvis.data.net.ReasonsResponse
import com.jarvis.data.net.RejectReasonDto
import com.jarvis.data.net.RejectRequest
import kotlinx.coroutines.runBlocking
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import retrofit2.HttpException
import retrofit2.Response
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The tolerant-wire / strict-domain boundary for approvals (the
 * [com.jarvis.data.voice.VoiceProfileRepository] pattern).
 *
 * Two rules, and they pull in opposite directions on purpose:
 *
 * - A row that cannot be RENDERED is dropped, and only that row. One malformed approval
 *   must not blank the whole queue — the screen's entire job is to show what IS there.
 * - A status this build has never heard of is NOT dropped and NOT treated as pending:
 *   it degrades to [ApprovalStatus.UNKNOWN], which the card draws without decision
 *   buttons. Fail-closed. Guessing "pending" would offer an Onayla button for something
 *   the server may already have decided.
 */
class ApprovalRepositoryTest {

    /** Builds a real retrofit HttpException for a status code, the way OkHttp would. */
    private fun http(code: Int) = HttpException(
        Response.error<Any>(code, "".toResponseBody("application/json".toMediaType())),
    )

    /** An API whose queue call always fails; everything else is unused here. */
    private class FailingApi(private val boom: Throwable) : ApprovalApi {
        override suspend fun list(): ApprovalsResponse = throw boom
        override suspend fun get(id: String): ApprovalDto = throw boom
        override suspend fun approve(id: String): ApprovalDecisionDto = throw boom
        override suspend fun reject(id: String, request: RejectRequest): ApprovalDecisionDto = throw boom
        override suspend fun reasons(): ReasonsResponse = throw boom
    }

    private class FakeApprovalApi(
        var queue: List<ApprovalDto> = emptyList(),
        var single: ApprovalDto = ApprovalDto(id = "a1", title = "t"),
        var decision: ApprovalDecisionDto = ApprovalDecisionDto("approved", "oldu", false),
        var reasonsResponse: ReasonsResponse = ReasonsResponse(),
    ) : ApprovalApi {
        var approved: String? = null
        var rejected: String? = null
        var rejectedReason: String? = null
        var fetched: String? = null

        override suspend fun list() = ApprovalsResponse(queue)
        override suspend fun get(id: String): ApprovalDto {
            fetched = id
            return single
        }
        override suspend fun approve(id: String): ApprovalDecisionDto {
            approved = id
            return decision
        }
        override suspend fun reject(id: String, request: RejectRequest): ApprovalDecisionDto {
            rejected = id
            rejectedReason = request.reason
            return decision
        }
        override suspend fun reasons(): ReasonsResponse = reasonsResponse
    }

    @Test
    fun pending_mapsEveryWireFieldTheCardDraws() = runBlocking {
        val api = FakeApprovalApi(
            queue = listOf(
                ApprovalDto(
                    id = "a1",
                    kind = "tool_call",
                    title = "'cancel_reminder' çalıştırılsın mı?",
                    detail = "Kırmızı bölge eylemi.",
                    tool_name = "cancel_reminder",
                    tool_args = mapOf("reminder_id" to "r7"),
                    status = "pending",
                    created_at = "2026-08-03T10:00:00+00:00",
                    expires_at = "2026-08-03T11:00:00+00:00",
                ),
            ),
        )

        val rows = ApprovalRepository(api).pending()

        assertEquals(1, rows.size)
        assertEquals("a1", rows[0].id)
        assertEquals(ApprovalStatus.PENDING, rows[0].status)
        assertEquals("cancel_reminder", rows[0].toolName)
        assertEquals("Kırmızı bölge eylemi.", rows[0].detail)
        assertEquals("r7", rows[0].toolArgs["reminder_id"])
        assertEquals("2026-08-03T11:00:00+00:00", rows[0].expiresAt)
    }

    @Test
    fun pending_mapsEveryStatusTheServerCanWrite() = runBlocking {
        val api = FakeApprovalApi(
            queue = listOf("pending", "approved", "rejected", "expired", "failed")
                .mapIndexed { i, s -> ApprovalDto(id = "a$i", title = "t", status = s) },
        )

        assertEquals(
            listOf(
                ApprovalStatus.PENDING,
                ApprovalStatus.APPROVED,
                ApprovalStatus.REJECTED,
                ApprovalStatus.EXPIRED,
                ApprovalStatus.FAILED,
            ),
            ApprovalRepository(api).pending().map { it.status },
        )
    }

    /** Fail-closed: an unheard-of status must never come out as PENDING. */
    @Test
    fun pending_mapsAnUnknownStatusToUnknown_neverToPending() = runBlocking {
        val api = FakeApprovalApi(
            queue = listOf(ApprovalDto(id = "a1", title = "t", status = "quantum")),
        )
        assertEquals(ApprovalStatus.UNKNOWN, ApprovalRepository(api).pending()[0].status)
    }

    /**
     * One bad row must not blank the queue. An approval with no id has nothing to
     * approve; one with no title has nothing to show.
     */
    @Test
    fun pending_dropsRowsWithNoIdOrNoTitle_andKeepsTheRest() = runBlocking {
        val api = FakeApprovalApi(
            queue = listOf(
                ApprovalDto(id = "a1", title = "ilk", status = "pending"),
                ApprovalDto(id = null, title = "id yok", status = "pending"),
                ApprovalDto(id = "  ", title = "id boş", status = "pending"),
                ApprovalDto(id = "a4", title = null, status = "pending"),
                ApprovalDto(id = "a5", title = "son", status = "pending"),
            ),
        )

        assertEquals(listOf("a1", "a5"), ApprovalRepository(api).pending().map { it.id })
    }

    @Test
    fun get_returnsNullForAnUnrenderableRow_ratherThanAHalfCard() = runBlocking {
        val api = FakeApprovalApi(single = ApprovalDto(id = "a1", title = null))
        assertNull(ApprovalRepository(api).get("a1"))
        assertEquals("a1", api.fetched)
    }

    @Test
    fun decisions_reachTheirEndpointsAndCarryTheServersVerdict() = runBlocking {
        val api = FakeApprovalApi(
            decision = ApprovalDecisionDto("failed", "yürütme hatası: boom", already = false),
        )
        val repo = ApprovalRepository(api)

        val approved = repo.approve("a1")
        val rejected = repo.reject("a2", "Şimdi olmaz")

        assertEquals("a1", api.approved)
        assertEquals("a2", api.rejected)
        assertEquals("Şimdi olmaz", api.rejectedReason)
        assertEquals(ApprovalStatus.FAILED, approved.status)
        assertTrue(approved.outcome!!.contains("yürütme hatası"))
        assertEquals(ApprovalStatus.FAILED, rejected.status)
    }

    @Test
    fun pending_preservesNewFieldsAndZone() = runBlocking {
        val api = FakeApprovalApi(
            queue = listOf(
                ApprovalDto(
                    id = "a1",
                    title = "t",
                    status = "pending",
                    zone = "red",
                    actor = "orchestrator",
                    trust_level = "HIGH",
                    cause = "red_zone",
                    operand = "user_123",
                    reversible = true,
                    decision_reason = "gerekce",
                ),
            ),
        )
        val item = ApprovalRepository(api).pending()[0]
        assertEquals("red", item.zone)
        assertEquals("orchestrator", item.actor)
        assertEquals("HIGH", item.trustLevel)
        assertEquals("red_zone", item.cause)
        assertEquals("user_123", item.operand)
        assertEquals(true, item.reversible)
        assertEquals("gerekce", item.decisionReason)
    }

    @Test
    fun pending_withoutNewFields_yieldsNulls() = runBlocking {
        val api = FakeApprovalApi(
            queue = listOf(ApprovalDto(id = "a1", title = "t", status = "pending")),
        )
        val item = ApprovalRepository(api).pending()[0]
        assertNull(item.zone)
        assertNull(item.actor)
        assertNull(item.trustLevel)
        assertNull(item.cause)
        assertNull(item.operand)
        assertNull(item.reversible)
        assertNull(item.decisionReason)
    }

    @Test
    fun reasons_mapsValidRowsAndDropsUnrenderableRows() = runBlocking {
        val api = FakeApprovalApi(
            reasonsResponse = ReasonsResponse(
                reasons = listOf(
                    RejectReasonDto(id = "r1", title = "T1", prompt_fill = "F1"),
                    RejectReasonDto(id = null, title = "T2", prompt_fill = "F2"),
                    RejectReasonDto(id = "  ", title = "T3", prompt_fill = "F3"),
                    RejectReasonDto(id = "r4", title = "T4", prompt_fill = null),
                    RejectReasonDto(id = "r5", title = "T5", prompt_fill = "  "),
                    RejectReasonDto(id = "r6", title = null, prompt_fill = "F6"),
                    RejectReasonDto(id = "r7", title = "  ", prompt_fill = "F7"),
                ),
            ),
        )
        val list = ApprovalRepository(api).reasons()
        assertEquals(3, list.size)

        assertEquals("r1", list[0].id)
        assertEquals("T1", list[0].title)
        assertEquals("F1", list[0].promptFill)

        // Title falls back to id when title is null or blank
        assertEquals("r6", list[1].id)
        assertEquals("r6", list[1].title)
        assertEquals("F6", list[1].promptFill)

        assertEquals("r7", list[2].id)
        assertEquals("r7", list[2].title)
        assertEquals("F7", list[2].promptFill)
    }

    @Test
    fun reasons_handlesVersionSkew404() = runBlocking {
        val repo = ApprovalRepository(FailingApi(http(404)))
        assertEquals(emptyList<RejectReason>(), repo.reasons())
    }

    /** A decision body the client cannot read is not a silent success. */
    @Test
    fun decision_withAnUnknownStatus_isUnknown_notApproved() = runBlocking {
        val api = FakeApprovalApi(decision = ApprovalDecisionDto(status = null))
        assertEquals(ApprovalStatus.UNKNOWN, ApprovalRepository(api).approve("a1").status)
    }

    // --- version skew: an older server has no approval centre ---------------------

    @Test
    fun aServerWithoutTheApprovalEndpoint_readsAsAnEmptyQueue_notAnError() = runBlocking {
        // The app can be newer than the deployment (it was, on 2026-08-03: the branch
        // shipped to the phone while production still 404'd /api/approvals). A 404 here
        // means the server HAS no approvals, so reporting an error banner on every
        // launch and every send would be alarming Kadir about nothing.
        val repo = ApprovalRepository(FailingApi(http(404)))
        assertEquals(emptyList<Approval>(), repo.pending())
    }

    @Test
    fun aRealFailure_stillPropagates_soAPendingRedActionIsNeverHiddenSilently() = runBlocking {
        // The opposite case, and the reason the 404 rule is narrow: a 502 or a 401 means
        // "we do not know what is pending". Answering "nothing" there would hide a red
        // action behind a green-looking screen.
        val repo = ApprovalRepository(FailingApi(http(502)))
        val patladi = try {
            repo.pending()
            false
        } catch (e: HttpException) {
            assertEquals(502, e.code())
            true
        }
        assertTrue("502 yutulmamalı", patladi)
    }
}
