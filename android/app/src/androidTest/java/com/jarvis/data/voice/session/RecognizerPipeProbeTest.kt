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
import org.junit.Assert.assertNotNull
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
            Log.i(
                TAG,
                "VERDICT=NO_RECOGNIZER bytesWritten=0 writerDone=false result=null",
            )
            assumeTrue(
                "no SpeechRecognizer service available on this device -- inconclusive, not a fail",
                false,
            )
            return
        }

        val (readFd, writeFd) = ParcelFileDescriptor.createPipe()
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "tr-TR")
            putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE, readFd)
            putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_SAMPLING_RATE, 16000)
            putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_CHANNEL_COUNT, 1)
            putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_ENCODING, AudioFormat.ENCODING_PCM_16BIT)
        }

        // Holds either a transcript, or "ERROR:<code>" from onError -- either way, one item is
        // exactly the recorded outcome of this probe.
        val transcript = LinkedBlockingQueue<String>(1)
        val recognizer = arrayOfNulls<SpeechRecognizer>(1)

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
            recognizer[0] = SpeechRecognizer.createSpeechRecognizer(context).apply {
                setRecognitionListener(recognitionListener)
                startListening(intent)
            }
        }

        // Writer thread: pushes the fixture into the pipe and closes it so the recognizer sees
        // EOF. Daemon so a blocked write (the PFD_IGNORED case, where nothing ever drains the
        // pipe past the OS buffer) cannot wedge the instrumentation process after the test ends.
        val bytesWritten = AtomicLong(0)
        val writerCompleted = AtomicBoolean(false)
        val writerStartMs = SystemClock.elapsedRealtime()
        val writerEndMs = AtomicLong(-1)
        val writer = Thread({
            try {
                ParcelFileDescriptor.AutoCloseOutputStream(writeFd).use { out ->
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
                // A broken pipe here (recognizer closed its read end early) is itself a
                // signal -- it read *something* then stopped -- not a bug in this probe.
                Log.w(TAG, "writer thread stopped: ${io.message}")
            }
        }, "RecognizerPipeProbe-writer")
        writer.isDaemon = true
        writer.start()

        val result = transcript.poll(15, TimeUnit.SECONDS)
        // Give the writer a short extra window to settle into its final state (finished, or
        // durably blocked) once the recognizer has either answered or gone silent.
        writer.join(2_000)

        val writerDone = writerCompleted.get()
        val writerMs = if (writerDone) writerEndMs.get() - writerStartMs else -1
        Log.i(
            TAG,
            "writer detail: bytesWritten=${bytesWritten.get()} of $FIXTURE_SIZE_BYTES " +
                "writerDone=$writerDone tookMs=$writerMs",
        )

        val verdict = when {
            result != null && !result.startsWith("ERROR:") -> "PFD_CONSUMED_TRANSCRIPT"
            writerDone -> "PFD_CONSUMED_NO_TRANSCRIPT"
            else -> "PFD_IGNORED"
        }
        Log.i(
            TAG,
            "VERDICT=$verdict bytesWritten=${bytesWritten.get()} writerDone=$writerDone " +
                "result=$result",
        )

        instrumentation.runOnMainSync { recognizer[0]?.destroy() }

        // The probe's product is the recorded answer above, so both outcomes are written down
        // rather than one of them failing the build -- this only asserts that a verdict-bearing
        // outcome was produced at all (a real hang with no error and a fully-drained pipe would
        // be a genuine instrumentation problem, not a negative answer).
        assertNotNull("recognizer never responded to a piped source", result)
    }

    private companion object {
        const val TAG = "RecognizerPipeProbe"
        const val FIXTURE_ASSET = "tr_probe_16k.pcm"
        const val FIXTURE_SIZE_BYTES = 192_000
    }
}
