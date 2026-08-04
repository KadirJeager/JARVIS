package com.jarvis.wear.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

/**
 * Uygulamanın TEK Preferences DataStore'u. Burada, bir kez tanımlanır: `preferencesDataStore`
 * delegesi süreç içinde aynı dosya adıyla yalnızca bir kez oluşturulabilir — ikinci bir
 * delege (ör. bu satırın başka bir dosyaya kopyalanması) çalışma zamanında patlar
 * ("There are multiple DataStores active for the same file"), üstelik yalnız o ikinci
 * depoya ilk dokunulduğunda (telefonun `data/JarvisDataStore.kt`'siyle aynı ders).
 */
private val Context.wearDataStore by preferencesDataStore(name = "jarvis_wear")

/**
 * [Prefs]'in gerçek DataStore Preferences gerçeklemesi — yalnız cihazda/emülatörde
 * çalışır (Context ister). JVM testlerinde bunun yerine [FakePrefs] kullanılır.
 */
class DataStorePrefs(context: Context) : Prefs {
    private val appContext = context.applicationContext

    override suspend fun get(key: String): String? =
        appContext.wearDataStore.data.first()[stringPreferencesKey(key)]

    override suspend fun put(key: String, value: String) {
        appContext.wearDataStore.edit { it[stringPreferencesKey(key)] = value }
    }

    override suspend fun remove(key: String) {
        appContext.wearDataStore.edit { it.remove(stringPreferencesKey(key)) }
    }

    override fun watch(key: String): Flow<String?> =
        appContext.wearDataStore.data.map { it[stringPreferencesKey(key)] }
}
