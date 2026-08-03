package com.jarvis.data.approvals

import com.jarvis.data.net.ApprovalApi
import com.jarvis.data.net.ApprovalDecisionDto
import com.jarvis.data.net.ApprovalDto
import com.jarvis.data.net.ApprovalsResponse
import kotlinx.coroutines.runBlocking
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

    private class FakeApprovalApi(
        var queue: List<ApprovalDto> = emptyList(),
        var single: ApprovalDto = ApprovalDto(id = "a1", title = "t"),
        var decision: ApprovalDecisionDto = ApprovalDecisionDto("approved", "oldu", false),
    ) : ApprovalApi {
        var approved: String? = null
        var rejected: String? = null
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
        override suspend fun reject(id: String): ApprovalDecisionDto {
            rejected = id
            return decision
        }
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
        val rejected = repo.reject("a2")

        assertEquals("a1", api.approved)
        assertEquals("a2", api.rejected)
        assertEquals(ApprovalStatus.FAILED, approved.status)
        assertTrue(approved.outcome!!.contains("yürütme hatası"))
        assertEquals(ApprovalStatus.FAILED, rejected.status)
    }

    /** A decision body the client cannot read is not a silent success. */
    @Test
    fun decision_withAnUnknownStatus_isUnknown_notApproved() = runBlocking {
        val api = FakeApprovalApi(decision = ApprovalDecisionDto(status = null))
        assertEquals(ApprovalStatus.UNKNOWN, ApprovalRepository(api).approve("a1").status)
    }
}
