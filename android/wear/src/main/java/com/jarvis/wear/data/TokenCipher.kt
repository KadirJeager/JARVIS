package com.jarvis.wear.data

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Token'ı aygıt-bağlı anahtarla şifreler. Arayüz, JVM testinde sahtelenebilsin
 * diye ayrı (Keystore yalnız cihazda/emülatörde var). */
interface TokenCipher {
    fun encrypt(plain: String): String
    fun decrypt(blob: String): String?
}

/** Android Keystore AES/GCM. Blob biçimi: Base64(iv) + ":" + Base64(ciphertext).
 * androidx.security-crypto KULLANILMADI: kütüphane deprecated (2024) — Keystore
 * + GCM zaten platformun kendi mekanizması (spec §5 plan-anı doğrulaması). */
class KeystoreTokenCipher : TokenCipher {
    private val alias = "jarvis_wear_token"

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(alias, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(
            KeyGenParameterSpec.Builder(
                alias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build(),
        )
        return gen.generateKey()
    }

    override fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val ct = cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(cipher.iv, Base64.NO_WRAP) + ":" +
            Base64.encodeToString(ct, Base64.NO_WRAP)
    }

    override fun decrypt(blob: String): String? = try {
        val (ivB64, ctB64) = blob.split(":", limit = 2)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.DECRYPT_MODE,
            key(),
            GCMParameterSpec(128, Base64.decode(ivB64, Base64.NO_WRAP)),
        )
        String(cipher.doFinal(Base64.decode(ctB64, Base64.NO_WRAP)), Charsets.UTF_8)
    } catch (e: Exception) {
        null // bozuk blob / anahtar yenilendi → "eşleştirilmemiş" durumuna düş
    }
}
