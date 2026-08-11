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
         * (only consulted when there was no partial and no result -- see
         * [onCycleEnd]). 96_000 bytes = 3 seconds of 16 kHz PCM16.
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
         * Task 3 fix round 1 (2026-08-11) alongside the [writerStuck] parameter
         * below, which covers the same failure from the other direction.
         */
        private const val MIN_CONSUMED_BYTES = 96_000L

        /**
         * Flip to legacy mode after this many consecutive dead cycles.
         * One dead cycle can be a transient service race (recognizer briefly busy,
         * socket hiccup). Two consecutive is a pattern: the pipe is broken.
         */
        private const val FALLBACK_AFTER = 2
    }

    /**
     * Observe one complete recognizer cycle: whether it yielded a partial, whether
     * it yielded a result, how many bytes the pipe fed it, and whether the writer
     * thread was still stuck (blocked inside a pipe write, never reached EOF) when
     * the cycle was torn down.
     *
     * [writerStuck] is the authoritative "nobody was reading" signal, and it makes a
     * cycle dead REGARDLESS of [bytesWritten]: the kernel pipe buffer can silently
     * absorb up to ~64 KB with nobody downstream ever reading it (see
     * [MIN_CONSUMED_BYTES]'s doc), so a large byte count alone is not proof of life
     * when the writer itself never finished. A cycle with a partial or a result is
     * always alive regardless of either signal -- that is direct evidence the
     * recognizer actually processed something from the pipe.
     */
    fun onCycleEnd(
        hadPartial: Boolean,
        hadResult: Boolean,
        bytesWritten: Long,
        writerStuck: Boolean,
    ) {
        val isCycleDead =
            !hadPartial && !hadResult && (writerStuck || bytesWritten < MIN_CONSUMED_BYTES)

        if (isCycleDead) {
            failStreak++
            if (failStreak >= FALLBACK_AFTER) {
                usePfd = false
            }
        } else {
            // Any live cycle (partial, result, or enough bytes actually consumed)
            // resets the streak.
            failStreak = 0
        }

        lastSummary = "bytes=$bytesWritten pfd=$usePfd"
    }

    /**
     * Whether to use the piped PFD mode. Once flipped to false, never returns true
     * for this instance (permanent per call).
     */
    fun shouldUsePfd(): Boolean = usePfd

    /**
     * One-line DATA string for logging: current bytes written and current mode.
     */
    fun cycleSummary(): String = lastSummary
}
