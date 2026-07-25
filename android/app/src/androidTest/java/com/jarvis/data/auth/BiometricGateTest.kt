package com.jarvis.data.auth

import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_WEAK
import androidx.biometric.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class BiometricGateTest {

    /**
     * DEVICE_CREDENTIAL alone and BIOMETRIC_STRONG|DEVICE_CREDENTIAL are unsupported on
     * API 28-29, and minSdk here is 26. BIOMETRIC_WEAK (0x00FF) is a superset mask of
     * BIOMETRIC_STRONG (0x000F), so the weak-or-credential combination still accepts a
     * strong biometric while staying legal on every level we ship to. We hold no
     * CryptoObject, so there is no reason to demand STRONG.
     */
    @Test
    fun theGateUsesTheOneCombinationLegalOnEveryApiLevelWeShipTo() {
        assertEquals(BIOMETRIC_WEAK or DEVICE_CREDENTIAL, GATE_AUTHENTICATORS)
        assertTrue(
            "WEAK maskesi STRONG'u kapsamalı",
            BIOMETRIC_WEAK and BIOMETRIC_STRONG == BIOMETRIC_STRONG,
        )
        assertTrue(
            "STRONG|DEVICE_CREDENTIAL kullanılmamalı (API<=29'da desteklenmiyor)",
            GATE_AUTHENTICATORS != (BIOMETRIC_STRONG or DEVICE_CREDENTIAL),
        )
    }

    /**
     * The real query must run against the real BiometricManager on the real device:
     * a headless emulator with no lock reports "not available", which is exactly the
     * branch the screen must survive.
     */
    @Test
    fun availabilityQueryRunsWithoutThrowingOnThisDevice() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val gate = AndroidBiometricGate(context)
        assertNotNull(gate.isAvailable())     // true or false, but never a crash
    }
}
