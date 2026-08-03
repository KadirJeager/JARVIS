package com.jarvis.data.voice.session

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import java.util.Locale
import java.util.concurrent.atomic.AtomicInteger

/**
 * Real on-device TTS: platform `TextToSpeech` in tr-TR, QUEUE_ADD semantics, replacing
 * the old 24kHz server-audio playback (protocol v2 sends jarvis_text, not PCM).
 *
 * Threading: all engine calls are marshalled onto the main thread, since [VoiceSession]
 * calls this from the transport's reader thread. Init is asynchronous, so [speak] calls
 * landing before onInit completes are held in [pending] and flushed on success -- a fast
 * server reply can otherwise be spoken into a half-built engine and silently lost.
 *
 * Not JVM-testable (needs a real TTS engine); [VoiceSession]'s SPEAKING-phase logic is
 * tested against a fake [SpeechSynthesis] instead (VoiceSessionTest).
 */
class AndroidTextToSpeech(context: Context) : SpeechSynthesis {

    private val main = Handler(Looper.getMainLooper())
    private val appContext = context.applicationContext

    // Everything below (except listener) is only ever touched on the main thread.
    private var engine: TextToSpeech? = null
    private var ready = false
    private val pending = ArrayDeque<String>()
    private val utteranceSeq = AtomicInteger(0)

    // Written by start()/destroy() (any thread), read from engine callbacks.
    @Volatile
    private var listener: SpeechSynthesisListener? = null

    private val progressListener = object : UtteranceProgressListener() {
        override fun onStart(utteranceId: String?) {}

        override fun onDone(utteranceId: String?) = reportDone(utteranceId)

        // Errors still mean the utterance is OVER for the session's purposes (it will
        // never finish) -- report done so SPEAKING can settle instead of sticking.
        override fun onError(utteranceId: String?) = reportDone(utteranceId)

        private fun reportDone(utteranceId: String?) {
            // The internal queue-flush marker of stop() is not a session utterance.
            if (utteranceId == FLUSH_UTTERANCE_ID) return
            listener?.onUtteranceDone()
        }
    }

    override fun start(listener: SpeechSynthesisListener) {
        this.listener = listener
        main.post {
            if (engine != null) return@post
            engine = TextToSpeech(appContext) { status ->
                if (status != TextToSpeech.SUCCESS) {
                    Log.e(TAG, "TTS init failed: $status")
                    // No engine: drop queued text and report each as done, so a server
                    // reply does not leave the session stuck on SPEAKING forever.
                    while (pending.isNotEmpty()) {
                        pending.removeFirst()
                        listener.onUtteranceDone()
                    }
                    return@TextToSpeech
                }
                val langResult = engine?.setLanguage(Locale("tr", "TR"))
                if (langResult == TextToSpeech.LANG_MISSING_DATA ||
                    langResult == TextToSpeech.LANG_NOT_SUPPORTED
                ) {
                    // Fall back to the engine's default voice rather than failing the
                    // call: a wrong-accent reply beats a silent one.
                    Log.w(TAG, "tr-TR TTS data unavailable ($langResult), using engine default")
                }
                // Put the REPLY on the same audio path the microphone's echo canceller
                // references. The recognizer and the PCM mic both open
                // VOICE_COMMUNICATION (the AEC-enabled source), but TextToSpeech
                // defaults to Engine.DEFAULT_STREAM = STREAM_MUSIC — so the platform AEC
                // was cancelling against a playback path the reply was never on, and had
                // no chance of removing it. That is a large part of why Jarvis kept
                // hearing himself, and why VoiceSession had to grow a half-duplex guard
                // to compensate.
                //
                // HYPOTHESIS, not a proven fix: AEC behaviour is HAL-specific and this
                // has to be MEASURED on the S23 (does the recognizer still transcribe
                // Jarvis's own reply with the guard removed?). The guard stays either
                // way — it is the structural guarantee; this only stops asking it to
                // cover for a misrouted audio stream.
                engine?.setAudioAttributes(
                    android.media.AudioAttributes.Builder()
                        .setUsage(android.media.AudioAttributes.USAGE_VOICE_COMMUNICATION)
                        .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build(),
                )
                engine?.setOnUtteranceProgressListener(progressListener)
                ready = true
                while (pending.isNotEmpty()) speakNow(pending.removeFirst())
            }
        }
    }

    override fun speak(text: String) {
        main.post {
            if (!ready) {
                pending.addLast(text)
                return@post
            }
            speakNow(text)
        }
    }

    override fun stop() {
        main.post {
            // Barge-in: clear BOTH queues -- our not-yet-init backlog and the engine's
            // already-queued utterances -- then interrupt whatever is speaking. Without
            // the flush, a queued sentence would keep playing after the user cut in.
            pending.clear()
            engine?.speak("", TextToSpeech.QUEUE_FLUSH, null, FLUSH_UTTERANCE_ID)
            engine?.stop()
        }
    }

    override fun destroy() {
        listener = null
        main.post {
            ready = false
            pending.clear()
            engine?.stop()
            engine?.shutdown()
            engine = null
        }
    }

    private fun speakNow(text: String) {
        engine?.speak(text, TextToSpeech.QUEUE_ADD, null, "jarvis-${utteranceSeq.incrementAndGet()}")
    }

    private companion object {
        const val TAG = "AndroidTextToSpeech"
        const val FLUSH_UTTERANCE_ID = "jarvis-flush"
    }
}
