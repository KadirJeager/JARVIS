package com.jarvis.data.voice.session

import android.content.Context
import android.content.Intent
import android.media.AudioFormat
import android.os.Bundle
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.rule.GrantPermissionRule
import java.io.IOException
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Probe (not a feature): answers one question and writes it down -- does the platform
 * recognizer accept a [ParcelFileDescriptor] audio feed for tr-TR on this device? If it does,
 * one `AudioRecord` can feed STT + speaker-ID + VAD from the same AEC-processed signal. If it
 * does not, the two-capture-client design stays and the mic contention has to be answered
 * another way (docs/superpowers/plans/2026-08-11-ses-kimligi-pixel-dogrulugu.md, Task 7).
 *
 * Fixture provenance: `tr_probe_16k.pcm` (192,000 bytes = 6.0 s of 16 kHz mono PCM16) is a
 * Turkish gTTS voice saying "Merhaba Jarvis, bugun hava cok guzel. Bu bir deneme kaydidir...".
 * It is a synthetic TTS clip, not a recording of a person -- generated purely to give the
 * recognizer real speech content instead of silence or noise.
 *
 * Mechanical discriminator: the OS pipe buffer is ~64 KB; the fixture is ~3x that. The writer
 * thread below can only finish writing all 192,000 bytes if something on the other end
 * (the recognizer) actually reads the pipe. If the recognizer never touches the piped fd, the
 * writer blocks forever after the first ~64 KB and `writerCompleted` stays false -- that is what
 * separates "recognizer read the pipe but found nothing intelligible" from "recognizer ignored
 * the pipe outright" in a way ERROR codes alone cannot, because a recognizer that ignores the
 * EXTRA_AUDIO_SOURCE extra typically falls back to the real microphone and still produces an
 * ordinary timeout error from listening to emulator silence.
 *
 * Deviations from the task brief's skeleton, made while fitting it to the real SDK:
 *  - Every `SpeechRecognizer` call (create, setRecognitionListener, startListening, destroy) is
 *    marshalled onto the main thread via `runOnMainSync`. The platform docs require this for
 *    all SpeechRecognizer methods, not just startListening; the brief's skeleton constructed
 *    the recognizer off the main thread.
 *  - EXTRA_LANGUAGE_MODEL is set to LANGUAGE_MODEL_FREE_FORM, matching how the app's real
 *    recognize intent (AndroidSpeechToText) and the platform docs build a well-formed request --
 *    the brief's skeleton omitted it, and an incomplete intent would risk a false negative
 *    unrelated to the actual question being probed.
 *  - Availability is gated with `SpeechRecognizer.isRecognitionAvailable` before any of this
 *    runs; if there is no recognizer on the device, the test short-circuits via
 *    `assumeTrue(false, ...)` (JUnit "ignored", not a red X) after logging the verdict, per the
 *    controller's instruction that a misleading failure is worse than an honest inconclusive.
 *  - `readFd` (the pipe end handed to the recognizer via EXTRA_AUDIO_SOURCE) is a
 *    ParcelFileDescriptor sent across Binder inside `startListening()`, which duplicates it for
 *    the receiving process -- our own copy is no longer needed once the session concludes, and
 *    is closed exactly once in the `finally` block (not eagerly right after `startListening()`
 *    returns: that call queues the actual IPC through SpeechRecognizer's own internal Handler
 *    rather than performing it synchronously, so an eager close can race ahead of the real
 *    hand-off -- see the inline comment at the call site). Both pipe ends and the recognizer are
 *    covered by a try/finally so an exception anywhere in setup (e.g. `startListening` itself
 *    throwing) still cleans up instead of leaking fds or a live, un-destroyed `SpeechRecognizer`.
 *  - The assertion is on the *verdict*, not on `result`. The brief's skeleton asserted
 *    `assertNotNull(result)`, but `result` is legitimately null for two of this probe's real,
 *    non-failing outcomes (`PFD_IGNORED`, and `PFD_CONSUMED_NO_TRANSCRIPT` when no error
 *    callback ever fires) -- asserting on it would fail the build on exactly the negative
 *    answers this probe exists to record. The verdict computed below is always one of a known
 *    set by construction; asserting membership in that set catches only a genuine coding
 *    regression, never a negative-but-valid answer.
 */
@RunWith(AndroidJUnit4::class)
class RecognizerPipeProbeTest {

    @get:Rule
    val permissions: GrantPermissionRule =
        GrantPermissionRule.grant(android.Manifest.permission.RECORD_AUDIO)

    @Test
    fun recognizerAcceptsPipedAudioSource() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        // The fixture lives in src/androidTest/assets, which AGP packages into the
        // instrumentation APK (com.jarvis.test), not the app-under-test APK. The app context
        // above (ApplicationProvider) resolves assets against the app-under-test's own APK
        // (src/main/assets) and would throw FileNotFoundException for this file -- the asset
        // must be opened via the instrumentation's own context instead.
        val testContext = instrumentation.context

        if (!SpeechRecognizer.isRecognitionAvailable(context)) {
            Log.i(TAG, "VERDICT=$VERDICT_NO_RECOGNIZER bytesWritten=0 writerDone=false result=null")
            // Throws AssumptionViolatedException -- JUnit reports this test as "ignored", not
            // failed. Nothing below this line runs, so there is no resource to clean up yet.
            assumeTrue(
                "no SpeechRecognizer service available on this device -- inconclusive, not a fail",
                false,
            )
        }

        // Declared outside the try so the finally block can reach them from any exit path,
        // including an exception thrown mid-setup (e.g. startListening() itself failing).
        var readFd: ParcelFileDescriptor? = null
        var writeFd: ParcelFileDescriptor? = null
        val recognizerRef = arrayOfNulls<SpeechRecognizer>(1)

        try {
            val pipe = ParcelFileDescriptor.createPipe()
            val localReadFd = pipe[0]
            val localWriteFd = pipe[1]
            readFd = localReadFd
            writeFd = localWriteFd

            val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(
                    RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                    RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
                )
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, "tr-TR")
                putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE, localReadFd)
                putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_SAMPLING_RATE, 16000)
                putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_CHANNEL_COUNT, 1)
                putExtra(
                    RecognizerIntent.EXTRA_AUDIO_SOURCE_ENCODING,
                    AudioFormat.ENCODING_PCM_16BIT,
                )
            }

            // Holds either a transcript, or "ERROR:<code>" from onError -- either way, one item
            // is exactly the recorded outcome of this probe.
            val transcript = LinkedBlockingQueue<String>(1)

            val recognitionListener = object : RecognitionListener {
                override fun onPartialResults(partial: Bundle) {
                    partial.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                        ?.firstOrNull()?.let { transcript.offer(it) }
                }
                override fun onResults(results: Bundle) {
                    results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                        ?.firstOrNull()?.let { transcript.offer(it) }
                }
                override fun onError(error: Int) {
                    transcript.offer("ERROR:$error")
                }
                override fun onReadyForSpeech(params: Bundle?) {}
                override fun onBeginningOfSpeech() {}
                override fun onRmsChanged(rmsdB: Float) {}
                override fun onBufferReceived(buffer: ByteArray?) {}
                override fun onEndOfSpeech() {}
                override fun onEvent(eventType: Int, params: Bundle?) {}
            }

            instrumentation.runOnMainSync {
                val r = SpeechRecognizer.createSpeechRecognizer(context)
                r.setRecognitionListener(recognitionListener)
                // Assigned BEFORE startListening() so that if startListening() throws, the
                // finally block below can still find and destroy() this recognizer instead of
                // leaking a live one.
                recognizerRef[0] = r
                r.startListening(intent)
            }

            // Deliberately NOT closed here. SpeechRecognizer.startListening() dispatches the
            // actual Binder call to the recognition service through its own internal Handler on
            // this same main Looper -- it queues the real IPC rather than performing it before
            // returning, so runOnMainSync() returning is not proof the fd has been handed off
            // yet. Closing localReadFd eagerly here raced ahead of that queued dispatch in
            // testing (observed: an instant ERROR_CLIENT(5)/EPIPE on a cold app-process start,
            // with no RecognitionServiceImpl log at all -- the fd was invalid before the service
            // was ever reached). readFd is instead closed exactly once, in the finally block
            // below, after the recognition session has actually concluded.
            //
            // Writer thread: pushes the fixture into the pipe and closes it so the recognizer
            // sees EOF. Daemon so a blocked write (the PFD_IGNORED case, where nothing ever
            // drains the pipe past the OS buffer) cannot wedge the instrumentation process
            // after the test ends.
            val bytesWritten = AtomicLong(0)
            val writerCompleted = AtomicBoolean(false)
            val writerStartMs = SystemClock.elapsedRealtime()
            val writerEndMs = AtomicLong(-1)
            val writer = Thread({
                try {
                    ParcelFileDescriptor.AutoCloseOutputStream(localWriteFd).use { out ->
                        testContext.assets.open(FIXTURE_ASSET).use { input ->
                            val buffer = ByteArray(8 * 1024)
                            var read = input.read(buffer)
                            while (read != -1) {
                                out.write(buffer, 0, read)
                                bytesWritten.addAndGet(read.toLong())
                                read = input.read(buffer)
                            }
                        }
                    }
                    writerCompleted.set(true)
                    writerEndMs.set(SystemClock.elapsedRealtime())
                } catch (io: IOException) {
                    // A broken pipe here (recognizer closed its read end early, or the finally
                    // block below force-closed a still-blocked write) is itself a signal -- it
                    // read *something* then stopped -- not a bug in this probe.
                    Log.w(TAG, "writer thread stopped: ${io.message}")
                }
            }, "RecognizerPipeProbe-writer")
            writer.isDaemon = true
            writer.start()

            val result = transcript.poll(15, TimeUnit.SECONDS)
            // Give the writer a short extra window to settle into its final state (finished, or
            // durably blocked) once the recognizer has either answered or gone silent.
            writer.join(2_000)

            if (!writer.isAlive) {
                // The writer's own `use {}` block already closed localWriteFd on the way out,
                // whether it finished normally or hit an IOException -- nothing left to close.
                writeFd = null
            }

            val writerDone = writerCompleted.get()
            val writerMs = if (writerDone) writerEndMs.get() - writerStartMs else -1
            Log.i(
                TAG,
                "writer detail: bytesWritten=${bytesWritten.get()} of $FIXTURE_SIZE_BYTES " +
                    "writerDone=$writerDone tookMs=$writerMs",
            )

            val verdict = when {
                result != null && !result.startsWith("ERROR:") -> VERDICT_TRANSCRIPT
                writerDone -> VERDICT_NO_TRANSCRIPT
                else -> VERDICT_IGNORED
            }
            Log.i(
                TAG,
                "VERDICT=$verdict bytesWritten=${bytesWritten.get()} writerDone=$writerDone " +
                    "result=$result",
            )

            // The probe's product is the recorded VERDICT line above, so both outcomes are
            // written down rather than one of them failing the build. This only asserts that
            // the verdict computed above is one of the known, always-producible outcomes --
            // it must pass on PFD_IGNORED and PFD_CONSUMED_NO_TRANSCRIPT just as much as on
            // PFD_CONSUMED_TRANSCRIPT, since all three are valid recorded answers.
            assertTrue(
                "computed an unrecognized verdict: $verdict",
                verdict in KNOWN_VERDICTS,
            )
        } finally {
            try {
                instrumentation.runOnMainSync { recognizerRef[0]?.destroy() }
            } catch (e: Exception) {
                // Same reasoning as the fd closes below: a destroy() throw must not mask
                // the try-block's own outcome (verdict already computed and logged above)
                // or skip the fd closes that still need to run.
                Log.w(TAG, "cleanup: recognizer destroy failed: ${e.message}")
            }
            try {
                readFd?.close()
            } catch (io: IOException) {
                Log.w(TAG, "cleanup: readFd close failed: ${io.message}")
            }
            try {
                writeFd?.close()
            } catch (io: IOException) {
                Log.w(TAG, "cleanup: writeFd close failed: ${io.message}")
            }
        }
    }

    private companion object {
        const val TAG = "RecognizerPipeProbe"
        const val FIXTURE_ASSET = "tr_probe_16k.pcm"
        const val FIXTURE_SIZE_BYTES = 192_000

        const val VERDICT_TRANSCRIPT = "PFD_CONSUMED_TRANSCRIPT"
        const val VERDICT_NO_TRANSCRIPT = "PFD_CONSUMED_NO_TRANSCRIPT"
        const val VERDICT_IGNORED = "PFD_IGNORED"
        const val VERDICT_NO_RECOGNIZER = "NO_RECOGNIZER"

        val KNOWN_VERDICTS = setOf(VERDICT_TRANSCRIPT, VERDICT_NO_TRANSCRIPT, VERDICT_IGNORED)
    }
}
