package com.jarvis.data.voice.session

import android.content.Context
import android.content.Intent
import android.media.AudioFormat
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log
import com.jarvis.data.voice.protocol.AUDIO_IN_RATE_HZ
import java.io.IOException
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.atomic.AtomicLong

/**
 * Real on-device STT: `SpeechRecognizer` configured for tr-TR free-form dictation,
 * preferring the offline (on-device) recognizer when the platform offers one.
 *
 * On-device availability ([SpeechRecognizer.isOnDeviceRecognitionAvailable]) is a
 * GENERIC capability flag -- it says nothing about whether the requested language
 * (tr-TR) is actually installed. If the on-device recognizer reports
 * `ERROR_LANGUAGE_UNAVAILABLE` (its language pack is missing, e.g. evicted under
 * storage pressure), [fallbackToNetworkRecognizer] transparently tears down the
 * on-device recognizer, rebuilds on `SpeechRecognizer.createSpeechRecognizer` (the
 * network one), and re-arms listening for the SAME turn instead of surfacing
 * [SpeechToTextListener.onFatalError] -- see [sttErrorAction] for the full decision
 * table. The flip is one-way and sticky for the rest of this instance's life
 * (mirroring [PfdFeedPolicy]'s own "permanent for this instance" shape): once
 * [usingOnDeviceRecognizer] is false, it never goes back to true. If the network
 * recognizer then reports the SAME code, that IS fatal -- there is nowhere left to
 * fall back to.
 *
 * PFD feed (Task 3 of docs/superpowers/plans/2026-08-11-tek-audiorecord-pfd.md): when
 * [tapSource] is non-null, each recognizer cycle is fed from the session's own
 * `AudioRecord` via a piped [ParcelFileDescriptor] instead of letting the recognizer
 * open its own microphone -- this is what actually puts STT inside the AEC-processed
 * signal (see the long comment on [recognizeIntent] for why the earlier
 * EXTRA_AUDIO_SOURCE attempt never worked). [PfdFeedPolicy] decides, per [start] call,
 * whether to keep using the pipe: two consecutive DEAD cycles flip it to legacy mode
 * PERMANENTLY for that instance. A cycle is only judged dead or alive once it has run
 * long enough to mean anything (a short cycle with no partial and no result is normal
 * idle behaviour -- ERROR_RECOGNIZER_BUSY/NO_MATCH/SPEECH_TIMEOUT can all fire quickly
 * on an unattended call -- not evidence, so it counts as neither and leaves the streak
 * untouched); once a cycle has run long enough, it is dead only if it produced no
 * partial, no result, AND either the writer was still stuck at cycle end or too few
 * bytes were actually consumed. See [PfdFeedPolicy.onCycleEnd]'s doc for the full
 * three-state verdict and why bytes written alone is not proof of consumption. A null
 * [tapSource] is the same legacy mode from the very first call -- the safety hatch if
 * PFD needs to be disabled entirely.
 *
 * Per-cycle pipe/writer lifecycle: [buildPfdCycleIntent] creates a fresh
 * `ParcelFileDescriptor` pipe, a bounded queue, and a daemon writer thread that drains
 * the queue into the pipe's write end; [endCurrentPfdCycle] tears all three down
 * exactly once -- on every terminal recognizer callback (`onResults`, `onError`) and on
 * [destroy]. It also runs at the top of [listen], in case a previous cycle's terminal
 * callback has not fired yet, so two back-to-back [listen] calls never leak a pipe or a
 * writer thread. [activeCycle] is nulled out FIRST inside [endCurrentPfdCycle], before
 * any teardown work runs -- that ordering is what makes cleanup idempotent, not a
 * separate flag. Within that teardown, our own `readFd` copy is closed BEFORE the
 * writer thread is joined (not after): see the inline comment at that call site for
 * why the reverse order silently guaranteed the join's full timeout in exactly the
 * `writerStuck` case it exists to detect.
 *
 * Threading: every `SpeechRecognizer` call is marshalled onto the main thread, because
 * [VoiceSession] invokes this class from the transport's reader thread while the platform
 * recognizer is main-thread-bound. Recognition callbacks arrive on the main thread and
 * are forwarded to the session's listener directly -- the session synchronizes itself.
 * All PFD cycle bookkeeping ([activeCycle], [policy], [hadPartial]/[hadResult]) is
 * main-thread-only for the same reason. The one exception is the tap installed on
 * [tapSource]: it runs on the mic's own capture thread (`Dispatchers.IO` inside
 * `AndroidMicSource.readFrame()`), and its body touches only the cycle's
 * `ArrayBlockingQueue` and `AtomicLong` counters -- both thread-safe on their own, so
 * nothing PFD-related needs a lock shared with the main thread.
 *
 * This class cannot be unit-tested on the JVM (it needs a real recognizer service);
 * [VoiceSession]'s restart/barge-in logic is tested against a fake [SpeechToText]
 * instead (VoiceSessionTest). Only [isRecoverableSttError] and [sttErrorAction], pure
 * functions over the platform's error-code constants, are JVM-tested
 * (SttErrorMappingTest); [fallbackToNetworkRecognizer] itself, like the rest of this
 * class, is not. The PFD path is proven by an instrumented test instead
 * (PfdSpeechToTextTest, Task 4 of the same plan).
 *
 * This class's own `tl ev=` lines (Task 11) intentionally omit the `t=`/`gen=` fields
 * [VoiceSession]'s carry -- there is no session-relative clock or generation counter at
 * this layer to attach -- and rely on logcat's own per-line timestamp for correlation
 * against [VoiceSession]'s lines instead.
 */
class AndroidSpeechToText(
    private val context: Context,
    private val tapSource: PcmTapSource? = null,
) : SpeechToText {

    private val main = Handler(Looper.getMainLooper())

    // Only ever touched on the main thread.
    private var recognizer: SpeechRecognizer? = null

    // Main-thread only. One instance per successful start() call, matching
    // PfdFeedPolicy's "permanent for this instance" fallback semantics. Left null
    // when tapSource == null: that instance is legacy-mode forever and has no
    // decision to make.
    private var policy: PfdFeedPolicy? = null

    // Main-thread only. Non-null exactly while a PFD cycle's pipe/writer/tap are
    // live; null between cycles and in legacy mode. See the class doc for why
    // nulling this FIRST inside endCurrentPfdCycle() is the cleanup-once guard.
    private var activeCycle: PfdCycle? = null

    // Main-thread only; monotonic. Used only to correlate a cycle's teardown log
    // line with its setup -- not itself a concurrency guard.
    private var cycleGen = 0

    // Main-thread only, cycle-scoped: reset in listen(), read by endCurrentPfdCycle().
    private var hadPartial = false
    private var hadResult = false

    // Main-thread only. Logged once per start()/destroy() cycle -- the first time
    // this instance falls back to (or never attempts) the PFD feed after a given
    // start() -- so repeated legacy cycles do not spam logcat. destroy() resets it,
    // so a restarted instance (a fresh start() after destroy()) gets one fresh log
    // line of its own rather than staying permanently silent.
    private var loggedFallback = false

    // Main-thread only. Which recognizer [recognizer] currently is -- set whenever it
    // is (re)built, in start() and in fallbackToNetworkRecognizer(). Read by onError
    // to decide sttErrorAction()'s FallbackToNetwork branch. Distinct from the PFD
    // [policy]'s legacy-mode flip: this is about which SpeechRecognizer implementation
    // is in use, not which audio feed path it gets.
    private var usingOnDeviceRecognizer = false

    // Written by start()/destroy() (any thread), read from main-thread callbacks.
    @Volatile
    private var listener: SpeechToTextListener? = null

    private val recognitionListener = object : RecognitionListener {
        override fun onBeginningOfSpeech() {
            listener?.onBeginningOfSpeech()
        }

        override fun onPartialResults(partialResults: Bundle?) {
            val text = partialResults
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
                ?: return
            hadPartial = true
            listener?.onPartialResult(text)
        }

        override fun onResults(results: Bundle?) {
            hadResult = true
            val text = results
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
            // Lengths only, never transcript content (existing convention).
            Log.i(TAG, "tl ev=onResults len=${text?.length ?: 0}")
            endCurrentPfdCycle()
            // An empty final result is a no-match in disguise: treat it as recoverable
            // so the session re-arms listening instead of stalling with no callback.
            if (text.isNullOrBlank()) listener?.onRecoverableError() else listener?.onResult(text)
        }

        override fun onError(error: Int) {
            Log.i(TAG, "tl ev=onError code=$error")
            endCurrentPfdCycle()
            when (sttErrorAction(error, usingOnDeviceRecognizer)) {
                SttErrorAction.Retry -> listener?.onRecoverableError()
                SttErrorAction.FallbackToNetwork -> fallbackToNetworkRecognizer()
                SttErrorAction.Fatal -> {
                    Log.w(TAG, "SpeechRecognizer fatal error: $error")
                    listener?.onFatalError()
                }
            }
        }

        // rms/buffer events carry no protocol meaning and stay silent (would otherwise
        // spam the timeline at frame rate). onReadyForSpeech/onEndOfSpeech ARE logged
        // below: onReadyForSpeech is THE datum for the first-words investigation -- the
        // moment the recognizer is actually listening, closing the listen()->ready gap
        // that is the primary deaf-window suspect.
        override fun onReadyForSpeech(params: Bundle?) {
            Log.i(TAG, "tl ev=onReadyForSpeech")
        }
        override fun onEndOfSpeech() {
            Log.i(TAG, "tl ev=onEndOfSpeech")
        }
        override fun onRmsChanged(rmsdB: Float) {}
        override fun onBufferReceived(buffer: ByteArray?) {}
        override fun onEvent(eventType: Int, params: Bundle?) {}
    }

    override fun start(listener: SpeechToTextListener) {
        this.listener = listener
        main.post {
            if (recognizer != null) return@post
            if (!SpeechRecognizer.isRecognitionAvailable(context)) {
                Log.w(TAG, "No speech recognition service on this device")
                listener.onFatalError()
                return@post
            }
            // One policy instance per start() call (see class doc).
            policy = tapSource?.let { PfdFeedPolicy() }
            usingOnDeviceRecognizer = isOnDeviceRecognizerAvailable(context)
            recognizer = createRecognizer(context).apply {
                setRecognitionListener(recognitionListener)
            }
        }
    }

    override fun listen() {
        main.post {
            Log.i(TAG, "tl ev=startListening")
            // A previous cycle's pipe/writer/tap may still be live if listen() is
            // called again before its terminal callback fired; tear it down before
            // building a fresh one so back-to-back calls never leak a pipe or a thread.
            endCurrentPfdCycle()
            hadPartial = false
            hadResult = false

            val tap = tapSource
            val activePolicy = policy
            val intent = if (tap != null && activePolicy != null && activePolicy.shouldUsePfd()) {
                buildPfdCycleIntent(tap) ?: run {
                    // Pipe setup itself failed before any recognizer session started --
                    // fall back for just this call. No cycle was attempted, so the
                    // policy's streak is untouched; the next listen() retries PFD.
                    Log.w(TAG, "tl ev=pfd.setupFailed")
                    recognizeIntent
                }
            } else {
                logFallbackOnce(tap, activePolicy)
                recognizeIntent
            }
            // null when start() has not completed or declared a fatal error -- the
            // session is already tearing down in that case, so dropping is correct.
            recognizer?.startListening(intent)
        }
    }

    /**
     * One-way flip triggered from [onError] when [sttErrorAction] returns
     * [SttErrorAction.FallbackToNetwork] -- see the class doc. By the time this runs,
     * [onError] has already called [endCurrentPfdCycle] unconditionally (it runs before
     * the `when` that dispatches here), so there is no PFD cycle left to tear down; the
     * re-armed [listen] call below builds a fresh one exactly as any other listen() call
     * would. [usingOnDeviceRecognizer] flips to false immediately -- the SAME instant
     * [onError] observed it fail -- but destroying the old recognizer and building the
     * new one is deferred one more turn through [main] even though [onError] already
     * runs on the main thread: everything else that mutates [recognizer] in this class
     * goes through `main.post`, and doing the same here means `recognizer.destroy()`
     * never runs from inside that SAME recognizer's own callback stack.
     */
    private fun fallbackToNetworkRecognizer() {
        Log.i(TAG, "tl ev=stt.recognizerFallback from=onDevice to=network reason=languageUnavailable")
        usingOnDeviceRecognizer = false
        main.post {
            recognizer?.destroy()
            // SpeechRecognizer.createSpeechRecognizer directly, NOT the createRecognizer()
            // factory below -- that one re-checks on-device availability and would just
            // rebuild the SAME on-device recognizer that just failed, defeating the
            // one-way flip.
            recognizer = SpeechRecognizer.createSpeechRecognizer(context).apply {
                setRecognitionListener(recognitionListener)
            }
            listen()
        }
    }

    override fun destroy() {
        listener = null
        main.post {
            endCurrentPfdCycle()
            recognizer?.destroy()
            recognizer = null
            policy = null
            loggedFallback = false
            usingOnDeviceRecognizer = false
        }
    }

    /**
     * Builds a fresh pipe + writer + tap for one recognizer cycle and returns the
     * intent carrying the four PFD extras, or null if the pipe itself could not be
     * created ([ParcelFileDescriptor.createPipe] threw, e.g. the process is out of
     * file descriptors) -- in which case no cycle state is installed at all.
     */
    private fun buildPfdCycleIntent(tap: PcmTapSource): Intent? {
        val pipe = try {
            ParcelFileDescriptor.createPipe()
        } catch (io: IOException) {
            Log.w(TAG, "tl ev=pfd.pipeCreateFailed msg=${io.message}")
            return null
        }
        val readFd = pipe[0]
        val writeFd = pipe[1]
        val queue = ArrayBlockingQueue<ByteArray>(PFD_QUEUE_CAPACITY)
        val bytesWritten = AtomicLong(0)
        val drops = AtomicLong(0)

        val writer = startPfdWriter(queue, writeFd, bytesWritten)
        // Tap body: pure, non-blocking, never throws -- runs on the mic's capture
        // read path (Dispatchers.IO in AndroidMicSource.readFrame()), never on main.
        tap.setTap { frame -> if (!queue.offer(frame)) drops.incrementAndGet() }

        activeCycle = PfdCycle(
            gen = ++cycleGen,
            readFd = readFd,
            queue = queue,
            writerThread = writer,
            bytesWritten = bytesWritten,
            drops = drops,
            // Stamped here, read back in endCurrentPfdCycle() to compute the
            // cycle's wall-clock duration for PfdFeedPolicy's duration gate.
            // elapsedRealtime (not currentTimeMillis): monotonic, unaffected by
            // wall-clock adjustments, and this is a duration, not a timestamp.
            startedAtElapsedMs = SystemClock.elapsedRealtime(),
        )

        // Copy of the base intent (same extras as today) plus the PFD-only ones.
        // Do NOT close readFd here -- SpeechRecognizer.startListening() queues the
        // actual Binder hand-off through its own internal Handler rather than
        // performing it synchronously, so closing our copy immediately can race that
        // hand-off (observed as a spurious ERROR:5 -- see RecognizerPipeProbeTest).
        // readFd is closed only in endCurrentPfdCycle().
        return Intent(recognizeIntent).apply {
            putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE, readFd)
            // AUDIO_IN_RATE_HZ, not a separate 16000 literal: this must always match
            // the rate AndroidMicSource was actually started at (VoiceSession.kt calls
            // mic.start(AUDIO_IN_RATE_HZ)) -- one concept, one spelling. A drifted
            // literal here would silently lie to the recognizer about the feed's rate.
            putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_SAMPLING_RATE, AUDIO_IN_RATE_HZ)
            putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_CHANNEL_COUNT, 1)
            putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_ENCODING, AudioFormat.ENCODING_PCM_16BIT)
        }
    }

    /** Daemon thread: drains [queue] into [writeFd] until [POISON_PILL] arrives. */
    private fun startPfdWriter(
        queue: ArrayBlockingQueue<ByteArray>,
        writeFd: ParcelFileDescriptor,
        bytesWritten: AtomicLong,
    ): Thread {
        val writer = Thread({
            try {
                ParcelFileDescriptor.AutoCloseOutputStream(writeFd).use { out ->
                    while (true) {
                        val frame = queue.take()
                        if (frame === POISON_PILL) break
                        out.write(frame)
                        bytesWritten.addAndGet(frame.size.toLong())
                    }
                }
            } catch (io: IOException) {
                // Pipe broken (recognizer stopped reading, or the read side tore down
                // first) -- not a bug, the feed just ended before EOF. `.use` above
                // still closes writeFd on the way out.
                Log.w(TAG, "tl ev=pfd.writerIoError msg=${io.message}")
            } catch (interrupted: InterruptedException) {
                // Not triggered by our own cleanup (a poison pill, not
                // Thread.interrupt()); handled defensively. `.use` still closes
                // writeFd on the way out.
                Log.w(TAG, "tl ev=pfd.writerInterrupted")
            }
        }, "AndroidSpeechToText-pfd-writer")
        writer.isDaemon = true
        writer.start()
        return writer
    }

    /**
     * Tears down the currently active PFD cycle, if any, exactly once. Safe to call
     * from any terminal recognizer callback, from [listen] (to close out a cycle a new
     * call is superseding), and from [destroy]. [activeCycle] is nulled out FIRST, so a
     * second call arriving before this one returns -- or one that races in after -- sees
     * nothing left to tear down instead of double-closing fds or double-counting the
     * cycle in [policy].
     */
    private fun endCurrentPfdCycle() {
        val cycle = activeCycle ?: return
        activeCycle = null

        tapSource?.setTap(null)

        // Deliver the "stop" signal even if the queue is currently full: clear it
        // first so there is always room, then offer -- offer() on a freshly-cleared
        // bounded queue cannot fail for lack of capacity. Any frame a still-in-flight
        // tap invocation offers after this point lands in an abandoned queue nobody
        // reads from again; harmless, since a fresh queue is created per cycle.
        cycle.queue.clear()
        cycle.queue.offer(POISON_PILL)

        // Close OUR copy of the read end BEFORE joining the writer -- order matters.
        // As long as we hold readFd open, the pipe's read-end refcount can never
        // reach zero, so a writer blocked inside write() can never receive EPIPE no
        // matter what the recognizer process does with ITS copy; joining first (the
        // original order) meant the join below was guaranteed to burn its full
        // timeout in exactly the PFD_IGNORED case it exists to detect. Closing here,
        // not right after startListening(), is deliberate -- but the two call sites
        // that reach this are not equally certain the Binder hand-off has completed:
        // a terminal recognizer callback (onResults/onError) DOES prove it -- the
        // recognizer already read from (or gave up on) this exact fd, so the
        // hand-off is unquestionably done. destroy() and a second listen() racing
        // ahead of the first cycle's terminal callback do NOT have that proof; they
        // reach here via their own main.post{}, shortly after startListening() was
        // posted on the same Handler/Looper -- we rely on SpeechRecognizer's queued
        // dispatch being bound to that same main Looper (so our post, enqueued
        // after startListening() returned, cannot run before the framework's own
        // queued hand-off does), not on elapsed wall-clock time. This is the same
        // ordering guarantee RecognizerPipeProbeTest's ERROR:5 lesson is about; it
        // has held in practice for all three paths, but only the terminal-callback
        // path is actually PROVEN rather than inferred from Looper ordering.
        try {
            cycle.readFd.close()
        } catch (io: IOException) {
            Log.w(TAG, "tl ev=pfd.readFdCloseFailed msg=${io.message}")
        }

        try {
            cycle.writerThread.join(WRITER_JOIN_TIMEOUT_MS)
        } catch (interrupted: InterruptedException) {
            Thread.currentThread().interrupt()
        }
        // The authoritative "nobody was reading" signal for PfdFeedPolicy: with
        // readFd already closed above, a writer that is STILL alive after the join
        // can only mean a genuine second reader (the recognizer's own Binder-duplicated
        // fd) is still holding the pipe open without draining it -- our own reference
        // is gone, so this is not an artifact of our own cleanup ordering. A stuck
        // writer means the cycle is dead regardless of how many bytes the kernel pipe
        // buffer silently absorbed (see PfdFeedPolicy.MIN_CONSUMED_BYTES's doc). Note
        // WRITER_JOIN_TIMEOUT_MS therefore bounds two different things at once: how
        // long this call can stall the main thread, AND how long we wait before
        // calling the writer "stuck". `isAlive` is broader than "blocked inside
        // write()" -- it is also true while the thread is merely runnable but not yet
        // scheduled, so the realistic false positive here is NOT a recognizer being
        // slow to close its dup (a writer can only actually block once the pipe's
        // ~64KB kernel buffer is full, which a recognizer that closes its dup
        // reasonably promptly never causes); it is the daemon writer thread simply
        // not being scheduled onto a CPU within 100ms under system load. The duration
        // gate below is what keeps that cheap either way: this signal only feeds a
        // verdict at all on cycles that already ran >= 3s with no partial/result, and
        // a healthy short cycle with a merely-delayed writer is not going to also
        // produce a 3-second silent one on top of it.
        val writerStuck = cycle.writerThread.isAlive
        if (writerStuck) {
            // The thread is a daemon, so it cannot outlive the process, and its own
            // `.use` block still closes writeFd whenever the write unblocks or the
            // pipe breaks -- a documented, bounded risk, not a hang in this call.
            Log.w(TAG, "tl ev=pfd.writerJoinTimeout gen=${cycle.gen}")
        }

        // Teardown (this whole function) stays synchronous on the main thread
        // rather than backgrounding the join + policy call. The stronger reason is
        // ORDERING, not just PfdFeedPolicy's fields being non-atomic (a background
        // join + main.post { policy.onCycleEnd(...) } would keep policy touched
        // only from the main thread too, and so would not by itself be unsafe):
        // endCurrentPfdCycle() runs at the very top of listen(), and
        // activePolicy.shouldUsePfd() is read four lines later in the SAME
        // function. If this cycle's onCycleEnd() were marshalled to run later via
        // main.post{}, that shouldUsePfd() read could run BEFORE the marshalled
        // onCycleEnd() -- listen() would silently decide PFD-vs-legacy for the new
        // cycle against a stale policy state and skip a cycle's worth of fail-streak
        // accounting, with no error or log to reveal it happened.
        val elapsedMs = SystemClock.elapsedRealtime() - cycle.startedAtElapsedMs
        policy?.onCycleEnd(hadPartial, hadResult, cycle.bytesWritten.get(), writerStuck, elapsedMs)
        // Observability escape hatch for the trade INCONCLUSIVE buys (fix round 3,
        // minor a): a recognizer that accepts our fd but never drains it, on a
        // device whose cycles also happen to always end short of
        // PfdFeedPolicy.JUDGE_AFTER_MS, would sit INCONCLUSIVE forever --
        // shouldUsePfd() never flips to legacy, so STT stays silently deaf for the
        // rest of the call with nothing in the log calling attention to it. This
        // check does NOT touch the fail streak or the mode (PfdFeedPolicy already
        // left both alone for every INCONCLUSIVE cycle); it only logs once per run
        // of 5 (the `== 5` check, not `>= 5`, is what makes it "once per run" --
        // the count must drop back to 0 via an ALIVE/DEAD verdict before it can
        // climb back up and log again).
        if (policy?.consecutiveInconclusiveCount() == 5) {
            Log.w(TAG, "tl ev=pfd.inconclusiveRun n=5")
        }
        Log.i(TAG, "tl ev=pfd.cycle ${policy?.cycleSummary()} drops=${cycle.drops.get()}")
    }

    private fun logFallbackOnce(tap: PcmTapSource?, activePolicy: PfdFeedPolicy?) {
        if (loggedFallback) return
        loggedFallback = true
        // Three distinct reasons this call takes the legacy-intent branch -- only
        // the last one is an actual policy flip; the middle one (tap present but no
        // policy yet) is listen() racing ahead of start()'s main.post{} completing,
        // or landing after destroy() cleared policy -- not a PfdFeedPolicy decision
        // at all, so it must not be reported as "policy_flipped".
        val reason = when {
            tap == null -> "no_tap_source"
            activePolicy == null -> "policy_not_ready"
            else -> "policy_flipped"
        }
        Log.i(TAG, "tl ev=pfd.fallback reason=$reason")
    }

    private companion object {
        const val TAG = "AndroidSpeechToText"

        // Frames buffered between the tap (mic capture thread) and the writer
        // (pipe I/O thread) before offer() starts rejecting and counting drops.
        const val PFD_QUEUE_CAPACITY = 32

        // Bounds TWO different things at once (see the writerStuck comment in
        // endCurrentPfdCycle() for the second): how long endCurrentPfdCycle() can
        // stall the MAIN thread waiting for the writer to finish (it runs inside
        // onResults()/onError(), both main-thread callbacks -- see the class doc on
        // why cycle teardown stays on the main thread rather than moving to a
        // background executor), and how long we wait before reporting the writer as
        // "stuck" to PfdFeedPolicy. Kept low rather than the original 500ms: after
        // the readFd-before-join fix above, the writer finishes in low single-digit
        // milliseconds in the common case (queue drains fast; a healthy recognizer
        // either already drained everything or our readFd close unblocks a stalled
        // write via EPIPE), so 100ms is generous headroom for that path while still
        // bounding the rare truly-wedged-write case instead of reproducing the
        // original 500ms-per-turn stall.
        const val WRITER_JOIN_TIMEOUT_MS = 100L

        // Sentinel compared by reference (===), never by content -- a real captured
        // frame is never this exact array instance, so there is no collision risk
        // even though both are plain ByteArrays.
        val POISON_PILL = ByteArray(0)

        // Endpointing tuning (prod complaint 2026-07-31: "birkaç kelime sonra
        // cümle yarıda kesiliyor"). The platform defaults finalize an utterance
        // after a short pause, which chops Turkish speech at every breath.
        // These ask the recognizer to tolerate natural sentence-internal
        // pauses before declaring end-of-speech. They are hints, not
        // guarantees: Soda respects them, some network recognizers ignore them.
        const val MIN_SPEECH_MS = 2000L
        const val COMPLETE_SILENCE_MS = 1500L
        const val MAYBE_COMPLETE_SILENCE_MS = 1500L

        val recognizeIntent: Intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
            )
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "tr-TR")
            // Ask for the offline recognizer; platforms without one silently fall back
            // to the network recognizer, which is still better than failing the call.
            putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            // NOT setting RecognizerIntent.EXTRA_AUDIO_SOURCE here is deliberate -- this
            // is the BASE intent shared by both the legacy fallback path and, via
            // Intent(recognizeIntent) copies in buildPfdCycleIntent(), the PFD path.
            // That extra takes a ParcelFileDescriptor pointing at an already-open audio
            // source; we were once passing MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            // an Int, which the framework silently ignores. The sibling constants
            // (EXTRA_AUDIO_SOURCE_CHANNEL_COUNT / _ENCODING / _SAMPLING_RATE) confirm
            // the shape -- a bare AudioSource int would need none of them.
            //
            // So the recognizer used to always open its own microphone with its own
            // default source, and the STT leg of the AEC chain was never established.
            // The comment removed here once claimed the opposite and is why the 5 Aug
            // review recorded this as done. Feeding the recognizer from our single
            // AudioRecord via ParcelFileDescriptor.createPipe() is the real fix,
            // confirmed working by RecognizerPipeProbeTest (Task 7 of
            // docs/superpowers/plans/2026-08-11-ses-kimligi-pixel-dogrulugu.md) and
            // implemented for real recognizer cycles by buildPfdCycleIntent() above
            // (Task 3 of docs/superpowers/plans/2026-08-11-tek-audiorecord-pfd.md).
            // This base intent stays PFD-extra-free on purpose: it is also exactly what
            // a flipped-to-legacy or tapSource == null instance sends, verbatim.
            //
            // Bias the recognizer towards the words it keeps getting wrong. Kadir said
            // "selam Jarvis nasılsın" and the transcript read "selam CEVİZ nasılsın"
            // (S23, 2026-08-03) -- a Turkish recognizer has no reason to expect an
            // English name, and the assistant's own name being unrecognisable is not a
            // cosmetic problem when it is the wake word of every sentence.
            //
            // API 33+ (verified against the installed android-36 SDK, not assumed).
            // AOSP documents it as "Optional list of strings, towards which the
            // recognizer should bias the recognition results" but does NOT document the
            // extra's value type, and the sibling constant carries an explicit
            // "may have no effect depending on the recognizer implementation". So this
            // is a best-effort hint sent as an ArrayList (the accessor Android pairs
            // with "list"), and whether it lands is a DEVICE measurement, not a claim.
            if (Build.VERSION.SDK_INT >= 33) {
                putStringArrayListExtra(
                    RecognizerIntent.EXTRA_BIASING_STRINGS,
                    arrayListOf("Jarvis", "Cârvis", "Kadir"),
                )
                putExtra(RecognizerIntent.EXTRA_ENABLE_BIASING_DEVICE_CONTEXT, true)
            }
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS, MIN_SPEECH_MS)
            putExtra(
                RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS,
                COMPLETE_SILENCE_MS,
            )
            putExtra(
                RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS,
                MAYBE_COMPLETE_SILENCE_MS,
            )
        }

        // Shared by createRecognizer() and start()'s usingOnDeviceRecognizer bookkeeping
        // so the two never drift apart into disagreeing about which recognizer a given
        // start() actually built.
        fun isOnDeviceRecognizerAvailable(context: Context): Boolean =
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                SpeechRecognizer.isOnDeviceRecognitionAvailable(context)

        fun createRecognizer(context: Context): SpeechRecognizer =
            if (isOnDeviceRecognizerAvailable(context)) {
                SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
            } else {
                SpeechRecognizer.createSpeechRecognizer(context)
            }
    }
}

/**
 * Bookkeeping for one PFD feed cycle -- torn down exactly once by
 * [AndroidSpeechToText.endCurrentPfdCycle]. [readFd] is this class's own copy of the
 * pipe's read end (a separate copy is duplicated across Binder for the recognizer
 * itself when the intent carrying it is delivered); [writeFd] is owned entirely by
 * [writerThread] and is not held here -- it is closed by the writer's own `.use` block,
 * never from the main thread. [startedAtElapsedMs] is stamped at construction
 * (`SystemClock.elapsedRealtime()`) so teardown can compute the cycle's wall-clock
 * duration for [PfdFeedPolicy]'s duration gate.
 */
private class PfdCycle(
    val gen: Int,
    val readFd: ParcelFileDescriptor,
    val queue: ArrayBlockingQueue<ByteArray>,
    val writerThread: Thread,
    val bytesWritten: AtomicLong,
    val drops: AtomicLong,
    val startedAtElapsedMs: Long,
)

/**
 * Maps a `SpeechRecognizer.onError` code to the session's restart policy. Timeout and
 * no-match are the normal silence outcomes of an open mic, and RECOGNIZER_BUSY is a
 * transient service race -- all three just re-arm listening. Everything else (no
 * service, permission loss, client/network errors) is treated as call-fatal so the
 * overlay can show a Turkish error instead of looping forever.
 *
 * Pure Kotlin over compile-time-constant error codes on purpose, so the mapping itself
 * is unit-testable on the JVM (SttErrorMappingTest).
 */
fun isRecoverableSttError(error: Int): Boolean = when (error) {
    SpeechRecognizer.ERROR_SPEECH_TIMEOUT,
    SpeechRecognizer.ERROR_NO_MATCH,
    SpeechRecognizer.ERROR_RECOGNIZER_BUSY,
    -> true
    else -> false
}

/** What [AndroidSpeechToText.onError] does with a given verdict from [sttErrorAction]. */
enum class SttErrorAction {
    /** Re-arm listening on the SAME recognizer -- the existing recoverable set. */
    Retry,

    /** Tear down the on-device recognizer and rebuild on the network one, then re-arm
     *  listening for the same turn -- see [AndroidSpeechToText]'s class doc. */
    FallbackToNetwork,

    /** End the call with a user-visible error -- everything else. */
    Fatal,
}

/**
 * Superset of [isRecoverableSttError] that also knows which recognizer just failed.
 * `SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE` (13) means the recognizer's language
 * pack -- here, tr-TR -- is supported but not currently installed. Measured on
 * emulator-5556 (API 36): the on-device SODA pack is absent, so the on-device
 * recognizer fails EVERY call with this code, and code 13 is not in
 * [isRecoverableSttError]'s recoverable set, so without this function it always
 * reached [SttErrorAction.Fatal].
 *
 * The fallback is deliberately narrow: only ERROR_LANGUAGE_UNAVAILABLE, and only while
 * [usingOnDeviceRecognizer] is true. The network recognizer downloads its language model
 * server-side rather than depending on an on-device pack, so it is a genuinely different
 * failure surface worth trying once. If the SAME code then arrives from the network
 * recognizer, there is nowhere left to fall back to -- that IS fatal, which is exactly
 * what falls out of this `when` once [AndroidSpeechToText.usingOnDeviceRecognizer] has
 * been flipped to false by the first fallback (see [AndroidSpeechToText.fallbackToNetworkRecognizer]).
 * Every other code keeps [isRecoverableSttError]'s existing recoverable/fatal split
 * unchanged by BOTH values of [usingOnDeviceRecognizer] -- this function must not change
 * behaviour for any code besides 13.
 *
 * Deliberately NOT included: ERROR_SERVER_DISCONNECTED (11) -- that is a network-recognizer
 * failure (the streaming connection dropped), which on-device never raises and which a
 * network-to-network "fallback" cannot fix; ERROR_LANGUAGE_NOT_SUPPORTED (12) -- per the
 * platform docs this means the recognizer does not support the language AT ALL (not "not
 * installed"), a stronger claim than 13, and Android's own doc for it does not carry 13's
 * "supported, but not available currently (e.g. not downloaded)" language, so treating it
 * the same would be an unverified guess rather than the narrowly-scoped fix asked for.
 *
 * Pure Kotlin over compile-time-constant error codes on purpose, so the mapping itself is
 * unit-testable on the JVM (SttErrorMappingTest), exactly like [isRecoverableSttError].
 */
fun sttErrorAction(errorCode: Int, usingOnDeviceRecognizer: Boolean): SttErrorAction = when {
    isRecoverableSttError(errorCode) -> SttErrorAction.Retry
    errorCode == SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE && usingOnDeviceRecognizer ->
        SttErrorAction.FallbackToNetwork
    else -> SttErrorAction.Fatal
}
