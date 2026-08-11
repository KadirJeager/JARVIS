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

/**
 * Deployed jarvis-voice base URL, HTTPS sibling of [VOICE_WS_URL]'s host. [VoiceEnrollApi]'s
 * `challenge()`/`enroll()` MUST hit this service, not [BASE_URL] (jarvis-brain) -- this is a
 * deployed-topology contract, not a style choice, and routing it wrong was tonight's
 * Critical:
 * - `challenge()` asks the server to speak a code over the caller's OPEN voice
 *   WebSocket bridge. That bridge lives in jarvis-voice's in-process `active_bridges` map
 *   (see voice.py); jarvis-brain has no bridge for this user at all, so a challenge sent
 *   there can never find one to speak over.
 * - `enroll()` scores submitted clips against the channel-adaptive CM (anti-spoof). Only
 *   jarvis-voice warms that ~1.2 GiB model at startup; jarvis-brain deliberately never
 *   loads it (see brain/app/main.py's enroll guard) to stay inside its 3 Gi budget, so
 *   routing enroll there either 503s or -- worse -- lazy-loads the model into a live
 *   service under load (the 2026-08-10 OOM class).
 */
const val VOICE_BASE_URL = "https://jarvis-voice-000000000000.europe-west1.run.app"

/** Every API surface, sharing one OkHttp client and one Retrofit instance. */
class ApiSet(
    val chat: JarvisApi,
    val voice: VoiceApi,
    val conversations: ConversationsApi,
    val approvals: ApprovalApi,
    val fcm: FcmApi,
    val deviceTokens: DeviceTokenApi,
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
        client: OkHttpClient? = null,
    ): ApiSet {
        val retrofit = buildRetrofit(tokenProvider, tokenRefresher, baseUrl, client)
        return ApiSet(
            chat = retrofit.create(JarvisApi::class.java),
            voice = retrofit.create(VoiceApi::class.java),
            conversations = retrofit.create(ConversationsApi::class.java),
            approvals = retrofit.create(ApprovalApi::class.java),
            fcm = retrofit.create(FcmApi::class.java),
            deviceTokens = retrofit.create(DeviceTokenApi::class.java),
        )
    }

    /**
     * Same OkHttp/auth/json config as [createApis], only the base differs: [VoiceEnrollApi]
     * (`challenge()`/`enroll()`) must land on jarvis-voice, not jarvis-brain -- see
     * [VOICE_BASE_URL]'s doc for why. A SEPARATE Retrofit instance, not another interface
     * on [createApis]'s single instance, because that instance is pinned to [baseUrl] and
     * this one must be pinned to [VOICE_BASE_URL] instead.
     *
     * [client] defaults to null (build a fresh OkHttpClient here, the original behaviour) so
     * standalone callers -- [VoiceEnrollApiTest] included -- are unaffected. A caller that
     * ALSO talks to [createApis]'s base (see [com.jarvis.AppContainer]) should instead pass
     * that call's [buildHttpClient] instance here: same interceptor chain either way, but
     * one connection pool + dispatcher shared between both bases instead of two.
     */
    fun createVoiceEnrollApi(
        tokenProvider: () -> String?,
        tokenRefresher: () -> String? = { null },
        baseUrl: String = VOICE_BASE_URL,
        client: OkHttpClient? = null,
    ): VoiceEnrollApi =
        buildRetrofit(tokenProvider, tokenRefresher, baseUrl, client).create(VoiceEnrollApi::class.java)

    /**
     * The Bearer-attaching, 401-silent-retry OkHttp client both [createApis] and
     * [createVoiceEnrollApi] build internally when not given one explicitly. Exposed so a
     * caller that needs BOTH factories (jarvis-brain AND jarvis-voice, same auth) can build
     * this ONCE and pass it to both `client` parameters instead of paying for a second
     * connection pool + dispatcher for a client that would authenticate identically anyway.
     */
    fun buildHttpClient(
        tokenProvider: () -> String?,
        tokenRefresher: () -> String? = { null },
    ): OkHttpClient = OkHttpClient.Builder()
        .addInterceptor(AuthInterceptor(tokenProvider))
        .authenticator(TokenAuthenticator(tokenRefresher))
        .build()

    private fun buildRetrofit(
        tokenProvider: () -> String?,
        tokenRefresher: () -> String?,
        baseUrl: String,
        client: OkHttpClient? = null,
    ): Retrofit {
        val json = Json { ignoreUnknownKeys = true; explicitNulls = true }
        val httpClient = client ?: buildHttpClient(tokenProvider, tokenRefresher)
        return Retrofit.Builder()
            // Retrofit demands the trailing slash; accept it either way from the caller.
            .baseUrl(baseUrl.trimEnd('/') + "/")
            .client(httpClient)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
    }
}
