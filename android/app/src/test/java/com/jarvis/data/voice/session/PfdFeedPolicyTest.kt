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
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false)
        assertTrue(p.shouldUsePfd()) // one dead cycle is not proof
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 100, writerStuck = false)
        assertFalse(p.shouldUsePfd())
    }

    @Test fun aLiveCycleResetsTheStreak() {
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false)
        p.onCycleEnd(hadPartial = true, hadResult = false, bytesWritten = 500, writerStuck = false)
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false)
        assertTrue(p.shouldUsePfd())
    }

    @Test fun consumedBytesAloneCountAsAlive() {
        // The recognizer drained enough audio (well past the ~64 KB kernel pipe
        // buffer, so this is real evidence of consumption -- see
        // PfdFeedPolicy.MIN_CONSUMED_BYTES) but endpointed on silence with no text:
        // the PIPE works, there was just nothing to hear. Not a failure.
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 100_000, writerStuck = false)
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 100_000, writerStuck = false)
        assertTrue(p.shouldUsePfd())
    }

    @Test fun theFlipIsPermanentForThisInstance() {
        val p = PfdFeedPolicy()
        repeat(2) { p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false) }
        p.onCycleEnd(hadPartial = true, hadResult = true, bytesWritten = 90_000, writerStuck = false)
        assertFalse(p.shouldUsePfd())
    }

    @Test fun summaryCarriesTheDataFields() {
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = true, hadResult = true, bytesWritten = 64_000, writerStuck = false)
        val s = p.cycleSummary()
        assertTrue(s.contains("bytes=64000"))
        assertTrue(s.contains("pfd=true"))
    }

    // Fix round 1 (2026-08-11): bytesWritten alone was a false-positive liveness
    // signal -- the ~64 KB kernel pipe buffer absorbs bytes even when nobody reads
    // them, so PFD_IGNORED cycles were scoring "alive" and the fallback net never
    // engaged. The three tests below pin the corrected semantics.

    @Test fun writerStuckForcesDeadEvenWithLargeBytesWritten() {
        // 500_000 bytes is far past MIN_CONSUMED_BYTES -- if bytesWritten were still
        // the only signal, this would score alive. writerStuck must override it.
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 500_000, writerStuck = true)
        assertTrue(p.shouldUsePfd()) // one dead cycle is not proof yet
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 500_000, writerStuck = true)
        assertFalse(p.shouldUsePfd())
    }

    @Test fun bytesBelowNewThresholdWithWriterNotStuckIsStillDead() {
        // 64_000 bytes is exactly what the OS pipe buffer alone can absorb with no
        // reader at all -- the old (32_000) threshold would have scored this alive;
        // the corrected threshold (96_000) must not.
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 64_000, writerStuck = false)
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 64_000, writerStuck = false)
        assertFalse(p.shouldUsePfd())
    }

    @Test fun bytesAboveNewThresholdIsAlive() {
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 100_000, writerStuck = false)
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 100_000, writerStuck = false)
        assertTrue(p.shouldUsePfd())
    }
}
