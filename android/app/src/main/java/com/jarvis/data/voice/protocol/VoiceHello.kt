package com.jarvis.data.voice.protocol

import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

/**
 * Wire shape of the first TEXT frame, matching brain/app/voice_protocol.py's `parse_hello`
 * exactly: `{"token", "device_hint"?, "presence"?, "client_caps"}`. Field names are
 * snake_case on the wire on purpose -- no @SerialName here, the Kotlin property names ARE
 * the JSON keys, same convention as the other wire models in data.net.
 */
@Serializable
private data class VoiceHelloWire(
    val token: String,
    val device_hint: String,
    val presence: String,
    val client_caps: ClientCapsWire,
)

/**
 * Protocol-v2 capability declaration: STT and TTS run on the device (SpeechRecognizer /
 * TextToSpeech), so the server only sees text frames and mic PCM for speaker-ID -- never
 * model audio to send back. The server REJECTS a caps-less hello, so this is not
 * optional here even though the wire model tolerates old clients.
 */
@Serializable
private data class ClientCapsWire(
    val stt: String,
    val tts: String,
    val proto: Int,
)

/**
 * Builds the client's hello frame. [deviceHint] and [presence] are optional server-side
 * (backward compatible with a token-only hello) but SHOULD be sent -- they feed the
 * server's speaker-identity trust fusion (spec §6, §11) -- so this client always sends
 * both rather than relying on the server's defaults. `client_caps` is always the frozen
 * protocol-v2 set (`stt=device`, `tts=device`, `proto=2`).
 */
fun buildVoiceHello(token: String, deviceHint: String, presence: String = "foreground"): String =
    Json.encodeToString(
        VoiceHelloWire(
            token = token,
            device_hint = deviceHint,
            presence = presence,
            client_caps = ClientCapsWire(stt = "device", tts = "device", proto = 2),
        ),
    )
