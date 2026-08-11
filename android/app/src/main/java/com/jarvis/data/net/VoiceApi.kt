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

    /**
     * Same endpoint, two methods on purpose. The server treats an ABSENT field as "leave
     * it alone" and an EXPLICIT null as "clear it" (model_fields_set in voice_manage.py),
     * so a body must never mention a field the user did not touch. One shared body type
     * cannot express that — see [LabelPatch] / [NotePatch].
     */
    @PATCH("api/voice/sample/{id}")
    suspend fun patchLabel(@Path("id") id: String, @Body req: LabelPatch): VoiceSampleDto

    @PATCH("api/voice/sample/{id}")
    suspend fun patchNote(@Path("id") id: String, @Body req: NotePatch): VoiceSampleDto

    @DELETE("api/voice/sample/{id}")
    suspend fun deleteSample(@Path("id") id: String): SampleDeletedResponse

    @POST("api/voice/history/{id}/confirm")
    suspend fun confirm(@Path("id") id: String): ConfirmResponse

    @POST("api/voice/history/{id}/reject")
    suspend fun reject(@Path("id") id: String): RejectResponse

    @DELETE("api/voice/profile")
    suspend fun deleteProfile(): ProfileDeletedResponse
}

/**
 * `challenge()`/`enroll()` -- SEPARATE from [VoiceApi] because they must be built against
 * [NetworkModule.VOICE_BASE_URL] (jarvis-voice), not [NetworkModule.BASE_URL] (jarvis-brain)
 * that the rest of [VoiceApi] rides. See [NetworkModule.VOICE_BASE_URL]'s doc for why:
 * jarvis-voice holds the in-process `active_bridges` map `challenge()` needs and is the
 * only service that warms the CM `enroll()` scores against.
 */
interface VoiceEnrollApi {
    /**
     * Asks the server to mint a 4-digit liveness code. When a live voice bridge is
     * open for this user the server speaks it over that bridge, which is why the
     * enrollment flow has to run inside a voice call (see C2).
     */
    @POST("api/voice/challenge")
    suspend fun challenge(): ChallengeResponse

    /**
     * Writes anchors. Requires a liveness grant minted by [challenge] within the
     * last 5 minutes (409 otherwise) and clips the CM clears (422 otherwise).
     */
    @POST("api/voice/enroll")
    suspend fun enroll(@Body req: EnrollRequest): EnrollResponse
}
