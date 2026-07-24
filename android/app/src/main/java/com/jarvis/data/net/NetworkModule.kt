package com.jarvis.data.net

import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

/** Deployed jarvis-brain base URL (Katman 2b backend). */
const val BASE_URL = "https://jarvis-brain-000000000000.europe-west1.run.app"

object NetworkModule {
    /**
     * Builds the [JarvisApi] with a Bearer-attaching OkHttp client and a lenient
     * kotlinx.serialization converter. [tokenProvider] is read per request; on a 401,
     * [tokenRefresher] provides a fresh token for a single silent retry.
     */
    fun create(
        tokenProvider: () -> String?,
        tokenRefresher: () -> String? = { null },
    ): JarvisApi {
        val json = Json { ignoreUnknownKeys = true }
        val client = OkHttpClient.Builder()
            .addInterceptor(AuthInterceptor(tokenProvider))
            .authenticator(TokenAuthenticator(tokenRefresher))
            .build()
        return Retrofit.Builder()
            .baseUrl("$BASE_URL/")
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(JarvisApi::class.java)
    }
}
