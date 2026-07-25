package com.jarvis.data.auth

import android.content.Context
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_WEAK
import androidx.biometric.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity

/**
 * The authenticator set for the management screen.
 *
 * Verified against the 1.1.0 artifact: BIOMETRIC_WEAK is 0x00FF and BIOMETRIC_STRONG is
 * 0x000F, so WEAK's mask already accepts a strong biometric. DEVICE_CREDENTIAL alone and
 * BIOMETRIC_STRONG|DEVICE_CREDENTIAL are BOTH unsupported on API 28-29 while our minSdk
 * is 26, so this is the only combination legal everywhere we ship. We hold no
 * CryptoObject — this gate protects a screen, it does not unwrap a key — so nothing here
 * needs STRONG.
 */
const val GATE_AUTHENTICATORS = BIOMETRIC_WEAK or DEVICE_CREDENTIAL

/**
 * The client half of spec §7's two layers. It is a REAL protection against a real
 * threat (someone holding an unlocked phone) — and the server independently applies its
 * own brakes, because it cannot verify that this ever ran.
 *
 * For that reason NOTHING here ever reaches the network: no "biometric passed" header
 * exists, and none may be added. An unverifiable client assertion is not a signal
 * (spec §7, same class as `presence`).
 */
interface BiometricGate {
    /** Whether this device can satisfy [GATE_AUTHENTICATORS] at all. */
    fun isAvailable(): Boolean

    /** Shows the prompt; [onResult] receives success or the failure reason. */
    fun prompt(activity: FragmentActivity, onResult: (Result<Unit>) -> Unit)
}

class AndroidBiometricGate(context: Context) : BiometricGate {

    private val appContext = context.applicationContext

    override fun isAvailable(): Boolean =
        BiometricManager.from(appContext).canAuthenticate(GATE_AUTHENTICATORS) ==
            BiometricManager.BIOMETRIC_SUCCESS

    override fun prompt(activity: FragmentActivity, onResult: (Result<Unit>) -> Unit) {
        val prompt = BiometricPrompt(
            activity,
            ContextCompat.getMainExecutor(appContext),
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    onResult(Result.success(Unit))
                }

                override fun onAuthenticationError(code: Int, message: CharSequence) {
                    // Terminal: cancelled, locked out, no hardware. onAuthenticationFailed
                    // (a single bad fingerprint) is deliberately NOT handled — the prompt
                    // stays up and lets the user try again.
                    onResult(Result.failure(IllegalStateException(message.toString())))
                }
            },
        )
        val info = BiometricPrompt.PromptInfo.Builder()
            .setTitle("Ses kimliğin")
            .setSubtitle("Devam etmek için kimliğini doğrula")
            .setAllowedAuthenticators(GATE_AUTHENTICATORS)
            // setNegativeButtonText MUST NOT be called alongside DEVICE_CREDENTIAL —
            // build() throws. The system supplies the cancel affordance itself.
            .build()
        prompt.authenticate(info)
    }
}
