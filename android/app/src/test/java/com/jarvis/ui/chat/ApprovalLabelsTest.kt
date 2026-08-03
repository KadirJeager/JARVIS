package com.jarvis.ui.chat

import com.jarvis.data.approvals.ApprovalStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The badge copy is the only thing telling Kadir whether a red action ran. Two claims are
 * worth a test rather than a code review:
 *
 * - `expired` does not read as "Reddedildi". The timeout IS a rejection (spec §4.1), but
 *   wording it that way would claim Kadir decided something the clock decided.
 * - `failed` does not read as "Reddedildi" either: the approval succeeded and the tool it
 *   authorised is what blew up (spec §4.5). Collapsing those two would make a failed
 *   deletion look like a deliberate refusal.
 */
class ApprovalLabelsTest {

    @Test
    fun everyStatusHasItsOwnTurkishLabel() {
        assertEquals("Onay bekliyor", approvalStatusLabel(ApprovalStatus.PENDING))
        assertEquals("Onaylandı", approvalStatusLabel(ApprovalStatus.APPROVED))
        assertEquals("Reddedildi", approvalStatusLabel(ApprovalStatus.REJECTED))
        assertEquals("Süresi doldu", approvalStatusLabel(ApprovalStatus.EXPIRED))
        assertEquals("Çalıştırılamadı", approvalStatusLabel(ApprovalStatus.FAILED))
        assertEquals("Durum bilinmiyor", approvalStatusLabel(ApprovalStatus.UNKNOWN))
    }

    @Test
    fun noTwoStatusesShareALabel() {
        val labels = ApprovalStatus.entries.map { approvalStatusLabel(it) }
        assertEquals("her durum ayırt edilebilmeli", labels.size, labels.toSet().size)
        assertTrue("hiçbir rozet boş olamaz", labels.none { it.isBlank() })
    }
}
