package com.jarvis.data.net

import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.Path

/**
 * Speaker-identity management endpoints (spec §6). Deliberately SEPARATE from
 * [JarvisApi]: three tests hand-implement JarvisApi as a fake, and widening that
 * interface would break all three for reasons unrelated to what they test.
 */
interface VoiceApi {
    @GET("api/voice/profile")
    suspend fun profile(): VoiceProfileResponse

    @PATCH("api/voice/sample/{id}")
    suspend fun patchSample(@Path("id") id: String, @Body req: SamplePatchRequest): VoiceSampleDto

    @DELETE("api/voice/sample/{id}")
    suspend fun deleteSample(@Path("id") id: String): SampleDeletedResponse

    @POST("api/voice/history/{id}/confirm")
    suspend fun confirm(@Path("id") id: String): ConfirmResponse

    @POST("api/voice/history/{id}/reject")
    suspend fun reject(@Path("id") id: String): RejectResponse

    @DELETE("api/voice/profile")
    suspend fun deleteProfile(): ProfileDeletedResponse
}
