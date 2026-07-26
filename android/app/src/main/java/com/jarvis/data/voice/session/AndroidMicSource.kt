package com.jarvis.data.voice.session

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Real microphone capture: PCM16 mono at whatever rate [start] is called with (16kHz on
 * this client -- see [com.jarvis.data.voice.protocol.AUDIO_IN_RATE_HZ]). This class
 * cannot be unit-tested on the JVM (it needs a real `AudioRecord`); [VoiceSession]'s
 * frame-routing logic is tested against a fake [MicSource] instead (VoiceSessionTest).
 *
 * The RECORD_AUDIO runtime permission is assumed already granted -- the caller (the
 * Activity/ViewModel layer) gates [VoiceSession.start] on that, so a permission failure
 * never reaches here as a crash.
 */
class AndroidMicSource : MicSource {

    private var audioRecord: AudioRecord? = null

    // Sized in start(): the read chunk size must match the buffer that was actually
    // allocated for the rate/format this call was opened with.
    private var readChunkBytes = 0

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
    }

    override suspend fun readFrame(): ByteArray? = withContext(Dispatchers.IO) {
        val record = audioRecord ?: return@withContext null
        val buffer = ByteArray(readChunkBytes)
        // Blocking read, off the caller's dispatcher via withContext above -- AudioRecord
        // in blocking mode (the default) waits for readChunkBytes to become available.
        val read = record.read(buffer, 0, buffer.size)
        if (read > 0) buffer.copyOf(read) else null
    }

    override fun stop() {
        val record = audioRecord ?: return
        audioRecord = null
        record.stop()
        record.release()
    }
}
