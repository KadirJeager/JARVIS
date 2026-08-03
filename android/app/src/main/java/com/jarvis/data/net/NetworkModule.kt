package com.jarvis.data.net

import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

/** Deployed jarvis-brain base URL (Katman 2b backend). */
const val BASE_URL = "https://jarvis-brain-000000000000.europe-west1.run.app"

/** Deployed jarvis-voice live-call WebSocket (same endpoint the web PWA uses). */
const val VOICE_WS_URL = "wss://jarvis-voice-000000000000.europe-west1.run.app/ws/voice"

/** Every API surface, sharing one OkHttp client and one Retrofit instance. */
class ApiSet(
    val chat: JarvisApi,
    val voice: VoiceApi,
    val conversations: ConversationsApi,
    val approvals: ApprovalApi,
)

object NetworkModule {
    /**
     * Builds both APIs over a Bearer-attaching OkHttp client. [tokenProvider] is read per
     * request; on a 401, [tokenRefresher] provides a fresh token for a single silent
     * retry.
     *
     * `explicitNulls = true` is restated rather than relied on silently: it is ALREADY
     * kotlinx-serialization-json's default, so this line changes nothing today — it is a
     * pin against someone flipping it, because PATCH /api/voice/sample/{id} sends an
     * explicit null to CLEAR a field and the server reads an absent field as "leave it
     * alone". What actually makes those bodies correct is structural, not a flag: the
     * patch models ([LabelPatch] / [NotePatch]) carry ONE field and NO default, so the
     * field is written unconditionally and `encodeDefaults` cannot affect them either
     * way. Do NOT set `encodeDefaults = true` here hoping to fix a PATCH — with a shared
     * two-field body that would start sending `"note":null` on every label edit and WIPE
     * the user's note.
     *
     * [baseUrl] is a parameter so a test can point this exact production chain — same
     * OkHttp client, same converter, same Retrofit — at a local server and assert the
     * bytes that actually go on the wire (VoicePatchWireTest).
     */
    fun createApis(
        tokenProvider: () -> String?,
        tokenRefresher: () -> String? = { null },
        baseUrl: String = BASE_URL,
    ): ApiSet {
        val json = Json { ignoreUnknownKeys = true; explicitNulls = true }
        val client = OkHttpClient.Builder()
            .addInterceptor(AuthInterceptor(tokenProvider))
            .authenticator(TokenAuthenticator(tokenRefresher))
            .build()
        val retrofit = Retrofit.Builder()
            // Retrofit demands the trailing slash; accept it either way from the caller.
            .baseUrl(baseUrl.trimEnd('/') + "/")
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
        return ApiSet(
            chat = retrofit.create(JarvisApi::class.java),
            voice = retrofit.create(VoiceApi::class.java),
            conversations = retrofit.create(ConversationsApi::class.java),
            approvals = retrofit.create(ApprovalApi::class.java),
        )
    }
}
