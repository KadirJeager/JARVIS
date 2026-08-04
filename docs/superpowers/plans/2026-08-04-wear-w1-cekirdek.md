# Wear W1 — Saat Çekirdeği Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `android/wear` modülü: tek seferlik telefon eşleştirmesi (kalıcı `jdt_` token) + sohbet çekirdeği + hızlı komutlar + sesli komut/TTS — tamamı `bd_watch` emülatöründe (android-36, gerçek Galaxy Watch Ultra'nın güncel Wear OS 6'sıyla birebir) doğrulanır. Spec: `docs/superpowers/specs/2026-08-04-wear-os-design.md` §5.

**Architecture:** Saat standalone: brain REST'ine doğrudan (`POST /api/chat`, Bearer `jdt_`). Kimlik W0'ın canlıdaki altyapısı: telefon `POST /api/device-tokens` ile basar, Wearable `MessageClient` ile saate iter (yalnız eşleştirme anında); saat token'ı Android Keystore AES/GCM ile şifreleyip DataStore'da saklar. Telefon uygulamasına tek eylem eklenir ("Saati eşleştir"). **Spec'ten bilinçli sapma:** Horologist v1'e ALINMADI (YAGNI — düz wear-compose yeterli; ihtiyaç doğunca gelir).

**Tech Stack:** Kotlin 2.4.10, AGP 9.3.0, compileSdk/targetSdk 36, Java 21, Compose for Wear OS (stable `compose-material` hattı), Retrofit 3 + kotlinx-serialization + OkHttp 5.4 (telefonla aynı), DataStore 1.2.1, play-services-wearable, MockWebServer (JVM testleri).

## Global Constraints

- **Emülatör-önce (Kadir'in şartı):** hiçbir adım gerçek saate KURULUM yapmaz. Doğrulama `bd_watch` AVD'de (mevcut, android-36/android-wear-signed). Telefon tarafı APK yalnız S23'e (`PHONE_SERIAL`, USB'de takılı) — Kadir'in günlük akışı.
- **Android build'leri `gbuild.sh` bellek muhafızıyla koşar ve ÇIKTISI PIPE'LANMAZ** — exit kodu kontrol edilir, "kurdum" demeden önce APK zaman damgasına bakılır (LESSONS 4 Ağu, bu oturumda bir kez daha yaşandı).
- Düz `jdt_` token saatte YALNIZ şifreli saklanır (Keystore AES/GCM + DataStore); loglanmaz, ekranda gösterilmez.
- Token yokken saat DÜRÜST ekran gösterir ("Telefondan eşleştir"); sessiz boş ekran/sonsuz spinner YOK (25 Tem dersi).
- Sohbet oturum kimliği `wear-` önekli (telefonun `web-`/oturum düzenine paralel; sunucu `sanitize_session_id`'den geçirir).
- Wear kütüphane sürümleri task anında maven.google.com metadata'sından çözülür; **stable seçilir** (compose-material3 for Wear hâlâ alpha — KULLANILMAZ). Çözülen sürümler commit mesajına yazılır.
- Yeni modül telefonun kalıplarını izler: version catalog, kotlinx-serialization, Retrofit; docstring/UI metinleri Türkçe, tanımlayıcılar İngilizce.
- JVM testleri her task'ta; emülatör E2E Task 7'de tek yerde (ekran görüntüsü kanıttır — 3 Ağu dersi).
- Commit'ler repo kökünden, mesaj sonu:
`Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: `wear` modül iskeleti

**Files:**
- Modify: `android/settings.gradle.kts` (include `:wear`)
- Modify: `android/gradle/libs.versions.toml` (wear sürümleri + kütüphaneleri)
- Create: `android/wear/build.gradle.kts`
- Create: `android/wear/src/main/AndroidManifest.xml`
- Create: `android/wear/src/main/java/com/jarvis/wear/MainActivity.kt`
- Create: `android/wear/src/main/res/values/strings.xml`

**Interfaces:**
- Produces: derlenen `:wear` modülü, paket `com.jarvis.wear`, `applicationId "com.jarvis.wear"`, minSdk 34, standalone wear app manifesti. Sonraki task'lar bu modüle dosya ekler.

- [ ] **Step 1: Sürümleri çöz** — şu komutla stable sürümleri oku ve not et:

```bash
for a in compose-material compose-foundation compose-navigation; do
  echo "== $a =="; curl -sL "https://maven.google.com/androidx/wear/compose/$a/maven-metadata.xml" | grep -oE "<version>[0-9]+\.[0-9]+\.[0-9]+</version>" | tail -3
done
curl -sL "https://maven.google.com/com/google/android/gms/play-services-wearable/maven-metadata.xml" | grep -oE "<version>[0-9]+\.[0-9]+\.[0-9]+</version>" | tail -3
```

(Desen yalnız `X.Y.Z` düz sürümleri yakalar — alpha/beta/rc elenmiş olur; en sondaki = en yeni stable.)

- [ ] **Step 2: Catalog'a ekle** — `libs.versions.toml`:

```toml
# [versions] bölümüne (Step 1'de çözülen değerlerle):
wearCompose = "<çözülen>"
playServicesWearable = "<çözülen>"

# [libraries] bölümüne:
wear-compose-material = { group = "androidx.wear.compose", name = "compose-material", version.ref = "wearCompose" }
wear-compose-foundation = { group = "androidx.wear.compose", name = "compose-foundation", version.ref = "wearCompose" }
wear-compose-navigation = { group = "androidx.wear.compose", name = "compose-navigation", version.ref = "wearCompose" }
play-services-wearable = { group = "com.google.android.gms", name = "play-services-wearable", version.ref = "playServicesWearable" }
```

- [ ] **Step 3: `settings.gradle.kts`** — mevcut `include(":app")` satırının yanına `include(":wear")`.

- [ ] **Step 4: `android/wear/build.gradle.kts`** — `:app`'in yapısını izleyerek:

```kotlin
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "com.jarvis.wear"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.jarvis.wear"
        minSdk = 34          // Wear OS 5 tabanı; gerçek saat ve bd_watch AVD 36'da
        targetSdk = 36
        versionCode = 1
        versionName = "0.1"
    }

    buildTypes {
        release { isMinifyEnabled = false }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_21
        targetCompatibility = JavaVersion.VERSION_21
    }
    kotlin { compilerOptions { jvmTarget.set(JvmTarget.JVM_21) } }
    buildFeatures { compose = true }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.wear.compose.material)
    implementation(libs.wear.compose.foundation)
    implementation(libs.wear.compose.navigation)
    implementation(libs.play.services.wearable)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.retrofit)
    implementation(libs.retrofit.converter.kotlinx.serialization)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver3)
}
```

(`:app`'in build dosyasındaki compose/kotlin blok sözdizimini birebir izle — AGP 9.3'te alan adları oradakiyle aynı olmalı. `:app` `buildFeatures`/`composeOptions`'ı nasıl yazıyorsa kopyala.)

- [ ] **Step 5: Manifest** — `android/wear/src/main/AndroidManifest.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <uses-feature android:name="android.hardware.type.watch" />
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.RECORD_AUDIO" />

    <application
        android:label="@string/app_name"
        android:icon="@android:drawable/ic_dialog_info"
        android:theme="@android:style/Theme.DeviceDefault">
        <!-- Standalone: saat, telefonsuz kendi ağıyla çalışır (spec §2/A) -->
        <meta-data android:name="com.google.android.wearable.standalone" android:value="true" />

        <activity android:name=".MainActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
```

`strings.xml`: `<string name="app_name">Jarvis</string>`

- [ ] **Step 6: İskelet `MainActivity`** — `com.jarvis.wear.MainActivity`:

```kotlin
package com.jarvis.wear

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.wear.compose.material.MaterialTheme
import androidx.wear.compose.material.Text

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { WearRoot() }
    }
}

@Composable
fun WearRoot() {
    MaterialTheme {
        Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Text("Jarvis")
        }
    }
}
```

- [ ] **Step 7: Derle ve DOĞRULA** (pipe YOK; exit kodu + APK damgası):

```bash
cd /home/user/Projeler/JARVIS/android
./gbuild.sh :wear:assembleDebug ; echo "exit=$?"
ls -la wear/build/outputs/apk/debug/wear-debug.apk
```

(`gbuild.sh` repo'da yoksa `./gradlew :wear:assembleDebug` kullan — yine pipe'sız, `echo exit=$?` ile. `:app:assembleDebug`'ın da hâlâ derlendiğini bir kez doğrula: catalog değişikliği telefonu kırmamalı.)

- [ ] **Step 8: Commit**

```bash
git add android/settings.gradle.kts android/gradle/libs.versions.toml android/wear/
git commit -m "feat(wear): scaffold standalone wear module (Compose for Wear, minSdk 34)

Resolved stable versions: wear-compose <X>, play-services-wearable <Y>.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Token deposu (Keystore AES/GCM) + Jarvis API istemcisi

**Files:**
- Create: `android/wear/src/main/java/com/jarvis/wear/data/TokenCipher.kt`
- Create: `android/wear/src/main/java/com/jarvis/wear/data/TokenStore.kt`
- Create: `android/wear/src/main/java/com/jarvis/wear/data/JarvisApi.kt`
- Create: `android/wear/src/main/java/com/jarvis/wear/data/Net.kt`
- Test: `android/wear/src/test/java/com/jarvis/wear/data/TokenStoreTest.kt`
- Test: `android/wear/src/test/java/com/jarvis/wear/data/JarvisApiTest.kt`

**Interfaces:**
- Consumes: W0'ın canlı sözleşmesi — `Authorization: Bearer jdt_...`; `POST /api/chat {session_id, message} -> {reply}`; 401 = token geçersiz/iptal.
- Produces:
  - `TokenCipher` arayüzü: `fun encrypt(plain: String): String` / `fun decrypt(blob: String): String?` + `KeystoreTokenCipher` üretim gerçeklemesi (AES/GCM, Keystore alias `"jarvis_wear_token"`, IV blob'un başında, Base64)
  - `TokenStore(dataStore, cipher)`: `suspend fun save(token: String)`, `suspend fun read(): String?`, `suspend fun clear()`, `val hasToken: Flow<Boolean>`
  - `JarvisApi`: `suspend fun chat(sessionId, message): String` (reply döner); `class UnauthorizedException` (401'de — UI "yeniden eşleştir"e düşer)
  - `Net.buildApi(baseUrl, tokenProvider): JarvisApi`

- [ ] **Step 1: Başarısız JVM testlerini yaz**

`TokenStoreTest.kt` (cipher FAKE — Keystore JVM'de yok; depo sözleşmesi gerçek DataStore-benzeri fake ile):

```kotlin
package com.jarvis.wear.data

import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Sözleşme: düz token depoya ASLA düz yazılmaz; cipher'dan geçer. */
class FakeCipher : TokenCipher {
    override fun encrypt(plain: String) = "enc(" + plain.reversed() + ")"
    override fun decrypt(blob: String) =
        if (blob.startsWith("enc(")) blob.removePrefix("enc(").removeSuffix(")").reversed() else null
}

class TokenStoreTest {

    private fun store(backing: FakePrefs = FakePrefs()) =
        Pair(TokenStore(backing, FakeCipher()), backing)

    @Test fun `save then read roundtrips through the cipher`() = runTest {
        val (s, backing) = store()
        s.save("jdt_gizli")
        assertEquals("jdt_gizli", s.read())
        // Depoda düz token YOK — yalnız cipher çıktısı var:
        assertFalse(backing.dump().contains("jdt_gizli"))
        assertTrue(backing.dump().contains("enc("))
    }

    @Test fun `read returns null when nothing saved`() = runTest {
        val (s, _) = store()
        assertNull(s.read())
    }

    @Test fun `clear removes the token and hasToken follows`() = runTest {
        val (s, _) = store()
        s.save("jdt_gizli")
        assertTrue(s.hasToken.first())
        s.clear()
        assertFalse(s.hasToken.first())
        assertNull(s.read())
    }

    @Test fun `corrupted blob reads as null not crash`() = runTest {
        val (s, backing) = store()
        backing.put(TokenStore.KEY_TOKEN, "bozuk-blob")
        assertNull(s.read())
    }
}
```

`JarvisApiTest.kt` (MockWebServer — gerçek HTTP + serileştirme):

```kotlin
package com.jarvis.wear.data

import kotlinx.coroutines.test.runTest
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class JarvisApiTest {

    private fun withServer(block: suspend (MockWebServer, JarvisApi) -> Unit) = runTest {
        MockWebServer().use { server ->
            server.start()
            val api = Net.buildApi(server.url("/").toString()) { "jdt_test" }
            block(server, api)
        }
    }

    @Test fun `chat posts session and message with the bearer and returns reply`() =
        withServer { server, api ->
            server.enqueue(MockResponse(body = """{"reply":"Merhaba Kadir"}"""))
            val reply = api.chat("wear-1", "selam")
            assertEquals("Merhaba Kadir", reply)
            val req = server.takeRequest()
            assertEquals("/api/chat", req.url.encodedPath)
            assertEquals("Bearer jdt_test", req.headers["Authorization"])
            assertTrue(req.body.utf8().contains(""""session_id":"wear-1""""))
            assertTrue(req.body.utf8().contains(""""message":"selam""""))
        }

    @Test fun `401 raises UnauthorizedException`() =
        withServer { server, api ->
            server.enqueue(MockResponse(code = 401, body = """{"detail":"Geçersiz oturum"}"""))
            try {
                api.chat("wear-1", "selam")
                fail("UnauthorizedException bekleniyordu")
            } catch (expected: UnauthorizedException) { }
        }

    @Test fun `5xx surfaces a Turkish error message`() =
        withServer { server, api ->
            server.enqueue(MockResponse(code = 502, body = """{"detail":"altyapı"}"""))
            try {
                api.chat("wear-1", "selam")
                fail("JarvisApiException bekleniyordu")
            } catch (e: JarvisApiException) {
                assertTrue(e.userMessage.isNotBlank())
            }
        }
}
```

(MockWebServer3 API'si telefon test kodunda zaten kullanılıyorsa — `grep -rn "mockwebserver" android/app/src/test/` — oradaki import/kurulum desenini birebir izle; kütüphane sürümü catalog'dan geliyor.)

- [ ] **Step 2: FAIL gör**

```bash
cd /home/user/Projeler/JARVIS/android && ./gradlew :wear:testDebugUnitTest --tests "com.jarvis.wear.data.*" ; echo "exit=$?"
```
Expected: derleme hatası (sınıflar yok)

- [ ] **Step 3: Uygula**

`TokenCipher.kt`:

```kotlin
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
            KeyGenParameterSpec.Builder(alias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build())
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
        cipher.init(Cipher.DECRYPT_MODE, key(),
            GCMParameterSpec(128, Base64.decode(ivB64, Base64.NO_WRAP)))
        String(cipher.doFinal(Base64.decode(ctB64, Base64.NO_WRAP)), Charsets.UTF_8)
    } catch (e: Exception) {
        null   // bozuk blob / anahtar yenilendi → "eşleştirilmemiş" durumuna düş
    }
}
```

`TokenStore.kt` — DataStore soyutlaması test edilebilir olsun diye küçük bir arayüz üstünden:

```kotlin
package com.jarvis.wear.data

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/** Küçük anahtar-değer sözleşmesi: üretimde DataStore Preferences'a bağlanır,
 * JVM testinde FakePrefs. (Tek kavram tek isim: anahtar = KEY_TOKEN.) */
interface Prefs {
    suspend fun get(key: String): String?
    suspend fun put(key: String, value: String)
    suspend fun remove(key: String)
    fun watch(key: String): Flow<String?>
}

class TokenStore(private val prefs: Prefs, private val cipher: TokenCipher) {
    companion object { const val KEY_TOKEN = "device_token_blob" }

    suspend fun save(token: String) = prefs.put(KEY_TOKEN, cipher.encrypt(token))

    suspend fun read(): String? = prefs.get(KEY_TOKEN)?.let { cipher.decrypt(it) }

    suspend fun clear() = prefs.remove(KEY_TOKEN)

    val hasToken: Flow<Boolean> = prefs.watch(KEY_TOKEN).map { it != null }
}
```

`FakePrefs` test yardımcısı (test kaynak setine, `TokenStoreTest.kt` yanına ayrı dosya ya da aynı dosyada):

```kotlin
class FakePrefs : Prefs {
    private val map = LinkedHashMap<String, String>()
    private val flow = kotlinx.coroutines.flow.MutableStateFlow<Map<String, String>>(emptyMap())
    override suspend fun get(key: String) = map[key]
    override suspend fun put(key: String, value: String) { map[key] = value; flow.value = map.toMap() }
    override suspend fun remove(key: String) { map.remove(key); flow.value = map.toMap() }
    override fun watch(key: String) = kotlinx.coroutines.flow.map(flow) { it[key] }
    fun dump() = map.toString()
}
```

(Üretim `DataStorePrefs` gerçeklemesi Task 4'te Application kurulumuyla gelir — DataStore Context ister, bu task JVM-saf kalır.)

`JarvisApi.kt` + `Net.kt`:

```kotlin
package com.jarvis.wear.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.HttpException
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import retrofit2.http.Body
import retrofit2.http.POST
import java.util.concurrent.TimeUnit

@Serializable data class ChatRequest(val session_id: String, val message: String)
@Serializable data class ChatResponse(val reply: String)

class UnauthorizedException : Exception("Oturum geçersiz; saati telefondan yeniden eşleştir.")
class JarvisApiException(val userMessage: String) : Exception(userMessage)

interface JarvisService {
    @POST("api/chat")
    suspend fun chat(@Body body: ChatRequest): ChatResponse
}

class JarvisApi(private val service: JarvisService) {
    /** reply döner; 401 -> UnauthorizedException, diğer hatalar -> JarvisApiException
     * (Türkçe kullanıcı mesajı — saat ekranında olduğu gibi gösterilir). */
    suspend fun chat(sessionId: String, message: String): String = try {
        service.chat(ChatRequest(sessionId, message)).reply
    } catch (e: HttpException) {
        if (e.code() == 401) throw UnauthorizedException()
        throw JarvisApiException("Jarvis'e ulaşılamadı (${e.code()}). Az sonra tekrar dene.")
    } catch (e: Exception) {
        if (e is UnauthorizedException || e is JarvisApiException) throw e
        throw JarvisApiException("Ağ hatası: Jarvis'e ulaşılamadı. Bağlantını kontrol et.")
    }
}

object Net {
    private val json = Json { ignoreUnknownKeys = true }

    fun buildApi(baseUrl: String, tokenProvider: () -> String?): JarvisApi {
        val client = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)   // chat turu LLM bekler (telefonla aynı sınıf)
            .addInterceptor { chain ->
                val token = tokenProvider()
                val req = if (token != null)
                    chain.request().newBuilder().header("Authorization", "Bearer $token").build()
                else chain.request()
                chain.proceed(req)
            }
            .build()
        val retrofit = Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
        return JarvisApi(retrofit.create(JarvisService::class.java))
    }
}
```

(Telefonun `data/net/NetworkModule.kt`'sini OKU ve converter/timeout kurulum sözdizimini oradan doğrula — Retrofit 3'ün `asConverterFactory` importu telefonda nasılsa aynen. Prod base URL Task 4'te bağlanır: `https://jarvis-brain-000000000000.europe-west1.run.app/`.)

- [ ] **Step 4: PASS gör**

```bash
cd /home/user/Projeler/JARVIS/android && ./gradlew :wear:testDebugUnitTest ; echo "exit=$?"
```
Expected: exit=0, yeni testlerin tamamı yeşil

- [ ] **Step 5: Commit**

```bash
git add android/wear/
git commit -m "feat(wear): keystore-encrypted token store and Jarvis chat client

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Telefon tarafı — "Saati eşleştir"

**Files:**
- Modify: `android/gradle/libs.versions.toml` (play-services-wearable telefona da)
- Modify: `android/app/build.gradle.kts` (dependency)
- Create: `android/app/src/main/java/com/jarvis/data/wear/WatchPairing.kt`
- Modify: `android/app/src/main/java/com/jarvis/data/net/...` (mevcut Retrofit servisine `POST api/device-tokens` ekle — dosyayı OKUYUP mevcut uçların stiliyle)
- Modify: telefon UI — "Saati eşleştir" eylemi (aşağıda; dosya seçimi mevcut UI okunarak)
- Test: `android/app/src/test/java/com/jarvis/data/wear/WatchPairingTest.kt`

**Interfaces:**
- Consumes: W0 canlı ucu `POST /api/device-tokens {device} -> {token, id, device, expires_at}` (yalnız Google-taze Bearer ile — telefonun mevcut `AuthManager.currentToken` yolu); Wearable `MessageClient`.
- Produces: `WatchPairing.pair(): PairResult` — token basar, bağlı düğümlere `/jarvis/device-token` path'iyle iter; `PairResult` = `Sent(nodeCount)` | `NoWatch` | `Failed(userMessage)`. Saat tarafı (Task 4) bu path'i dinler.

- [ ] **Step 1: Başarısız JVM testini yaz** — `WatchPairingTest.kt`:

```kotlin
package com.jarvis.data.wear

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** MessageClient ve API, arayüz arkasına alınır: JVM testi gerçek Play Services
 * istemez. Sözleşme: (1) önce mint, (2) TÜM bağlı düğümlere gönder, (3) düğüm
 * yoksa mint bile YAPILMAZ (boşa token basılmaz), (4) hata Türkçe mesajla döner. */
class WatchPairingTest {

    private class FakeMinter(var result: Result<String> = Result.success("jdt_yeni")) {
        var calls = 0
        suspend fun mint(device: String): String { calls++; return result.getOrThrow() }
    }

    private class FakeNodes(var nodes: List<String> = listOf("node1")) {
        val sent = mutableListOf<Pair<String, String>>()   // (nodeId, payload)
        suspend fun connectedNodes() = nodes
        suspend fun send(nodeId: String, path: String, payload: ByteArray) {
            require(path == WatchPairing.TOKEN_PATH)
            sent += nodeId to String(payload, Charsets.UTF_8)
        }
    }

    private fun pairing(minter: FakeMinter, nodes: FakeNodes) =
        WatchPairing(mint = minter::mint, listNodes = nodes::connectedNodes, sendTo = nodes::send)

    @Test fun `pairs by minting once and sending to every node`() = runTest {
        val minter = FakeMinter(); val nodes = FakeNodes(listOf("a", "b"))
        val result = pairing(minter, nodes).pair()
        assertEquals(PairResult.Sent(2), result)
        assertEquals(1, minter.calls)
        assertEquals(listOf("a" to "jdt_yeni", "b" to "jdt_yeni"), nodes.sent)
    }

    @Test fun `no connected watch means no mint at all`() = runTest {
        val minter = FakeMinter(); val nodes = FakeNodes(emptyList())
        assertEquals(PairResult.NoWatch, pairing(minter, nodes).pair())
        assertEquals(0, minter.calls)
    }

    @Test fun `mint failure surfaces a Turkish message`() = runTest {
        val minter = FakeMinter(Result.failure(RuntimeException("500")))
        val result = pairing(minter, FakeNodes()).pair()
        assertTrue(result is PairResult.Failed)
        assertTrue((result as PairResult.Failed).userMessage.isNotBlank())
    }
}
```

- [ ] **Step 2: FAIL gör** — `./gradlew :app:testDebugUnitTest --tests "com.jarvis.data.wear.*" ; echo "exit=$?"` → derleme hatası

- [ ] **Step 3: Uygula** — `WatchPairing.kt`:

```kotlin
package com.jarvis.data.wear

/** Saat eşleştirme (Wear W1, spec §5): telefon kalıcı cihaz token'ı basar ve
 * Wearable MessageClient ile saate iter. Köprü YALNIZ bu akışta kullanılır —
 * kalıcı token saatin telefon bağımlılığını bilinçli olarak yok eder (W0). */
sealed class PairResult {
    data class Sent(val nodeCount: Int) : PairResult()
    object NoWatch : PairResult()
    data class Failed(val userMessage: String) : PairResult()

    override fun equals(other: Any?) = this === other ||
        (this is Sent && other is Sent && nodeCount == other.nodeCount)
    override fun hashCode() = javaClass.hashCode()
}

class WatchPairing(
    private val mint: suspend (device: String) -> String,
    private val listNodes: suspend () -> List<String>,
    private val sendTo: suspend (nodeId: String, path: String, payload: ByteArray) -> Unit,
) {
    companion object {
        const val TOKEN_PATH = "/jarvis/device-token"
        const val DEVICE_LABEL = "watch-ultra"
    }

    suspend fun pair(): PairResult {
        val nodes = try { listNodes() } catch (e: Exception) {
            return PairResult.Failed("Saate ulaşılamadı; saat bağlı mı?")
        }
        if (nodes.isEmpty()) return PairResult.NoWatch
        val token = try { mint(DEVICE_LABEL) } catch (e: Exception) {
            return PairResult.Failed("Token basılamadı; oturumun taze mi? Tekrar dene.")
        }
        val payload = token.toByteArray(Charsets.UTF_8)
        var sent = 0
        for (node in nodes) {
            try { sendTo(node, TOKEN_PATH, payload); sent++ } catch (e: Exception) { /* düğüm düşmüş olabilir */ }
        }
        return if (sent > 0) PairResult.Sent(sent)
        else PairResult.Failed("Saate gönderilemedi; saat bağlı mı?")
    }
}
```

Üretim bağlaması (aynı dosyada ya da küçük bir `WatchPairingFactory`):

```kotlin
// Üretim: MessageClient + Retrofit. Nodes/send Google Play Wearable'dan:
//   Wearable.getNodeClient(ctx).connectedNodes.await() -> map { it.id }
//   Wearable.getMessageClient(ctx).sendMessage(nodeId, path, payload).await()
// (kotlinx-coroutines-play-services 'await()' için gerekliyse catalog'a ekle —
//  telefonda zaten varsa mevcut kullanımı izle; yoksa Tasks.await ile sar.)
// mint: mevcut Retrofit servisine eklenen uç:
//   @POST("api/device-tokens") suspend fun mintDeviceToken(@Body body: DeviceTokenRequest): DeviceTokenResponse
```

Retrofit ekini telefonun `data/net` dosyalarını okuyarak mevcut uçların stiliyle yaz (`DeviceTokenRequest(device)`, `DeviceTokenResponse(token, id, device, expires_at)` — kotlinx-serialization).

UI girişi: telefon UI'ında ayar/menü nerede yaşıyorsa oraya tek eylem — önce `ui/chat` ve `Nav.kt`'yi OKU; mevcut desene göre (ör. sohbet ekranındaki menüye "Saati eşleştir" öğesi) ekle; sonuç `PairResult`'a göre Türkçe snackbar/toast: Sent→"Saat eşleştirildi", NoWatch→"Bağlı saat bulunamadı", Failed→userMessage. UI değişikliğini MİNİMAL tut (tek menü öğesi + geri bildirim), mevcut ekran düzenine dokunma.

- [ ] **Step 4: PASS gör + telefon derlemesi**

```bash
cd /home/user/Projeler/JARVIS/android && ./gradlew :app:testDebugUnitTest ; echo "exit=$?"
./gradlew :app:assembleDebug ; echo "exit=$?"
```

- [ ] **Step 5: Commit**

```bash
git add android/app/ android/gradle/libs.versions.toml
git commit -m "feat(app): watch pairing — mint device token and push via MessageClient

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Saat tarafı — eşleştirme alıcısı + kök yönlendirme

**Files:**
- Create: `android/wear/src/main/java/com/jarvis/wear/data/TokenListenerService.kt`
- Create: `android/wear/src/main/java/com/jarvis/wear/data/DataStorePrefs.kt`
- Create: `android/wear/src/main/java/com/jarvis/wear/WearApp.kt` (Application — depo/API tekil kurulumu)
- Modify: `android/wear/src/main/AndroidManifest.xml` (service + application name)
- Modify: `android/wear/src/main/java/com/jarvis/wear/MainActivity.kt` (kök yönlendirme)
- Create: `android/wear/src/main/java/com/jarvis/wear/ui/PairScreen.kt`
- Test: `android/wear/src/test/java/com/jarvis/wear/RootRouteTest.kt`

**Interfaces:**
- Consumes: Task 2 `TokenStore/KeystoreTokenCipher/Net`; Task 3'ün `TOKEN_PATH = "/jarvis/device-token"` sözleşmesi.
- Produces: `WearApp.tokenStore` / `WearApp.api` tekil erişimleri; `rootRoute(hasToken: Boolean): Route` (`Route.Pair` | `Route.Chat`) — Task 5 Chat ekranını bu yönlendirmeye takar.

- [ ] **Step 1: Başarısız testi yaz** — `RootRouteTest.kt`:

```kotlin
package com.jarvis.wear

import org.junit.Assert.assertEquals
import org.junit.Test

/** Dürüst ekran kuralı (spec §5): token yoksa sessiz boş ekran DEĞİL,
 * "Telefondan eşleştir" ekranı. */
class RootRouteTest {
    @Test fun `no token routes to pair screen`() = assertEquals(Route.Pair, rootRoute(false))
    @Test fun `token routes to chat`() = assertEquals(Route.Chat, rootRoute(true))
}
```

- [ ] **Step 2: FAIL gör** — `./gradlew :wear:testDebugUnitTest ; echo "exit=$?"`

- [ ] **Step 3: Uygula**

`Route` + `rootRoute` (MainActivity.kt içinde ya da ayrı `Route.kt`):

```kotlin
enum class Route { Pair, Chat }
fun rootRoute(hasToken: Boolean): Route = if (hasToken) Route.Chat else Route.Pair
```

`DataStorePrefs.kt` — Task 2'deki `Prefs` arayüzünün DataStore Preferences gerçeklemesi (standart `preferencesDataStore(name = "jarvis_wear")` deseni; `stringPreferencesKey`).

`WearApp.kt`:

```kotlin
package com.jarvis.wear

import android.app.Application
import com.jarvis.wear.data.DataStorePrefs
import com.jarvis.wear.data.JarvisApi
import com.jarvis.wear.data.KeystoreTokenCipher
import com.jarvis.wear.data.Net
import com.jarvis.wear.data.TokenStore
import kotlinx.coroutines.runBlocking

class WearApp : Application() {
    lateinit var tokenStore: TokenStore
        private set
    lateinit var api: JarvisApi
        private set

    override fun onCreate() {
        super.onCreate()
        tokenStore = TokenStore(DataStorePrefs(this), KeystoreTokenCipher())
        api = Net.buildApi(BASE_URL) { runBlocking { tokenStore.read() } }
        // runBlocking interceptor'da: OkHttp zaten worker thread'de çağırır,
        // main thread'e dokunmaz. (Telefonun AuthClient deseniyle aynı sınıf.)
    }

    companion object {
        const val BASE_URL = "https://jarvis-brain-000000000000.europe-west1.run.app/"
    }
}
```

`TokenListenerService.kt`:

```kotlin
package com.jarvis.wear.data

import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.WearableListenerService
import com.jarvis.wear.WearApp
import kotlinx.coroutines.runBlocking

/** Telefondan gelen kalıcı token'ı alır ve şifreli depoya yazar (tek seferlik
 * eşleştirme akışı — spec §5). Path sözleşmesi telefonun WatchPairing'iyle ortak. */
class TokenListenerService : WearableListenerService() {
    override fun onMessageReceived(event: MessageEvent) {
        if (event.path != "/jarvis/device-token") return
        val token = String(event.data, Charsets.UTF_8)
        if (!token.startsWith("jdt_")) return          // bilinmeyen yük — sessizce yok say + log
        runBlocking { (application as WearApp).tokenStore.save(token) }
    }
}
```

Manifest'e:

```xml
<service android:name=".data.TokenListenerService" android:exported="true">
    <intent-filter>
        <action android:name="com.google.android.gms.wearable.MESSAGE_RECEIVED" />
        <data android:scheme="wear" android:host="*" android:pathPrefix="/jarvis/device-token" />
    </intent-filter>
</service>
```

ve `<application android:name=".WearApp" ...>`.

`PairScreen.kt` — dürüst durum ekranı:

```kotlin
package com.jarvis.wear.ui

import androidx.compose.foundation.layout.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.wear.compose.material.Text

@Composable
fun PairScreen() {
    Column(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("Saat eşleştirilmemiş", textAlign = TextAlign.Center)
        Spacer(Modifier.height(8.dp))
        Text(
            "Telefonda Jarvis'i aç ve menüden 'Saati eşleştir'e dokun.",
            textAlign = TextAlign.Center,
        )
    }
}
```

`MainActivity` kökü: `tokenStore.hasToken` Flow'unu `collectAsState` ile izle, `rootRoute`'a göre `PairScreen()` / şimdilik `Text("Sohbet hazır")` (Task 5 dolduracak). Eşleştirme geldiği AN ekran kendiliğinden Chat'e geçer (Flow-tabanlı — LaunchedEffect/yenile beklemez; 4 Ağu overlay dersi sınıfı).

- [ ] **Step 4: PASS + derleme** — `./gradlew :wear:testDebugUnitTest :wear:assembleDebug ; echo "exit=$?"`

- [ ] **Step 5: Commit**

```bash
git add android/wear/
git commit -m "feat(wear): pairing receiver, encrypted store wiring and honest root routing

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Sohbet ekranı + hızlı komutlar

**Files:**
- Create: `android/wear/src/main/java/com/jarvis/wear/ui/ChatViewModel.kt`
- Create: `android/wear/src/main/java/com/jarvis/wear/ui/ChatScreen.kt`
- Modify: `android/wear/src/main/java/com/jarvis/wear/MainActivity.kt` (Chat rotasını bağla)
- Test: `android/wear/src/test/java/com/jarvis/wear/ui/ChatViewModelTest.kt`

**Interfaces:**
- Consumes: Task 2 `JarvisApi.chat` / `UnauthorizedException` / `JarvisApiException`; Task 4 `WearApp.api`, `rootRoute`.
- Produces: `ChatViewModel(api, sessionId)`: `val state: StateFlow<ChatState>`, `fun send(text: String)`; `ChatState(busy, lastQuestion, lastReply, error, needsPairing)`; `QUICK_COMMANDS` listesi. Task 6 `send`'i sesli girdiyle besler.

- [ ] **Step 1: Başarısız testleri yaz** — `ChatViewModelTest.kt`:

```kotlin
package com.jarvis.wear.ui

import com.jarvis.wear.data.JarvisApiException
import com.jarvis.wear.data.UnauthorizedException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class ChatViewModelTest {
    private val dispatcher = StandardTestDispatcher()

    @Before fun setUp() { Dispatchers.setMain(dispatcher) }
    @After fun tearDown() { Dispatchers.resetMain() }

    private class FakeApi(var result: suspend () -> String = { "cevap" }) {
        val calls = mutableListOf<Pair<String, String>>()
        suspend fun chat(sessionId: String, message: String): String {
            calls += sessionId to message; return result()
        }
    }

    private fun vm(api: FakeApi) = ChatViewModel(chat = api::chat, sessionId = "wear-test")

    @Test fun `send goes through the api and lands in state`() = runTest {
        val api = FakeApi()
        val v = vm(api)
        v.send("durum raporu")
        advanceUntilIdle()
        assertEquals(listOf("wear-test" to "durum raporu"), api.calls)
        assertEquals("cevap", v.state.value.lastReply)
        assertEquals("durum raporu", v.state.value.lastQuestion)
        assertFalse(v.state.value.busy)
        assertNull(v.state.value.error)
    }

    @Test fun `blank input is ignored`() = runTest {
        val api = FakeApi()
        vm(api).send("   ")
        advanceUntilIdle()
        assertTrue(api.calls.isEmpty())
    }

    @Test fun `401 flips needsPairing`() = runTest {
        val api = FakeApi { throw UnauthorizedException() }
        val v = vm(api)
        v.send("selam"); advanceUntilIdle()
        assertTrue(v.state.value.needsPairing)
    }

    @Test fun `api error shows the Turkish message and clears busy`() = runTest {
        val api = FakeApi { throw JarvisApiException("Jarvis'e ulaşılamadı.") }
        val v = vm(api)
        v.send("selam"); advanceUntilIdle()
        assertEquals("Jarvis'e ulaşılamadı.", v.state.value.error)
        assertFalse(v.state.value.busy)
    }

    @Test fun `a second send while busy is dropped`() = runTest {
        val api = FakeApi()
        val v = vm(api)
        v.send("bir"); v.send("iki")     // ilki daha bitmedi (dispatcher bekliyor)
        advanceUntilIdle()
        assertEquals(1, api.calls.size)
    }
}
```

- [ ] **Step 2: FAIL gör** — `./gradlew :wear:testDebugUnitTest ; echo "exit=$?"`

- [ ] **Step 3: Uygula**

`ChatViewModel.kt`:

```kotlin
package com.jarvis.wear.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.wear.data.JarvisApiException
import com.jarvis.wear.data.UnauthorizedException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

data class ChatState(
    val busy: Boolean = false,
    val lastQuestion: String? = null,
    val lastReply: String? = null,
    val error: String? = null,
    val needsPairing: Boolean = false,
)

/** Saat ekranında 3-4 hazır komut: tek dokunuş, aynı chat ucu (spec §5). */
val QUICK_COMMANDS = listOf(
    "Durum raporu ver",
    "Hatırlatmalarımı listele",
    "Bekleyen onaylarım var mı?",
    "Bugün ne yapmalıyım?",
)

class ChatViewModel(
    private val chat: suspend (sessionId: String, message: String) -> String,
    private val sessionId: String,
) : ViewModel() {
    private val _state = MutableStateFlow(ChatState())
    val state: StateFlow<ChatState> = _state

    fun send(text: String) {
        val message = text.trim()
        if (message.isEmpty() || _state.value.busy) return
        _state.value = _state.value.copy(busy = true, lastQuestion = message, error = null)
        viewModelScope.launch {
            try {
                val reply = chat(sessionId, message)
                _state.value = _state.value.copy(busy = false, lastReply = reply)
            } catch (e: UnauthorizedException) {
                _state.value = _state.value.copy(busy = false, needsPairing = true)
            } catch (e: JarvisApiException) {
                _state.value = _state.value.copy(busy = false, error = e.userMessage)
            } catch (e: Exception) {
                _state.value = _state.value.copy(busy = false, error = "Beklenmeyen hata; tekrar dene.")
            }
        }
    }
}
```

`ChatScreen.kt` — `ScalingLazyColumn`: üstte son soru/cevap (cevap `Card` içinde), `busy` iken küçük `CircularProgressIndicator`, `error` varsa kırmızı `Text`, altta `QUICK_COMMANDS` `Chip`leri (`onClick = { vm.send(it) }`), en altta mikrofon `Button`u (Task 6 dolduracak; şimdilik disabled). `needsPairing` true olursa kök `PairScreen`'e döner (MainActivity rotası `state.needsPairing`i de dinler ve `tokenStore.clear()` çağırır — eski token'la dönmeyi dener durur olmasın).

Oturum kimliği: `"wear-" + java.time.LocalDate.now()` (telefonun oturum düzeniyle aynı aile; sunucu sanitize eder).

- [ ] **Step 4: PASS + derleme** — `./gradlew :wear:testDebugUnitTest :wear:assembleDebug ; echo "exit=$?"`

- [ ] **Step 5: Commit**

```bash
git add android/wear/
git commit -m "feat(wear): chat screen with quick-command chips

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Sesli komut + TTS

**Files:**
- Create: `android/wear/src/main/java/com/jarvis/wear/ui/Voice.kt`
- Modify: `android/wear/src/main/java/com/jarvis/wear/ui/ChatScreen.kt` (mikrofon butonu bağlanır)
- Modify: `android/wear/src/main/java/com/jarvis/wear/MainActivity.kt` (TTS ömrü)
- Test: `android/wear/src/test/java/com/jarvis/wear/ui/VoiceReplySpeakerTest.kt`

**Interfaces:**
- Consumes: Task 5 `ChatViewModel.send`; sistem `RecognizerIntent` (`ACTION_RECOGNIZE_SPEECH`, `LANGUAGE="tr-TR"`); `android.speech.tts.TextToSpeech`.
- Produces: `rememberSpeechLauncher(onResult: (String) -> Unit)` composable yardımcısı; `ReplySpeaker(tts)` — `speakIfNew(reply)` (aynı cevabı iki kez okumaz).

- [ ] **Step 1: Başarısız testi yaz** — `VoiceReplySpeakerTest.kt`:

```kotlin
package com.jarvis.wear.ui

import org.junit.Assert.assertEquals
import org.junit.Test

/** TTS sarmalayıcısının sözleşmesi: yeni cevap okunur, aynı cevap tekrar
 * OKUNMAZ (state yeniden compose olduğunda kekeleme olmasın), null susar. */
class VoiceReplySpeakerTest {
    @Test fun `speaks each new reply once`() {
        val spoken = mutableListOf<String>()
        val speaker = ReplySpeaker { spoken += it }
        speaker.speakIfNew("merhaba")
        speaker.speakIfNew("merhaba")
        speaker.speakIfNew("ikinci")
        assertEquals(listOf("merhaba", "ikinci"), spoken)
    }

    @Test fun `null is silent`() {
        val spoken = mutableListOf<String>()
        ReplySpeaker { spoken += it }.speakIfNew(null)
        assertEquals(emptyList<String>(), spoken)
    }
}
```

- [ ] **Step 2: FAIL gör** — `./gradlew :wear:testDebugUnitTest ; echo "exit=$?"`

- [ ] **Step 3: Uygula** — `Voice.kt`:

```kotlin
package com.jarvis.wear.ui

import android.app.Activity
import android.content.Intent
import android.speech.RecognizerIntent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember

/** Sistem konuşma tanıma: ses kaydı sunucuya GİTMEZ (spec §2/C — v1'de saat
 * kanalında ses kanıtı yok; kırmızı bölge onay kartlarının arkasında). */
@Composable
fun rememberSpeechLauncher(onResult: (String) -> Unit) =
    rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            result.data
                ?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)
                ?.firstOrNull()
                ?.let(onResult)
        }
    }

fun speechIntent(): Intent =
    Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
        putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,
            RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
        putExtra(RecognizerIntent.EXTRA_LANGUAGE, "tr-TR")
        putExtra(RecognizerIntent.EXTRA_PROMPT, "Jarvis dinliyor")
    }

/** TTS tekrar koruması — compose yeniden çizimlerinde kekeleme yok. */
class ReplySpeaker(private val speak: (String) -> Unit) {
    private var last: String? = null
    fun speakIfNew(reply: String?) {
        if (reply != null && reply != last) { last = reply; speak(reply) }
    }
}
```

MainActivity: `TextToSpeech(this) { }` kur (locale `tr-TR`; `isLanguageAvailable` değilse sessiz metin-modu — hata değil, dürüst düşüş: emülatör imajında TTS sesi olmayabilir, log'a bir satır). `ReplySpeaker { text -> tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "jarvis") }`. `onDestroy`'da `tts.shutdown()`.

ChatScreen mikrofon butonu: `val launcher = rememberSpeechLauncher { vm.send(it) }`; `Button(onClick = { launcher.launch(speechIntent()) }, enabled = !state.busy)`. Cevap değişince `speaker.speakIfNew(state.lastReply)` (composable'da `LaunchedEffect(state.lastReply)`).

`RECORD_AUDIO` izni: RecognizerIntent sistem UI'sı kendi iznini yönetir; yine de ilk açılışta izin istenmez — sistem tanıyıcı ekranı halleder. Emülatörde tanıyıcı yoksa `ActivityNotFoundException` yakalanır → Türkçe hata metni state'e ("Bu cihazda konuşma tanıma yok").

- [ ] **Step 4: PASS + derleme** — `./gradlew :wear:testDebugUnitTest :wear:assembleDebug ; echo "exit=$?"`

- [ ] **Step 5: Commit**

```bash
git add android/wear/
git commit -m "feat(wear): voice input via system recognizer and spoken replies

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Emülatör E2E — eşleştirme + dört akışın kanıtı (kontrollü, ekran görüntülü)

**Files:** yok (doğrulama; bulgular ledger'a + gerekirse fix task'ları)

Bu task SDD kontrolcüsü tarafından koşulur (subagent değil): emülatör + gerçek S23 + ekran görüntüsü zinciri.

- [ ] **Step 1: Emülatörü başlat** (bellek notu: tek AVD, işi bitince kapat):

```bash
~/Android/Sdk/emulator/emulator -avd bd_watch -no-snapshot-save &
adb wait-for-device   # wear emülatörü ayrı seri alır: `adb devices` ile gör (emulator-5554)
```

- [ ] **Step 2: Wear APK'yı emülatöre kur** (yalnız EMÜLATÖRE — seri belirterek; gerçek saat YOK):

```bash
adb -s emulator-5554 install -r android/wear/build/outputs/apk/debug/wear-debug.apk
ls -la android/wear/build/outputs/apk/debug/wear-debug.apk   # damga kontrolü
```

- [ ] **Step 3: Dürüst ekran kanıtı** — uygulamayı aç, `PairScreen` görünmeli ("Saat eşleştirilmemiş"): `adb -s emulator-5554 exec-out screencap -p > /tmp/claude-1000/.../w1-pair-screen.png` → Read ile bak (3 Ağu dersi: ekran görüntüsü zinciri).

- [ ] **Step 4: S23 ↔ emülatör Data Layer bağlantısı.** Telefon APK'sını (Task 3'lü) S23'e kur (`adb -s PHONE_SERIAL install -r ...`). Wear emülatörünü telefonla eşle: Android Studio Device Manager "Pair Wearable" akışı (S23 USB'de). Bağlantı kurulunca telefonda "Saati eşleştir" → S23 token basar → emülatördeki saat uygulaması token'ı alır → ekran kendiliğinden Chat'e geçer (Flow). Ekran görüntüsü. **Bu adım W0'ın canlı mint zincirini de kapatır** (gerçek Google token → gerçek `POST /api/device-tokens` → Firestore'da doküman).
  - Pürüz çıkarsa (Data Layer emülatörle kurulamazsa): yedek yol spec §7 — `bd_phone` AVD'ye (Play imajlıysa) telefon uygulamasını kurup Kadir'in hesabıyla oturum açarak emülatör-emülatör eşleşmesi. İkisi de yürümezse DUR, ledger'a yaz, Kadir'le konuş.
- [ ] **Step 5: Sohbet + hızlı komut kanıtı** — Chat ekranında "Durum raporu ver" chip'ine dokun → gerçek cevap gelir (canlı brain!); ekran görüntüsü. Cloud Run logunda isteğin `jdt_` ile 200 aldığını gör (`resource.labels.revision_name` + 200; hangi revizyonun servis ettiğine bak — 3 Ağu dersi).
- [ ] **Step 6: Sesli komut kanıtı** — mikrofon butonu → emülatör host mikrofonu → "hatırlatmalarımı listele" → transkript → cevap + TTS (emülatörde ses yoksa metin düşüşü kabul; not düş). Ekran görüntüsü.
- [ ] **Step 7: 401 düşüş kanıtı** — telefondan `DELETE /api/device-tokens/{id}` (ya da Firestore'dan revoke) → saatte bir sonraki komut `needsPairing` → PairScreen'e dürüst dönüş. Ekran görüntüsü. Token'ı yeniden eşleştirerek geri getir.
- [ ] **Step 8: Kapanış** — emülatörü kapat; bulgular + ekran görüntüsü yolları ledger'a; deploy YOK (sunucu değişmedi); mesh checkpoint + memory güncelle. Gerçek saate kurulum BU PLANDA YOK — o kapı W2 sonrası "günlük seviye" çıtasında, Kadir'in onayıyla.

---

## Self-Review Notları

- **Spec §5 kapsaması:** modül→T1, kimlik→T2+T3+T4, sesli komut→T6, hızlı komutlar→T5, dürüst ekran→T4, emülatör stratejisi→T7. **Tile spec'te W1'deydi — bilinçli erteleme W2'ye** (Tile ayrı API yüzeyi; çekirdek dört akış önce; spec'e not düşülecek). Horologist çıkarıldı (YAGNI).
- **Tip tutarlılığı:** `TokenStore(prefs, cipher)` T2'de tanımlı, T4 `DataStorePrefs`+`KeystoreTokenCipher` ile kurar; `TOKEN_PATH` telefon+saat aynı sabit (iki modülde ayrı tanım — değer birebir, T4 testine sabit eşitliği pinlemek İSTEĞE bağlı değil: `assertEquals("/jarvis/device-token", ...)` her iki tarafta).
- **Bilinen riskler:** (1) AGP 9.3 + wear-compose sürüm uyumu — T1 Step 7 tam derleme kanıtlar; (2) `mockwebserver3` API'si (OkHttp 5) telefon testlerindeki kullanımdan doğrulanacak; (3) S23↔emülatör Data Layer eşleşmesi T7'nin tek gerçek belirsizliği — yedek yol yazılı, ikisi de yürümezse DUR; (4) `runBlocking` interceptor içinde — OkHttp worker thread'inde, ana thread'e dokunmaz (telefon deseniyle aynı sınıf), yine de T7'de gerçek akışta donma gözlenirse ledger'a.
- **W0'dan devralınan backlog (bu planda DEĞİL, W2'de):** `DELETE /api/voice/profile` + enroll'u `require_google_user`'a alma; `auth.py` bayat yorum; `test_revoke...` test adı.
