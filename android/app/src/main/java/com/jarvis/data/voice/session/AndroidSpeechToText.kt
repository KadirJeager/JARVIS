package com.jarvis.data.voice.session

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log

/**
 * Real on-device STT: `SpeechRecognizer` configured for tr-TR free-form dictation,
 * preferring the offline (on-device) recognizer when the platform offers one.
 *
 * Threading: every `SpeechRecognizer` call is marshalled onto the main thread, because
 * [VoiceSession] invokes this class from the transport's reader thread while the platform
 * recognizer is main-thread-bound. Recognition callbacks arrive on the main thread and
 * are forwarded to the session's listener directly -- the session synchronizes itself.
 *
 * This class cannot be unit-tested on the JVM (it needs a real recognizer service);
 * [VoiceSession]'s restart/barge-in logic is tested against a fake [SpeechToText]
 * instead (VoiceSessionTest). Only [isRecoverableSttError], a pure function over the
 * platform's error-code constants, is JVM-tested (SttErrorMappingTest).
 */
class AndroidSpeechToText(private val context: Context) : SpeechToText {

    private val main = Handler(Looper.getMainLooper())

    // Only ever touched on the main thread.
    private var recognizer: SpeechRecognizer? = null

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
            listener?.onPartialResult(text)
        }

        override fun onResults(results: Bundle?) {
            val text = results
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
            // Lengths only, never transcript content (existing convention).
            Log.i(TAG, "tl ev=onResults len=${text?.length ?: 0}")
            // An empty final result is a no-match in disguise: treat it as recoverable
            // so the session re-arms listening instead of stalling with no callback.
            if (text.isNullOrBlank()) listener?.onRecoverableError() else listener?.onResult(text)
        }

        override fun onError(error: Int) {
            Log.i(TAG, "tl ev=onError code=$error")
            if (isRecoverableSttError(error)) listener?.onRecoverableError()
            else {
                Log.w(TAG, "SpeechRecognizer fatal error: $error")
                listener?.onFatalError()
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
            recognizer = createRecognizer(context).apply {
                setRecognitionListener(recognitionListener)
            }
        }
    }

    override fun listen() {
        main.post {
            Log.i(TAG, "tl ev=startListening")
            // null when start() has not completed or declared a fatal error -- the
            // session is already tearing down in that case, so dropping is correct.
            recognizer?.startListening(recognizeIntent)
        }
    }

    override fun destroy() {
        listener = null
        main.post {
            recognizer?.destroy()
            recognizer = null
        }
    }

    private companion object {
        const val TAG = "AndroidSpeechToText"

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
            // VOICE_COMMUNICATION = the AEC-enabled audio source (API 31+): without
            // it the recognizer hears Jarvis's own TTS from the speaker, treats it as
            // user speech, and barge-in kills every reply after a few words (prod
            // report 2026-07-31: "kendi sesi yüzünden dinleme moduna geçiyor").
            // The PCM mic path (AndroidMicSource) already uses this same source.
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                putExtra(
                    RecognizerIntent.EXTRA_AUDIO_SOURCE,
                    android.media.MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                )
            }
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

        fun createRecognizer(context: Context): SpeechRecognizer =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                SpeechRecognizer.isOnDeviceRecognitionAvailable(context)
            ) {
                SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
            } else {
                SpeechRecognizer.createSpeechRecognizer(context)
            }
    }
}

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
