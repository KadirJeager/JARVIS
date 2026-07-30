package com.jarvis.data.voice.session

/**
 * Seam over the on-device speech recognizer (`SpeechRecognizer` in [AndroidSpeechToText])
 * so [VoiceSession]'s STT state machine is JVM-testable with a fake. No Android import
 * here on purpose.
 *
 * Lifecycle, owned by the session: [start] once per call (arms the engine with its
 * listener), [listen] once per utterance turn -- the session calls it again after every
 * final result and after every recoverable error, which is what keeps the dialog
 * continuous -- and [destroy] once at teardown.
 */
interface SpeechToText {
    /** Prepares the engine for a call and installs [listener]. */
    fun start(listener: SpeechToTextListener)

    /** Begins listening for ONE utterance. Results arrive on [SpeechToTextListener]. */
    fun listen()

    /** Releases the recognizer for good. Safe to call even if never started. */
    fun destroy()
}

/**
 * Callbacks from [SpeechToText]. Implementations may invoke these on any thread (the
 * platform recognizer delivers on the main thread); [VoiceSession] synchronizes.
 */
interface SpeechToTextListener {
    /** The user started talking -- the barge-in signal: stop TTS, tell the server. */
    fun onBeginningOfSpeech()

    /** Interim transcription of the current utterance; may change as the user talks. */
    fun onPartialResult(text: String)

    /** Final transcription of one utterance. */
    fun onResult(text: String)

    /** The turn ended without a result but the engine itself is healthy (speech timeout,
     *  no match, recognizer busy): the session should simply [SpeechToText.listen] again. */
    fun onRecoverableError()

    /** The engine cannot serve this call (no recognition service, permanent failure):
     *  the session should end the call with a user-visible error. */
    fun onFatalError()
}

/**
 * Seam over the on-device synthesizer (`TextToSpeech` in [AndroidTextToSpeech]) so
 * [VoiceSession]'s SPEAKING-phase logic is JVM-testable with a fake. No Android import
 * here on purpose.
 */
interface SpeechSynthesis {
    /** Initializes the engine for a call and installs [listener]. May be asynchronous
     *  underneath -- [speak] calls made before init completes must be queued, not lost. */
    fun start(listener: SpeechSynthesisListener)

    /** Queues [text] to be spoken after anything already queued. */
    fun speak(text: String)

    /** Barge-in: stops the current utterance AND drops everything still queued. */
    fun stop()

    /** Releases the engine for good. Safe to call even if never started. */
    fun destroy()
}

/** Callbacks from [SpeechSynthesis]; may arrive on any thread. */
interface SpeechSynthesisListener {
    /** One utterance finished (or failed/interrupted) -- fired for every [SpeechSynthesis.speak]
     *  exactly once, so the session can settle SPEAKING back to LISTENING. */
    fun onUtteranceDone()
}
