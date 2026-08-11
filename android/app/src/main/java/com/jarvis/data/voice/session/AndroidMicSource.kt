package com.jarvis.data.voice.session

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioRecordingConfiguration
import android.media.MediaRecorder
import android.media.audiofx.AudioEffect
import android.os.Build
import android.util.Log
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** One attached capture effect as reported by [AudioRecordingConfiguration.getEffects]. */
internal data class MicEffect(val name: String, val type: UUID)

/**
 * DATA line for the capture path: what the platform actually attached, not what we
 * asked for. `isClientSilenced` is the direct test of "is our own TTS reaching the
 * mic" -- the system mutes a capture client when another one wins the route.
 * Both APIs are 29+; below that the honest answer is "unknown".
 *
 * AEC presence is decided by comparing [MicEffect.type] -- the standardized
 * `AudioEffect.Descriptor` UUID -- against [aecType], NEVER by matching
 * [MicEffect.name]: that field is a vendor-supplied human-readable label with no
 * guaranteed spelling (a device could name it "Acoustic Echo Canceler", which
 * contains no "aec" substring in any casing). Names are still what gets printed in
 * `effects=`, because that's what a human reads the log for.
 */
internal fun formatEffectReport(
    effects: List<MicEffect>,
    silenced: Boolean,
    sdkInt: Int,
    aecType: UUID,
): String =
    if (sdkInt < 29) {
        "mic effects=unknown silenced=unknown (API $sdkInt < 29)"
    } else {
        val names = if (effects.isEmpty()) "none" else effects.joinToString(",") { it.name }
        "mic effects=$names aec=${effects.any { it.type == aecType }} silenced=$silenced"
    }

/**
 * Real microphone capture: PCM16 mono at whatever rate [start] is called with (16kHz on
 * this client -- see [com.jarvis.data.voice.protocol.AUDIO_IN_RATE_HZ]). This class
 * cannot be unit-tested on the JVM (it needs a real `AudioRecord`); [VoiceSession]'s
 * frame-routing logic is tested against a fake [MicSource] instead (VoiceSessionTest).
 *
 * The RECORD_AUDIO runtime permission is assumed already granted -- the caller (the
 * Activity/ViewModel layer) gates [VoiceSession.start] on that, so a permission failure
 * never reaches here as a crash.
 *
 * [audioManager] is a narrow dependency (one system service, not a whole `Context`) used
 * solely for the AEC self-test telemetry below: this class has never needed a `Context`
 * and the callback needs exactly `registerAudioRecordingCallback`/`unregister...`.
 */
class AndroidMicSource(private val audioManager: AudioManager) : MicSource, PcmTapSource {

    private var audioRecord: AudioRecord? = null

    // Set via setTap(); read on the capture read path in readFrame(). @Volatile because
    // the tap is installed from a different coroutine/thread than the one running
    // readFrame()'s Dispatchers.IO block.
    @Volatile private var tap: ((ByteArray) -> Unit)? = null

    // Sized in start(): the read chunk size must match the buffer that was actually
    // allocated for the rate/format this call was opened with.
    private var readChunkBytes = 0

    // Registered in start() on API 29+ only; null otherwise or before start() runs.
    // Kept so stop() can unregister the exact instance it registered.
    private var recordingCallback: AudioManager.AudioRecordingCallback? = null

    @SuppressLint("MissingPermission") // caller gates on RECORD_AUDIO before calling start()
    override fun start(sampleRateHz: Int) {
        val minBufferSize = AudioRecord.getMinBufferSize(
            sampleRateHz,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        readChunkBytes = minBufferSize
        // 2x the platform minimum: headroom against brief scheduling jitter on the
        // capture thread (a GC pause, a slow readFrame() consumer) without adding
        // noticeable latency -- at 16kHz mono 16-bit, even 4x the minimum is well under
        // 100ms of buffered audio.
        val internalBufferBytes = minBufferSize * 2
        val record = AudioRecord(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            sampleRateHz,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            internalBufferBytes,
        )
        // Construction "succeeds" even when the mic is unavailable (held by a real
        // phone call); the failure only shows in state. startRecording() on such a
        // record throws from OkHttp's reader thread — fail here with a release instead,
        // and let VoiceSession turn it into a user-visible Turkish error.
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            throw IllegalStateException("AudioRecord failed to initialize (mic busy or unavailable)")
        }
        record.startRecording()
        audioRecord = record
        // AEC self-test: report what the platform actually attached to THIS capture
        // client, not what VOICE_COMMUNICATION above merely asked for -- the direct
        // test of the 2026-08-10 utterance-4 hypothesis (our own TTS reaching the mic).
        // Honest limit: this covers only our capture client. The system speech
        // recognizer (SODA) runs in a separate process with its own AudioRecord, so its
        // capture never appears in this list -- do not read "no effects reported here"
        // as "the whole audio chain is unmeasured/unprotected".
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val callback = object : AudioManager.AudioRecordingCallback() {
                override fun onRecordingConfigChanged(configs: MutableList<AudioRecordingConfiguration>) {
                    val mine = configs.firstOrNull { it.clientAudioSessionId == record.audioSessionId }
                        ?: return
                    Log.i(TAG, formatEffectReport(
                        mine.effects.map { MicEffect(it.name, it.type) },
                        mine.isClientSilenced,
                        Build.VERSION.SDK_INT,
                        AudioEffect.EFFECT_TYPE_AEC,
                    ))
                }
            }
            recordingCallback = callback
            audioManager.registerAudioRecordingCallback(callback, null)
        }
    }

    override suspend fun readFrame(): ByteArray? = withContext(Dispatchers.IO) {
        val record = audioRecord ?: return@withContext null
        val buffer = ByteArray(readChunkBytes)
        // Blocking read, off the caller's dispatcher via withContext above -- AudioRecord
        // in blocking mode (the default) waits for readChunkBytes to become available.
        val read = record.read(buffer, 0, buffer.size)
        val frame = if (read > 0) buffer.copyOf(read) else null
        // Tap sees every frame the primary consumer reads, before any session-level
        // gating -- parity with a recognizer that owns its own microphone. Invoked here
        // (not in VoiceSession) so the tap is upstream of ALL session-level decisions,
        // not just the ones VoiceSession happens to apply after reading.
        if (frame != null) {
            tap?.invoke(frame)
        }
        frame
    }

    override fun setTap(tap: ((ByteArray) -> Unit)?) {
        this.tap = tap
    }

    override fun stop() {
        val record = audioRecord ?: return
        audioRecord = null
        // Unregister before release(): safe even if start() never registered one
        // (API < 29, or start() was never called) since recordingCallback is null then.
        recordingCallback?.let { audioManager.unregisterAudioRecordingCallback(it) }
        recordingCallback = null
        record.stop()
        record.release()
    }

    private companion object {
        const val TAG = "AndroidMicSource"
    }
}
