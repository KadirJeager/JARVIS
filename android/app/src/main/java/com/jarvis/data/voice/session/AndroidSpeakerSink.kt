package com.jarvis.data.voice.session

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack

/**
 * Real speaker playback: PCM16 mono streaming at whatever rate [start] is called with
 * (24kHz on this client -- see [com.jarvis.data.voice.protocol.AUDIO_OUT_RATE_HZ]). This
 * class cannot be unit-tested on the JVM (it needs a real `AudioTrack`); [VoiceSession]'s
 * frame-routing logic is tested against a fake [SpeakerSink] instead (VoiceSessionTest).
 *
 * [write] is called from [VoiceSession]'s transport-callback thread (OkHttp's reader
 * thread in production, never the Android main thread), so blocking briefly there --
 * `AudioTrack.write` in [AudioTrack.MODE_STREAM] blocks until buffer space frees up -- is
 * safe and does not risk an ANR.
 */
class AndroidSpeakerSink : SpeakerSink {

    private var audioTrack: AudioTrack? = null

    override fun start(sampleRateHz: Int) {
        val minBufferSize = AudioTrack.getMinBufferSize(
            sampleRateHz,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        // 2x the platform minimum: same jitter headroom as AndroidMicSource's capture
        // buffer, sized against the OUTPUT rate/format pair per AudioTrack's own
        // getMinBufferSize contract (it is NOT interchangeable with the input minimum).
        val bufferSizeBytes = minBufferSize * 2
        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(sampleRateHz)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .build(),
            )
            .setBufferSizeInBytes(bufferSizeBytes)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        track.play()
        audioTrack = track
    }

    override fun write(pcm: ByteArray) {
        audioTrack?.write(pcm, 0, pcm.size)
    }

    override fun stop() {
        val track = audioTrack ?: return
        audioTrack = null
        track.stop()
        track.release()
    }
}
