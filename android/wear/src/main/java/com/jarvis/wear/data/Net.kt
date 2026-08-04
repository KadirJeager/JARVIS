package com.jarvis.wear.data

import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import java.util.concurrent.TimeUnit

/**
 * Retrofit + OkHttp kurulumu — telefonun `data/net/NetworkModule.kt`'sı ile aynı
 * converter/Json/timeout deseni. Saat için ayrı token yenileme akışı (TokenAuthenticator)
 * yok: 401 doğrudan [UnauthorizedException] olarak yükselir, UI eşleştirme ekranına düşer.
 */
object Net {
    private val json = Json { ignoreUnknownKeys = true }

    fun buildApi(baseUrl: String, tokenProvider: () -> String?): JarvisApi {
        val client = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS) // chat turu LLM bekler (telefonla aynı sınıf)
            .addInterceptor { chain ->
                val token = tokenProvider()
                val request = if (token != null) {
                    chain.request().newBuilder()
                        .addHeader("Authorization", "Bearer $token")
                        .build()
                } else {
                    chain.request()
                }
                chain.proceed(request)
            }
            .build()
        val retrofit = Retrofit.Builder()
            // Retrofit trailing slash ister; çağıranın vermesine bağlı kalma.
            .baseUrl(baseUrl.trimEnd('/') + "/")
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
        return JarvisApi(retrofit.create(JarvisService::class.java))
    }
}
