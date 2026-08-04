package com.jarvis.wear.data

import kotlinx.serialization.Serializable
import retrofit2.HttpException
import retrofit2.http.Body
import retrofit2.http.POST

@Serializable data class ChatRequest(val session_id: String, val message: String)
@Serializable data class ChatResponse(val reply: String)

/** Telefondaki eşleştirme akışına düşülmesi gerektiğinin işareti (spec §W0: 401 = token
 * geçersiz/iptal). */
class UnauthorizedException : Exception("Oturum geçersiz; saati telefondan yeniden eşleştir.")

/** [userMessage] doğrudan saat ekranında gösterilecek Türkçe metin. */
class JarvisApiException(val userMessage: String) : Exception(userMessage)

interface JarvisService {
    @POST("api/chat")
    suspend fun chat(@Body body: ChatRequest): ChatResponse
}

class JarvisApi(private val service: JarvisService) {
    /** reply döner; 401 -> UnauthorizedException, diğer hatalar -> JarvisApiException
     * (Türkçe kullanıcı mesajı — saat ekranında olduğu gibi gösterilir). */
    suspend fun chat(sessionId: String, message: String): String = try {
        service.chat(ChatRequest(sessionId, message)).reply
    } catch (e: HttpException) {
        if (e.code() == 401) throw UnauthorizedException()
        throw JarvisApiException("Jarvis'e ulaşılamadı (${e.code()}). Az sonra tekrar dene.")
    } catch (e: Exception) {
        if (e is UnauthorizedException || e is JarvisApiException) throw e
        throw JarvisApiException("Ağ hatası: Jarvis'e ulaşılamadı. Bağlantını kontrol et.")
    }
}
