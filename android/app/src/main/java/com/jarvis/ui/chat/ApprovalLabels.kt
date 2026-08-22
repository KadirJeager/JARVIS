package com.jarvis.ui.chat

import com.jarvis.data.approvals.ApprovalStatus

/**
 * The one place an approval status becomes Turkish (the `ui/voice/VoiceLabels.kt` pattern):
 * the wire never carries display text and the display never carries wire values.
 *
 * Kept out of `ApprovalCard.kt` and free of Compose so the JVM suite can pin the copy
 * without a device — user-visible wording is exactly the kind of thing that rots silently.
 */
fun approvalStatusLabel(status: ApprovalStatus): String = when (status) {
    ApprovalStatus.PENDING -> "Onay bekliyor"
    ApprovalStatus.APPROVED -> "Onaylandı"
    ApprovalStatus.REJECTED -> "Reddedildi"
    // Timeout IS a rejection (spec §4.1), but saying "Reddedildi" would claim Kadir
    // decided something the clock decided.
    ApprovalStatus.EXPIRED -> "Süresi doldu"
    // Deliberately not "Başarısız": the approval succeeded, the tool it authorised is what
    // blew up, and the outcome text below the badge carries the observation (spec §4.5).
    ApprovalStatus.FAILED -> "Çalıştırılamadı"
    // Reached when a newer backend invents a status this build has never seen. Saying so
    // is honest; guessing would put an "Onayla" button on an unknown state.
    ApprovalStatus.UNKNOWN -> "Durum bilinmiyor"
}

/**
 * Turkish display label for an approval cause slug.
 * Fail-closed: an unknown slug returns "Neden bilinmiyor", never raw text or crash.
 */
fun approvalCauseLabel(cause: String?): String? {
    if (cause.isNullOrBlank()) return null
    return when (cause) {
        "red_zone" -> "Kırmızı bölge kuralı"
        "capability_request" -> "Yetki isteği"
        else -> "Neden bilinmiyor"
    }
}

/**
 * Turkish display label for a trust level slug.
 * Fail-closed: an unknown trust level returns "Güven düzeyi bilinmiyor".
 */
fun approvalTrustLabel(trustLevel: String?): String? {
    if (trustLevel.isNullOrBlank()) return null
    return when (trustLevel) {
        "HIGH" -> "Yüksek güvenle"
        "MEDIUM" -> "Orta güvenle"
        "LOW" -> "Düşük güvenle"
        else -> "Güven düzeyi bilinmiyor"
    }
}

/**
 * Turkish display label for reversibility.
 */
fun approvalReversibilityLabel(reversible: Boolean?): String? = when (reversible) {
    true -> "Geri alınabilir"
    false -> "Geri alınamaz"
    null -> null
}
