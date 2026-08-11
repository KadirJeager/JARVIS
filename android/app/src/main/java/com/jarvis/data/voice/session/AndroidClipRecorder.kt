package com.jarvis.data.voice.session

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import com.jarvis.data.voice.protocol.AUDIO_IN_RATE_HZ
import com.jarvis.ui.voice.ClipRecorder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Real microphone capture for the "Bu cihazı tanıt" enrollment flow (Task 9): records
 * [count] clips of [seconds] each, back to back, as raw PCM16 mono 16kHz byte arrays --
 * the wire format [EnrollDeviceViewModel][com.jarvis.ui.voice.EnrollDeviceViewModel]
 * base64-encodes for `EnrollRequest.clips`.
 *
 * This runs WHILE the voice call's own [AndroidMicSource] `AudioRecord` is live: the
 * enrollment flow requires an open voice bridge (the server only speaks the liveness
 * code over one, and the user has just repeated it on that same call). Same-app
 * concurrent capture from two `AudioRecord` clients is permitted by Android's capture
 * policy, but that concurrency is device-verified only in Task 10 -- this class has not
 * yet been run against the live call path on real hardware (honest limit).
 *
 * Cannot be unit-tested on the JVM (needs a real `AudioRecord`), same split as
 * [AndroidMicSource]: [ClipRecorder] is what keeps
 * [com.jarvis.ui.voice.EnrollDeviceViewModel] itself JVM-testable against a fake.
 *
 * RECORD_AUDIO is assumed already granted -- same precondition [AndroidMicSource]
 * documents, gated by the caller before a voice call (and therefore this flow) can
 * start at all.
 */
class AndroidClipRecorder : ClipRecorder {

    @SuppressLint("MissingPermission") // caller gates on RECORD_AUDIO before a call can start
    override suspend fun record(count: Int, seconds: Double): List<ByteArray> =
        withContext(Dispatchers.IO) {
            val minBufferSize = AudioRecord.getMinBufferSize(
                AUDIO_IN_RATE_HZ,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
            )
            // 2x the platform minimum -- same headroom AndroidMicSource allocates.
            val record = AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                AUDIO_IN_RATE_HZ,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                minBufferSize * 2,
            )
            // Construction "succeeds" even when the mic is unavailable; the failure
            // only shows in state -- same idiom as AndroidMicSource.start().
            if (record.state != AudioRecord.STATE_INITIALIZED) {
                record.release()
                throw IllegalStateException("AudioRecord failed to initialize (mic busy or unavailable)")
            }
            try {
                record.startRecording()
                val bytesPerClip = (AUDIO_IN_RATE_HZ * seconds * BYTES_PER_SAMPLE).toInt()
                (1..count).map { readClip(record, bytesPerClip, minBufferSize) }
            } finally {
                // Guarded: a throw from startRecording() itself (state STOPPED, never
                // started) would otherwise have its own exception masked by the
                // IllegalStateException stop() raises when called on a non-recording
                // AudioRecord -- release() must still run either way.
                if (record.recordingState == AudioRecord.RECORDSTATE_RECORDING) record.stop()
                record.release()
            }
        }

    /** Blocking reads in [minBufferSize]-sized chunks until [bytesNeeded] is filled or
     *  the record stops producing data, matching AndroidMicSource.readFrame()'s
     *  "read > 0 or treat as ended" convention. A short read here (mic dropped mid-clip)
     *  surfaces as a shorter-than-expected clip rather than a crash; the server-side CM
     *  is the backstop that rejects a bad clip (422), not this layer. */
    private fun readClip(record: AudioRecord, bytesNeeded: Int, minBufferSize: Int): ByteArray {
        val out = ByteArray(bytesNeeded)
        var filled = 0
        while (filled < bytesNeeded) {
            val toRead = minOf(minBufferSize, bytesNeeded - filled)
            val read = record.read(out, filled, toRead)
            if (read <= 0) break
            filled += read
        }
        return if (filled == bytesNeeded) out else out.copyOf(filled)
    }

    private companion object {
        const val BYTES_PER_SAMPLE = 2 // PCM16
    }
}
