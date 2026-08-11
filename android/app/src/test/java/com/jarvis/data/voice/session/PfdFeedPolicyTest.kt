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
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false, elapsedMs = 3_000)
        assertTrue(p.shouldUsePfd()) // one dead cycle is not proof
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 100, writerStuck = false, elapsedMs = 3_000)
        assertFalse(p.shouldUsePfd())
    }

    @Test fun aLiveCycleResetsTheStreak() {
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false, elapsedMs = 3_000)
        // A short cycle (500ms) with a partial is still ALIVE -- hadPartial bypasses
        // the duration gate entirely (it is direct evidence, not an inference).
        p.onCycleEnd(hadPartial = true, hadResult = false, bytesWritten = 500, writerStuck = false, elapsedMs = 500)
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false, elapsedMs = 3_000)
        assertTrue(p.shouldUsePfd())
    }

    @Test fun consumedBytesAloneCountAsAlive() {
        // The recognizer drained enough audio (well past the ~64 KB kernel pipe
        // buffer, so this is real evidence of consumption -- see
        // PfdFeedPolicy.MIN_CONSUMED_BYTES) but endpointed on silence with no
        // text: the PIPE works, there was just nothing to hear. Not a failure.
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 150_000, writerStuck = false, elapsedMs = 3_000)
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 150_000, writerStuck = false, elapsedMs = 3_000)
        assertTrue(p.shouldUsePfd())
    }

    @Test fun theFlipIsPermanentForThisInstance() {
        val p = PfdFeedPolicy()
        repeat(2) {
            p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false, elapsedMs = 3_000)
        }
        p.onCycleEnd(hadPartial = true, hadResult = true, bytesWritten = 90_000, writerStuck = false, elapsedMs = 500)
        assertFalse(p.shouldUsePfd())
    }

    @Test fun summaryCarriesTheDataFields() {
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = true, hadResult = true, bytesWritten = 64_000, writerStuck = false, elapsedMs = 500)
        val s = p.cycleSummary()
        assertTrue(s.contains("bytes=64000"))
        assertTrue(s.contains("pfd=true"))
        // Fix round 2: the summary must carry every deciding input, not just the
        // outcome -- one failing run must localize the fault without a rerun.
        assertTrue(s.contains("verdict=alive"))
        assertTrue(s.contains("hadPartial=true"))
        assertTrue(s.contains("hadResult=true"))
        assertTrue(s.contains("writerStuck=false"))
        assertTrue(s.contains("elapsedMs=500"))
    }

    // Fix round 1 (2026-08-11): bytesWritten alone was a false-positive liveness
    // signal -- the ~64 KB kernel pipe buffer absorbs bytes even when nobody reads
    // them, so PFD_IGNORED cycles were scoring "alive" and the fallback net never
    // engaged. The tests below pin the corrected byte-threshold semantics (all now
    // run at elapsedMs=3_000, i.e. long enough to be judged at all -- see the fix
    // round 2 section further down for the duration gate itself).

    @Test fun writerStuckForcesDeadEvenWithLargeBytesWritten() {
        // 500_000 bytes is far past MIN_CONSUMED_BYTES -- if bytesWritten were still
        // the only signal, this would score alive. writerStuck must override it.
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 500_000, writerStuck = true, elapsedMs = 3_000)
        assertTrue(p.shouldUsePfd()) // one dead cycle is not proof yet
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 500_000, writerStuck = true, elapsedMs = 3_000)
        assertFalse(p.shouldUsePfd())
    }

    @Test fun bytesBelowNewThresholdWithWriterNotStuckIsStillDead() {
        // 64_000 bytes is exactly what the OS pipe buffer alone can absorb with no
        // reader at all -- the pre-fix-round-1 (32_000) threshold would have scored
        // this alive; the current threshold (80_000, fix round 3) must not.
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 64_000, writerStuck = false, elapsedMs = 3_000)
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 64_000, writerStuck = false, elapsedMs = 3_000)
        assertFalse(p.shouldUsePfd())
    }

    @Test fun bytesAboveNewThresholdIsAlive() {
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 100_000, writerStuck = false, elapsedMs = 3_000)
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 100_000, writerStuck = false, elapsedMs = 3_000)
        assertTrue(p.shouldUsePfd())
    }

    // Fix round 2 (2026-08-11): MIN_CONSUMED_BYTES alone conflated "the pipe is
    // broken" with "the cycle was short" -- a fully healthy cycle can legitimately
    // produce no partial/result and end well under 3s (ERROR_RECOGNIZER_BUSY /
    // ERROR_NO_MATCH / ERROR_SPEECH_TIMEOUT are all on isRecoverableSttError and can
    // all fire quickly), which is normal idle behaviour of an unattended call, not
    // evidence the pipe is broken. These tests pin the duration-gated, three-state
    // (alive/dead/inconclusive) fix.

    @Test fun shortSilentCyclesAreInconclusiveAndNeverFlip() {
        // Repeated short, signal-free cycles (idle-call behaviour) must NOT count as
        // evidence the pipe is broken -- this is the exact false positive fix round
        // 2 exists to close. Five in a row, well past FALLBACK_AFTER's count of 2,
        // to make clear this is not merely "not yet enough", it never counts at all.
        val p = PfdFeedPolicy()
        repeat(5) {
            p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false, elapsedMs = 200)
        }
        assertTrue(p.shouldUsePfd())
    }

    @Test fun inconclusiveCycleDoesNotResetAnExistingFailStreak() {
        // Distinguishes "untouched" from "reset": if an inconclusive cycle reset the
        // streak, the third call below would be only the first dead cycle after that
        // reset (streak=1) and shouldUsePfd() would stay true. Because inconclusive
        // leaves the streak untouched, the streak carries 1 -> 2 across the gap and
        // the third call flips it.
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false, elapsedMs = 3_000) // DEAD, streak=1
        assertTrue(p.shouldUsePfd())
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false, elapsedMs = 200) // INCONCLUSIVE, streak untouched
        assertTrue(p.shouldUsePfd())
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false, elapsedMs = 3_000) // DEAD, streak=2 -> flip
        assertFalse(p.shouldUsePfd())
    }

    @Test fun partialWithWriterStuckIsStillAlive() {
        // A refactor to `dead = writerStuck || (...)` (dropping the hadPartial/
        // hadResult short-circuit) would flip this to dead and fail this test: a
        // partial is direct evidence the recognizer read something from the pipe,
        // and must win over every other signal, including a stuck writer.
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = true, hadResult = false, bytesWritten = 0, writerStuck = true, elapsedMs = 3_000)
        p.onCycleEnd(hadPartial = true, hadResult = false, bytesWritten = 0, writerStuck = true, elapsedMs = 3_000)
        assertTrue(p.shouldUsePfd())
    }

    @Test fun resultWithWriterStuckIsStillAlive() {
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = true, bytesWritten = 0, writerStuck = true, elapsedMs = 3_000)
        p.onCycleEnd(hadPartial = false, hadResult = true, bytesWritten = 0, writerStuck = true, elapsedMs = 3_000)
        assertTrue(p.shouldUsePfd())
    }

    @Test fun bytesExactlyAtThresholdVerdictIsAlive() {
        // Fix round 3 (2026-08-11): threshold lowered 96_000 -> 80_000 (see the
        // "Fix round 3" section below for why 96_000, exactly JUDGE_AFTER_MS's
        // byte equivalent, was itself a bug).
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 80_000, writerStuck = false, elapsedMs = 3_000)
        assertTrue(p.cycleSummary().contains("verdict=alive"))
    }

    @Test fun bytesJustBelowThresholdVerdictIsDead() {
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 79_999, writerStuck = false, elapsedMs = 3_000)
        assertTrue(p.cycleSummary().contains("verdict=dead"))
    }

    @Test fun elapsedExactlyAtJudgeThresholdVerdictIsDead() {
        // Exactly JUDGE_AFTER_MS with no signal and bytes below MIN_CONSUMED_BYTES
        // must be JUDGED (dead), not treated as still-too-short-to-call
        // (inconclusive) -- the gate is `elapsedMs < JUDGE_AFTER_MS`, so the
        // boundary value itself is already long enough.
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false, elapsedMs = 3_000)
        assertTrue(p.cycleSummary().contains("verdict=dead"))
    }

    @Test fun elapsedJustBelowJudgeThresholdVerdictIsInconclusive() {
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false, elapsedMs = 2_999)
        assertTrue(p.cycleSummary().contains("verdict=inconclusive"))
    }

    // Fix round 3 (2026-08-11): fix round 2 set MIN_CONSUMED_BYTES (96_000) EQUAL
    // to JUDGE_AFTER_MS's byte equivalent (3_000ms x 32_000 B/s = 96_000), on the
    // theory the two constants were "one decision expressed twice, keep them in
    // lockstep". That was wrong: bytesWritten structurally lags elapsedMs on a
    // perfectly healthy cycle (teardown time -- readFd.close() + the writer join
    // -- falls inside the measured span; the first mic chunk costs ~40-64ms before
    // any byte exists; queue.clear() discards an in-flight frame), so
    // `bytesWritten <= 32 * elapsedMs` always holds physically but is NEVER an
    // equality in practice -- any future test case must respect that inequality,
    // not assume a cycle can produce exactly 32 bytes per elapsed millisecond.

    @Test fun healthyCycleWithRealisticByteDeficitIsNotDead() {
        // Pins the exact regression fix round 3 closes: on the OLD (round 2)
        // threshold of 96_000, this cycle -- realistic, healthy, just carrying the
        // structural lag described above -- would have scored DEAD purely from
        // that lag, deterministically, on every device whose recognizer
        // silence-endpoints in this window. bytesWritten is computed from the
        // physical relation, not hardcoded, so this test fails loudly if a future
        // edit reintroduces an equality between the two constants.
        val p = PfdFeedPolicy()
        val elapsedMs = 3_100L
        val bytesPerMs = 32L // 16 kHz mono PCM16 = 32_000 B/s = 32 B/ms
        val realisticDeficitBytes = 6_000L // ~187ms worth -- within the documented ~50-200ms lag
        val bytesWritten = bytesPerMs * elapsedMs - realisticDeficitBytes // 93_200
        p.onCycleEnd(
            hadPartial = false,
            hadResult = false,
            bytesWritten = bytesWritten,
            writerStuck = false,
            elapsedMs = elapsedMs,
        )
        assertTrue(p.cycleSummary().contains("verdict=alive"))
        assertTrue(p.shouldUsePfd())
    }

    @Test fun consecutiveInconclusiveCountTracksAndResetsOnAnyJudgedVerdict() {
        val p = PfdFeedPolicy()
        assertEquals(0, p.consecutiveInconclusiveCount())
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false, elapsedMs = 200)
        assertEquals(1, p.consecutiveInconclusiveCount())
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false, elapsedMs = 200)
        assertEquals(2, p.consecutiveInconclusiveCount())
        // A DEAD verdict resets it just like an ALIVE one would -- only
        // INCONCLUSIVE itself extends the streak.
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0, writerStuck = false, elapsedMs = 3_000)
        assertEquals(0, p.consecutiveInconclusiveCount())
    }
}
