package com.jarvis.data.voice.session

/**
 * Pure Kotlin fallback-decision engine for the piped recognizer feed.
 * Starts in PFD mode; flips to legacy after consecutive dead cycles detect
 * a broken pipe.
 */
class PfdFeedPolicy {
    private var failStreak: Int = 0
    private var usePfd: Boolean = true
    private var lastSummary: String = ""

    companion object {
        /**
         * Minimum bytes consumed to count a cycle as "alive" purely on byte count
         * (only consulted for cycles that ran long enough to judge -- see
         * [JUDGE_AFTER_MS] and [onCycleEnd]). 96_000 bytes = 3 seconds of 16 kHz
         * PCM16.
         *
         * Deliberately set ABOVE the OS pipe buffer (~64 KB on Linux/Android): the
         * writer thread can push up to that much into the pipe's kernel buffer even
         * when NOTHING on the other end is reading it -- a recognizer that never
         * touches the pipe still absorbs ~64 KB "for free" before the writer would
         * ever block. A threshold at or below that buffer size cannot distinguish
         * "the recognizer actually drained the pipe" from "the kernel buffer alone
         * soaked up the bytes and the writer just hasn't blocked yet" -- exactly the
         * failure this policy exists to catch (Task 7's probe used a 192 KB fixture
         * for this same reason: it must exceed the buffer to prove real consumption).
         * The original value here (32_000 = 1s) sat inside the buffer and made the
         * permanent-fallback net decorative for the PFD_IGNORED case -- fixed in
         * Task 3 fix round 1 (2026-08-11) alongside the [writerStuck] parameter,
         * which covers the same failure from the other direction.
         *
         * [JUDGE_AFTER_MS] and this constant are ONE decision -- "how much silent,
         * signal-free feed does it take before we're willing to call it suspicious"
         * -- expressed twice, once as a duration and once as the byte count that
         * duration implies at 16 kHz mono PCM16. Changing one without the other
         * would make them describe two different durations; keep them in lockstep.
         */
        private const val MIN_CONSUMED_BYTES = 96_000L

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
     * actually carries evidence either way.
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
            }
            Verdict.ALIVE -> failStreak = 0
            Verdict.INCONCLUSIVE -> {
                // Deliberately a no-op -- see this function's doc for why neither
                // incrementing nor resetting is correct here.
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
}
