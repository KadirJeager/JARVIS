package com.jarvis.wear.data

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/** Küçük anahtar-değer sözleşmesi: üretimde DataStore Preferences'a bağlanır,
 * JVM testinde FakePrefs. (Tek kavram tek isim: anahtar = KEY_TOKEN.)
 * Üretim `DataStorePrefs` gerçeklemesi Task 4'te Application kurulumuyla gelir —
 * DataStore Context ister, bu task JVM-saf kalır. */
interface Prefs {
    suspend fun get(key: String): String?
    suspend fun put(key: String, value: String)
    suspend fun remove(key: String)
    fun watch(key: String): Flow<String?>
}

/** Cihaz token'ını (jdt_...) tutar. Düz metin ASLA [prefs]'e yazılmaz — her save/read
 * [cipher]'dan geçer, bkz. [KeystoreTokenCipher]. */
class TokenStore(private val prefs: Prefs, private val cipher: TokenCipher) {
    companion object { const val KEY_TOKEN = "device_token_blob" }

    suspend fun save(token: String) = prefs.put(KEY_TOKEN, cipher.encrypt(token))

    suspend fun read(): String? = prefs.get(KEY_TOKEN)?.let { cipher.decrypt(it) }

    suspend fun clear() = prefs.remove(KEY_TOKEN)

    val hasToken: Flow<Boolean> = prefs.watch(KEY_TOKEN).map { it != null }
}
