package com.jarvis.data.net

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Query

interface JarvisApi {
    @POST("api/chat")
    suspend fun chat(@Body req: ChatRequest): ChatResponse

    @GET("api/history")
    suspend fun history(@Query("session_id") sessionId: String): HistoryResponse
}
