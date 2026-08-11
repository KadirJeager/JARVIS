package com.jarvis.data.voice.session

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PfdFeedPolicyTest {
    @Test fun startsInPfdMode() {
        assertTrue(PfdFeedPolicy().shouldUsePfd())
    }

    @Test fun twoConsecutiveDeadCyclesFlipToLegacy() {
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0)
        assertTrue(p.shouldUsePfd()) // one dead cycle is not proof
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 100)
        assertFalse(p.shouldUsePfd())
    }

    @Test fun aLiveCycleResetsTheStreak() {
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0)
        p.onCycleEnd(hadPartial = true, hadResult = false, bytesWritten = 500)
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0)
        assertTrue(p.shouldUsePfd())
    }

    @Test fun consumedBytesAloneCountAsAlive() {
        // The recognizer drained ≥1s of audio but endpointed on silence with no
        // text: the PIPE works, there was just nothing to hear. Not a failure.
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 32_000)
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 32_000)
        assertTrue(p.shouldUsePfd())
    }

    @Test fun theFlipIsPermanentForThisInstance() {
        val p = PfdFeedPolicy()
        repeat(2) { p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0) }
        p.onCycleEnd(hadPartial = true, hadResult = true, bytesWritten = 90_000)
        assertFalse(p.shouldUsePfd())
    }

    @Test fun summaryCarriesTheDataFields() {
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = true, hadResult = true, bytesWritten = 64_000)
        val s = p.cycleSummary()
        assertTrue(s.contains("bytes=64000"))
        assertTrue(s.contains("pfd=true"))
    }
}
