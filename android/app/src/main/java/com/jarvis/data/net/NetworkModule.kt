package com.jarvis.data.net

import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

/** Deployed jarvis-brain base URL (Katman 2b backend). */
const val BASE_URL = "https://jarvis-brain-000000000000.europe-west1.run.app"

/** Both API surfaces, sharing one OkHttp client and one Retrofit instance. */
class ApiSet(val chat: JarvisApi, val voice: VoiceApi)

object NetworkModule {
    /**
     * Builds both APIs over a Bearer-attaching OkHttp client. [tokenProvider] is read per
     * request; on a 401, [tokenRefresher] provides a fresh token for a single silent
     * retry.
     *
     * `explicitNulls = true` matters: PATCH /api/voice/sample/{id} uses an explicit null
     * to CLEAR a label, and kotlinx's default (omit nulls) would turn that into "field
     * absent", which the server reads as "leave it alone".
     */
    fun createApis(
        tokenProvider: () -> String?,
        tokenRefresher: () -> String? = { null },
    ): ApiSet {
        val json = Json { ignoreUnknownKeys = true; explicitNulls = true }
        val client = OkHttpClient.Builder()
            .addInterceptor(AuthInterceptor(tokenProvider))
            .authenticator(TokenAuthenticator(tokenRefresher))
            .build()
        val retrofit = Retrofit.Builder()
            .baseUrl("$BASE_URL/")
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
        return ApiSet(
            chat = retrofit.create(JarvisApi::class.java),
            voice = retrofit.create(VoiceApi::class.java),
        )
    }

    /** Kept so existing chat-side callers and tests are untouched. */
    fun create(
        tokenProvider: () -> String?,
        tokenRefresher: () -> String? = { null },
    ): JarvisApi = createApis(tokenProvider, tokenRefresher).chat
}
