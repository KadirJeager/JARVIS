package com.jarvis.data.voice.session

/**
 * Pure Kotlin fallback-decision engine for the piped recognizer feed.
 * Starts in PFD mode; flips to legacy after consecutive dead cycles look
 * SILENT -- no partial, no result, and (once judged) too few bytes consumed
 * or a stuck writer.
 *
 * Explicit limit: this does NOT detect a broken pipe in general, only a
 * SILENT one. The classic PFD_IGNORED shape -- the recognizer ignores our fd
 * entirely, opens its own microphone instead, and transcribes normally off
 * ambient audio -- produces ordinary partials and results exactly like a
 * working PFD feed would, so it sets hadResult=true (or hadPartial=true) and
 * scores ALIVE forever. This class cannot tell "the recognizer heard OUR fed
 * audio" from "the recognizer heard SOMETHING, from wherever its mic
 * actually is" -- it has no access to what was fed, only to whether the
 * recognizer produced output. What DOES cover that gap: the instrumented
 * test (PfdSpeechToTextTest, Task 4 of
 * docs/superpowers/plans/2026-08-11-tek-audiorecord-pfd.md) feeds a known
 * fixture through the tap and asserts the resulting transcript matches THAT
 * fixture, not merely that a transcript arrived -- the only place in this
 * plan that can actually distinguish "read our fd" from "ignored it and
 * transcribed ambient audio instead".
 */
class PfdFeedPolicy {
    private var failStreak: Int = 0
    private var usePfd: Boolean = true
    private var lastSummary: String = ""

    // Consecutive INCONCLUSIVE verdicts in a row; reset by any ALIVE or DEAD
    // verdict. See [consecutiveInconclusiveCount]'s doc for what this is for.
    private var inconclusiveStreak: Int = 0

    companion object {
        /**
         * The Linux/Android pipe kernel buffer size, in bytes: the most a writer
         * can push into a pipe with NO reader ever draining it. Lower bound of
         * the corridor [MIN_CONSUMED_BYTES] must sit in -- see that constant's
         * doc.
         */
        private const val PIPE_BUFFER_BYTES = 65_536L

        /**
         * Minimum bytes consumed to count a cycle as "alive" purely on byte
         * count (only consulted for cycles that ran long enough to judge -- see
         * [JUDGE_AFTER_MS] and [onCycleEnd]). 80_000 bytes = 2.5 seconds of
         * 16 kHz PCM16 (32_000 bytes/s).
         *
         * Must sit strictly inside the corridor ([PIPE_BUFFER_BYTES], 96_000) =
         * (65_536, 96_000) -- 96_000 being what [JUDGE_AFTER_MS] implies at
         * 32_000 B/s (3.000 s x 32_000 B/s):
         *
         * - Strictly ABOVE [PIPE_BUFFER_BYTES] (~64 KB): the writer thread can
         *   push up to that much into the pipe's kernel buffer even when
         *   NOTHING on the other end is reading it -- a recognizer that never
         *   touches the pipe still absorbs that much "for free" before the
         *   writer would ever block. Bytes at or below this prove nothing about
         *   real consumption (Task 7's probe used a 192 KB fixture for this
         *   same reason: it must exceed the buffer to prove real consumption).
         *   Fixed in Task 3 fix round 1 (2026-08-11) -- the original value
         *   (32_000 = 1s) sat inside the buffer and made the permanent-fallback
         *   net decorative for the PFD_IGNORED case.
         * - Strictly BELOW 96_000, i.e. MIN_CONSUMED_BYTES / 32_000 (its
         *   implied seconds) must be strictly LESS than JUDGE_AFTER_MS / 1000
         *   (its seconds) -- NOT equal. This is not a rounding nicety:
         *   bytesWritten structurally lags elapsedMs on a perfectly healthy
         *   cycle, for three independent reasons -- elapsedMs is stamped at
         *   cycle creation and read AFTER readFd.close() + the writer join in
         *   AndroidSpeechToText.endCurrentPfdCycle(), so teardown time is
         *   inside the measured span while zero further bytes flow; the first
         *   mic chunk itself costs ~40-64ms of clock before any byte exists
         *   (AndroidMicSource.readFrame() blocks for a full getMinBufferSize()
         *   read); and endCurrentPfdCycle()'s queue.clear() discards whatever
         *   was still queued but not yet written, with those bytes never
         *   counted. Net deficit ~50-200ms (~1.6-6.4 KB) on ordinary healthy
         *   cycles. Fix round 2 (2026-08-11) set this constant EQUAL to
         *   JUDGE_AFTER_MS's byte equivalent (96_000, both exactly 3.000s) on
         *   the theory that the two described "one decision expressed twice,
         *   keep them in lockstep" -- wrong: that equality meant a HEALTHY
         *   no-signal cycle landing in roughly [3.00s, 3.20s] scored DEAD
         *   purely from this structural lag, deterministically, on every
         *   device whose recognizer silence-endpoints in that window. Fixed in
         *   Task 3 fix round 3 (2026-08-11) by giving this constant real slack
         *   (0.5s) under the duration gate instead of matching it exactly.
         */
        private const val MIN_CONSUMED_BYTES = 80_000L

        /**
         * Minimum cycle duration, in milliseconds, before its outcome is judged at
         * all -- see [onCycleEnd]'s INCONCLUSIVE case. Fixed in Task 3 fix round 2
         * (2026-08-11): [MIN_CONSUMED_BYTES] alone conflated "the pipe is broken"
         * with "the cycle was short", because a fully healthy cycle can legitimately
         * end well under 3 s with no partial and no result --
         * `SpeechRecognizer.ERROR_RECOGNIZER_BUSY` returns near-instantly, and
         * `ERROR_NO_MATCH` / `ERROR_SPEECH_TIMEOUT` (both on
         * [isRecoverableSttError]) can fire well under 3 s on an unattended call.
         * That is normal LISTENING-phase idling, not evidence of anything -- without
         * this gate, two such ordinary short-silent cycles in a row would flip a
         * perfectly working device to legacy mode, silently, for the rest of the
         * call.
         *
         * See [MIN_CONSUMED_BYTES]'s doc for why this constant and that one are
         * related by a strict inequality, not equality: bytesWritten structurally
         * lags elapsedMs, so MIN_CONSUMED_BYTES's implied duration must sit strictly
         * below this one's, never equal to or above it.
         */
        private const val JUDGE_AFTER_MS = 3_000L

        /**
         * Flip to legacy mode after this many consecutive dead cycles.
         * One dead cycle can be a transient service race (recognizer briefly busy,
         * socket hiccup). Two consecutive is a pattern: the pipe is broken.
         */
        private const val FALLBACK_AFTER = 2
    }

    /** The three possible outcomes of judging one cycle; see [onCycleEnd]. */
    private enum class Verdict(val label: String) {
        ALIVE("alive"),
        DEAD("dead"),
        INCONCLUSIVE("inconclusive"),
    }

    /**
     * Observe one complete recognizer cycle: whether it yielded a partial, whether
     * it yielded a result, how many bytes the pipe fed it, whether the writer
     * thread was still stuck (blocked inside a pipe write, never reached EOF) when
     * the cycle was torn down, and how long the cycle ran (the caller measures wall
     * clock -- this class stays JVM-pure, no clock of its own).
     *
     * Three-state verdict per cycle:
     * - **ALIVE**: [hadPartial] or [hadResult] is true (always, regardless of every
     *   other signal -- direct evidence the recognizer actually processed
     *   something from the pipe), OR the cycle ran long enough to judge
     *   ([elapsedMs] >= [JUDGE_AFTER_MS]) and the writer was NOT stuck and enough
     *   bytes were consumed (>= [MIN_CONSUMED_BYTES]).
     * - **DEAD**: no partial, no result, the cycle ran long enough to judge, and
     *   either the writer was stuck or too few bytes were consumed.
     * - **INCONCLUSIVE**: no partial, no result, and the cycle was too short to
     *   judge ([elapsedMs] < [JUDGE_AFTER_MS]) -- see [JUDGE_AFTER_MS]'s doc for why
     *   a short signal-free cycle is normal idle behaviour, not evidence.
     *
     * Only ALIVE and DEAD move the fail streak (reset / increment respectively).
     * INCONCLUSIVE leaves it UNTOUCHED on purpose, in both directions: resetting it
     * on an inconclusive cycle would let a device that always short-cycles (never
     * runs a cycle long enough to be judged) never flip even if the pipe is
     * genuinely broken; counting an inconclusive cycle as a failure is exactly the
     * false positive this gate exists to remove (idle LISTENING-phase silence must
     * not accumulate towards the fallback). A cycle only moves the streak when it
     * actually carries evidence either way. [consecutiveInconclusiveCount] tracks
     * the OTHER side of that trade -- see its doc.
     */
    fun onCycleEnd(
        hadPartial: Boolean,
        hadResult: Boolean,
        bytesWritten: Long,
        writerStuck: Boolean,
        elapsedMs: Long,
    ) {
        val verdict = when {
            hadPartial || hadResult -> Verdict.ALIVE
            elapsedMs < JUDGE_AFTER_MS -> Verdict.INCONCLUSIVE
            writerStuck || bytesWritten < MIN_CONSUMED_BYTES -> Verdict.DEAD
            else -> Verdict.ALIVE
        }

        when (verdict) {
            Verdict.DEAD -> {
                failStreak++
                if (failStreak >= FALLBACK_AFTER) {
                    usePfd = false
                }
                inconclusiveStreak = 0
            }
            Verdict.ALIVE -> {
                failStreak = 0
                inconclusiveStreak = 0
            }
            Verdict.INCONCLUSIVE -> {
                // failStreak deliberately untouched -- see this function's doc.
                inconclusiveStreak++
            }
        }

        // Every deciding input, not just the outcome: the project's DATA-logging
        // rule is that one failing run must localize the fault without a rerun, and
        // "pfd=false" alone cannot tell a later reader WHICH cycle's evidence
        // tipped the flip.
        lastSummary = "bytes=$bytesWritten pfd=$usePfd verdict=${verdict.label} " +
            "hadPartial=$hadPartial hadResult=$hadResult writerStuck=$writerStuck " +
            "elapsedMs=$elapsedMs"
    }

    /**
     * Whether to use the piped PFD mode. Once flipped to false, never returns true
     * for this instance (permanent per call).
     */
    fun shouldUsePfd(): Boolean = usePfd

    /**
     * One-line DATA string for logging: every input [onCycleEnd] just evaluated,
     * the verdict it reached, and the resulting mode.
     */
    fun cycleSummary(): String = lastSummary

    /**
     * How many consecutive cycles in a row have been INCONCLUSIVE (too short to
     * judge); reset to 0 by any ALIVE or DEAD verdict. This is the trade
     * INCONCLUSIVE buys, made observable: a recognizer that accepts our fd but
     * never drains it, on a device whose cycles also happen to always end short
     * of [JUDGE_AFTER_MS], would stay INCONCLUSIVE forever -- [shouldUsePfd]
     * never flips to legacy, so STT stays silently deaf for the rest of the call
     * with nothing in the log calling attention to it (fix round 3, 2026-08-11).
     * Deliberately just a counter, not a log line: this class stays free of
     * Android imports (JVM-pure by design, see the class doc and
     * PfdFeedPolicyTest), so the caller (AndroidSpeechToText, which already owns
     * every `tl ev=` log line) reads this after each [onCycleEnd] and logs the
     * escape hatch itself.
     */
    fun consecutiveInconclusiveCount(): Int = inconclusiveStreak
}
