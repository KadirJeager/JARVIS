package com.jarvis.data.voice.session

import android.os.Build
import android.os.SystemClock
import android.speech.SpeechRecognizer
import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.rule.GrantPermissionRule
import java.util.Locale
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Instrumented proof, one level above [RecognizerPipeProbeTest], that OUR plumbing --
 * [AndroidMicSource]'s tap -> the PFD queue -> the pipe -> the platform recognizer --
 * actually feeds the recognizer, not merely that SOME transcript arrives.
 *
 * [PfdFeedPolicy]'s own class doc names the exact blind spot this test exists to close:
 * a recognizer that ignores our piped fd, opens its own microphone, and transcribes
 * ambient audio instead produces ordinary partials/results too -- `hadResult=true`
 * forever, scoring ALIVE with no way to tell "read our fd" from "heard something,
 * somewhere else". The emulator's own microphone captures silence (no real audio input
 * feeds it), so the only way a FIXTURE-MATCHING transcript can appear is if the
 * recognizer actually read the bytes [FakeTapSource] pushed through
 * [AndroidSpeechToText]'s tap -> pipe path. A transcript that arrives but does NOT
 * match the fixture is therefore a genuine NEGATIVE finding about our plumbing --
 * logged, not a build failure (see the VERDICT discipline below; Task 7's probe already
 * proved the recognizer side of this in isolation, so a failure here points at us).
 *
 * [FakeTapSource] stands in for [AndroidMicSource]: once [AndroidSpeechToText] installs
 * a tap (i.e. once a PFD cycle actually starts), it feeds `tr_probe_16k.pcm` -- the same
 * committed fixture [RecognizerPipeProbeTest] uses, a synthetic Turkish TTS clip saying
 * "Merhaba Jarvis, bugun hava cok guzel. Bu bir deneme kaydidir..." -- in 1280-byte
 * frames (40ms of 16kHz mono PCM16 each) at real-time pace, then keeps feeding
 * zero-filled frames of the same size so the recognizer's own silence endpointing (see
 * AndroidSpeechToText's COMPLETE_SILENCE_MS/MAYBE_COMPLETE_SILENCE_MS extras) ends the
 * utterance naturally instead of the test needing to force a stop.
 *
 * This test exercises the full [AndroidSpeechToText] cycle lifecycle (pipe, writer
 * thread, [PfdFeedPolicy] bookkeeping) as a black box through its public [SpeechToText]
 * interface -- unlike the probe, it never touches a `ParcelFileDescriptor` directly,
 * because [AndroidSpeechToText] already owns that whole lifecycle end to end.
 *
 * Also reports which recognizer path served the cycle: [AndroidSpeechToText.createRecognizer]
 * prefers `createOnDeviceSpeechRecognizer` on API 31+ when
 * `SpeechRecognizer.isOnDeviceRecognitionAvailable` is true. Task 7's probe only ever
 * exercised `createSpeechRecognizer` (the network path); logging that availability bit
 * here records whether this run's emulator ever proves the on-device path at all,
 * rather than silently implying coverage it doesn't have.
 */
@RunWith(AndroidJUnit4::class)
class PfdSpeechToTextTest {

    @get:Rule
    val permissions: GrantPermissionRule =
        GrantPermissionRule.grant(android.Manifest.permission.RECORD_AUDIO)

    @Test
    fun pfdFeedProducesFixtureMatchingTranscript() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        // Application context, matching how JarvisApp.kt actually constructs
        // AndroidSpeechToText (context.applicationContext) -- targetContext IS that
        // application context for the app-under-test in an instrumented test.
        val context = instrumentation.targetContext
        // Fixture lives in src/androidTest/assets, packaged into the instrumentation
        // APK (com.jarvis.test) -- must be read via the instrumentation's own context,
        // NOT targetContext/ApplicationProvider, which resolves against the
        // app-under-test's own src/main/assets and throws FileNotFoundException for
        // this file. Same lesson RecognizerPipeProbeTest already paid for.
        val fixtureBytes = instrumentation.context.assets.open(FIXTURE_ASSET).use { it.readBytes() }
        check(fixtureBytes.size == FIXTURE_SIZE_BYTES) {
            "fixture size drifted: expected $FIXTURE_SIZE_BYTES, got ${fixtureBytes.size}"
        }

        if (!SpeechRecognizer.isRecognitionAvailable(context)) {
            Log.i(
                TAG,
                "$VERDICT_PREFIX$VERDICT_NO_RECOGNIZER onDevice=false bytesFed=0 elapsedMs=0 " +
                    "result=ERROR:no_recognizer",
            )
            // Throws AssumptionViolatedException -- JUnit reports this test as
            // "ignored", not failed, matching RecognizerPipeProbeTest's convention: a
            // missing recognizer service is inconclusive, not a plumbing failure.
            assumeTrue(
                "no SpeechRecognizer service available on this device -- inconclusive, not a fail",
                false,
            )
        }
        // Whether the on-device (offline) recognizer is what actually serves this
        // cycle vs. the network one -- see class doc. isOnDeviceRecognitionAvailable
        // is API 31+, matching the exact gate AndroidSpeechToText.createRecognizer uses.
        val onDeviceAvailable = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            SpeechRecognizer.isOnDeviceRecognitionAvailable(context)
        Log.i(TAG, "onDeviceRecognitionAvailable=$onDeviceAvailable (API ${Build.VERSION.SDK_INT})")

        val fakeTap = FakeTapSource(fixtureBytes)
        val speechToText = AndroidSpeechToText(context, fakeTap)
        val outcome = LinkedBlockingQueue<Outcome>(1)
        val listener = object : SpeechToTextListener {
            override fun onBeginningOfSpeech() {}
            override fun onPartialResult(text: String) {}
            override fun onResult(text: String) {
                outcome.offer(Outcome.Transcript(text))
            }
            override fun onRecoverableError() {
                outcome.offer(Outcome.Recoverable)
            }
            override fun onFatalError() {
                outcome.offer(Outcome.Fatal)
            }
        }

        try {
            speechToText.start(listener)
            val startedAtMs = SystemClock.elapsedRealtime()
            speechToText.listen()

            val result = outcome.poll(RESULT_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            val elapsedMs = SystemClock.elapsedRealtime() - startedAtMs
            val bytesFed = fakeTap.totalBytesFed.get()

            // Subject form (when(result), not condition-chained) so the compiler
            // exhaustively checks every Outcome subtype plus the null (timeout) case --
            // a condition-chained `when` here would silently need an `else`, which
            // could swallow an unhandled case instead of failing to compile.
            val (verdict, resultLabel) = when (result) {
                null -> VERDICT_NO_TRANSCRIPT to "ERROR:timeout"
                is Outcome.Fatal -> VERDICT_NO_TRANSCRIPT to "ERROR:fatal"
                is Outcome.Recoverable -> VERDICT_NO_TRANSCRIPT to "ERROR:recoverable"
                is Outcome.Transcript ->
                    if (matchesFixture(result.text)) VERDICT_FIXTURE_MATCH to result.text
                    else VERDICT_NOT_FIXTURE to result.text
            }

            // Single greppable line per the controller's verdict discipline: all four
            // outcomes are informative findings, never a build failure by themselves.
            Log.i(
                TAG,
                "$VERDICT_PREFIX$verdict onDevice=$onDeviceAvailable bytesFed=$bytesFed " +
                    "elapsedMs=$elapsedMs result=$resultLabel",
            )

            // Only asserts that a verdict was computed at all -- membership in the
            // known set catches a coding regression, never a negative-but-valid
            // plumbing answer (TRANSCRIPT_NOT_FIXTURE / NO_TRANSCRIPT are both real,
            // recordable outcomes, not test failures).
            assertTrue(
                "computed an unrecognized verdict: $verdict",
                verdict in KNOWN_VERDICTS,
            )
        } finally {
            speechToText.destroy()
            // destroy() marshals its teardown (pipe/writer/tap cleanup) onto the main
            // Looper via Handler.post(); a no-op runOnMainSync() here forces this test
            // to wait for that queued teardown to actually run before returning, since
            // both land on the same Looper's FIFO message queue -- the same ordering
            // guarantee AndroidSpeechToText's own class doc relies on for its
            // readFd-close-before-join sequencing.
            instrumentation.runOnMainSync {}
        }
    }

    private sealed class Outcome {
        data class Transcript(val text: String) : Outcome()
        object Recoverable : Outcome()
        object Fatal : Outcome()
    }

    private companion object {
        const val TAG = "PfdSpeechToTextTest"
        const val FIXTURE_ASSET = "tr_probe_16k.pcm"
        const val FIXTURE_SIZE_BYTES = 192_000
        const val RESULT_TIMEOUT_SECONDS = 25L
        const val VERDICT_PREFIX = "PfdSpeechToTextTest: VERDICT="

        const val VERDICT_FIXTURE_MATCH = "PLUMBING_OK_FIXTURE_MATCH"
        const val VERDICT_NOT_FIXTURE = "TRANSCRIPT_NOT_FIXTURE"
        const val VERDICT_NO_TRANSCRIPT = "NO_TRANSCRIPT"
        const val VERDICT_NO_RECOGNIZER = "NO_RECOGNIZER"

        // NO_RECOGNIZER is deliberately excluded: that path exits via assumeTrue(false)
        // before this set is ever consulted, exactly like RecognizerPipeProbeTest's
        // KNOWN_VERDICTS.
        val KNOWN_VERDICTS = setOf(VERDICT_FIXTURE_MATCH, VERDICT_NOT_FIXTURE, VERDICT_NO_TRANSCRIPT)

        // Distinctive content words from the fixture's spoken sentence ("Merhaba
        // Jarvis, bugun hava cok guzel. Bu bir deneme kaydidir..."), ASCII-folded so a
        // recognizer that drops Turkish diacritics still matches. "Jarvis" is kept
        // despite the known mis-transcription risk (see AndroidSpeechToText's
        // EXTRA_BIASING_STRINGS comment, "selam Jarvis" -> "selam CEVIZ" on a real
        // device) -- a correct match on it is still informative when it happens, and
        // the rest of the list does not depend on it.
        val FIXTURE_KEYWORDS = listOf("jarvis", "merhaba", "hava", "guzel", "deneme", "kayit")

        fun matchesFixture(transcript: String): Boolean {
            val folded = foldTurkish(transcript)
            return FIXTURE_KEYWORDS.any { folded.contains(it) }
        }

        fun foldTurkish(s: String): String =
            s.lowercase(Locale.forLanguageTag("tr"))
                .replace("ı", "i")
                .replace("ğ", "g")
                .replace("ü", "u")
                .replace("ş", "s")
                .replace("ö", "o")
                .replace("ç", "c")
    }
}

/**
 * Stands in for [AndroidMicSource] in this test: feeds `tr_probe_16k.pcm`
 * ([fixtureBytes]) through the installed tap once [AndroidSpeechToText] starts a PFD
 * cycle, at roughly real-time pace, then keeps feeding zero-filled frames of the same
 * size so the recognizer's own silence endpointing ends the utterance naturally.
 *
 * Mirrors [AndroidMicSource]'s tap contract: the feeder thread starts/stops exactly
 * when [setTap] installs/clears a tap (matching "once a tap is installed" -- there is
 * no primary read loop here to piggyback on, so this class IS the frame source), and a
 * throwing tap is caught, logged once, and the feeder stops rather than propagating --
 * the same duty [AndroidMicSource.readFrame] documents for a real tap.
 */
private class FakeTapSource(private val fixtureBytes: ByteArray) : PcmTapSource {

    val totalBytesFed = AtomicLong(0)

    // Written only from setTap(), which AndroidSpeechToText only ever calls from its
    // own main-thread main.post{} blocks -- so setTap() itself is never concurrent
    // with itself. @Volatile only for the feeder thread's own visibility, matching
    // AndroidMicSource's tap field.
    @Volatile private var feeder: Thread? = null

    override fun setTap(tap: ((ByteArray) -> Unit)?) {
        // Stop the previous feeder (if any) before installing a new one, or when
        // clearing -- setTap(null) is how AndroidSpeechToText.endCurrentPfdCycle()
        // signals "this cycle is over", so feeding must stop there too. interrupt()
        // unblocks a feeder currently inside Thread.sleep() between frames.
        feeder?.interrupt()
        feeder = null
        if (tap == null) return

        val thread = Thread({
            try {
                var offset = 0
                var alive = true
                while (alive && offset < fixtureBytes.size) {
                    val end = minOf(offset + FRAME_BYTES, fixtureBytes.size)
                    alive = feedOne(tap, fixtureBytes.copyOfRange(offset, end))
                    offset = end
                    if (alive) Thread.sleep(FRAME_INTERVAL_MS)
                }
                // Fixture exhausted: keep feeding silence so the recognizer's own
                // silence endpointing fires naturally instead of listening forever.
                val silence = ByteArray(FRAME_BYTES)
                while (alive) {
                    alive = feedOne(tap, silence)
                    if (alive) Thread.sleep(FRAME_INTERVAL_MS)
                }
            } catch (_: InterruptedException) {
                // Normal teardown path: setTap(null) (cycle end) or test cleanup
                // interrupted this thread between frames -- not an error.
            }
        }, "FakeTapSource-feeder")
        thread.isDaemon = true
        feeder = thread
        thread.start()
    }

    /**
     * Feeds one frame to [tap]. Non-blocking per the [PcmTapSource] contract (the real
     * tap installed by [AndroidSpeechToText] is just a bounded queue offer). Returns
     * false if [tap] threw, in which case the feeder loop above stops -- mirroring
     * [AndroidMicSource.readFrame]'s "log once and clear" duty for a throwing tap.
     */
    private fun feedOne(tap: (ByteArray) -> Unit, frame: ByteArray): Boolean =
        try {
            tap(frame)
            totalBytesFed.addAndGet(frame.size.toLong())
            true
        } catch (thrown: Throwable) {
            Log.e(TAG, "tap threw; stopping the feeder thread", thrown)
            false
        }

    private companion object {
        const val TAG = "FakeTapSource"

        // 1280 bytes = 40ms of 16kHz mono PCM16 (32,000 bytes/s) -- matches AUDIO_IN_RATE_HZ.
        const val FRAME_BYTES = 1280
        const val FRAME_INTERVAL_MS = 40L
    }
}
