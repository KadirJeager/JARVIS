package com.jarvis.data.net

import kotlinx.serialization.Serializable
import retrofit2.http.Body
import retrofit2.http.POST

/**
 * Durable device-token minting (`POST /api/device-tokens`, W0 canlı uç). This endpoint
 * only accepts a FRESH Google ID token — a device token itself gets 403 — so it must ride
 * the phone's normal authenticated path: the same [ApiSet] Retrofit instance, with
 * [AuthInterceptor] attaching the phone's own Google Bearer ([AuthClient.currentToken])
 * and [TokenAuthenticator] refreshing it on a 401. The phone never holds a device token of
 * its own to accidentally send instead.
 *
 * Separate interface for the same reason [FcmApi]/[ApprovalApi] are: fakes that implement
 * one surface must not widen for another feature's sake.
 */
interface DeviceTokenApi {
    @POST("api/device-tokens")
    suspend fun mint(@Body req: DeviceTokenRequest): DeviceTokenResponse
}

@Serializable
data class DeviceTokenRequest(val device: String)

@Serializable
data class DeviceTokenResponse(
    val token: String,
    val id: String,
    val device: String,
    val expires_at: String,
)
