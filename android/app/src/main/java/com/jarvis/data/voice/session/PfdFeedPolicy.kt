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
         * Minimum bytes consumed to count a cycle as "alive".
         * 32_000 bytes = 1 second of 16 kHz PCM16.
         * A recognizer that reads at all drains far more than 1 s while endpointing
         * (silence detection + partial buffer + socket handshake); if we see fewer
         * than 1 s consumed, the pipe is likely blocked or the recognizer crashed.
         */
        private const val MIN_CONSUMED_BYTES = 32_000L

        /**
         * Flip to legacy mode after this many consecutive dead cycles.
         * One dead cycle can be a transient service race (recognizer briefly busy,
         * socket hiccup). Two consecutive is a pattern: the pipe is broken.
         */
        private const val FALLBACK_AFTER = 2
    }

    /**
     * Observe one complete recognizer cycle: whether it yielded a partial,
     * whether it yielded a result, and how many bytes the pipe fed it.
     */
    fun onCycleEnd(hadPartial: Boolean, hadResult: Boolean, bytesWritten: Long) {
        val isCycleDead = !hadPartial && !hadResult && bytesWritten < MIN_CONSUMED_BYTES

        if (isCycleDead) {
            failStreak++
            if (failStreak >= FALLBACK_AFTER) {
                usePfd = false
            }
        } else {
            // Any live cycle (partial, result, or ≥ 1 s consumed) resets the streak.
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
