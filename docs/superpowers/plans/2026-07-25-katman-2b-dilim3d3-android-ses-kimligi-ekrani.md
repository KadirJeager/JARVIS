# Katman 2b Dilim 3d-3 — Android Ses Kimliği Yönetim Ekranı Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kadir'in ses kimliği profilini (galeri + doğrulama geçmişi) Android uygulamasından görüntülenebilir, düzeltilebilir ve silinebilir hale getirmek — spec §9'un tarif ettiği ekran.

**Architecture:** Ekran, sunucudaki §6 uçlarının **saf tüketicisi**. Hiçbir kural istemcide tekrarlanmaz: son-çapa koruması, elle-örnek cap'i, idempotanlık ve etiket doğrulaması sunucuda; istemci sadece çağırır, sunucunun Türkçe hata metnini gösterir ve **yeniden yükler**. İyimser (optimistic) güncelleme yoktur — iyimser güncelleme sunucu kurallarını istemcide taklit etmek zorunda kalırdı, spec §9 bunu yasaklıyor. Katmanlar mevcut sohbet yığınının aynısı: wire model → repository → ViewModel (StateFlow) → durumsuz (stateless) Compose ekran; DI elle (`AppContainer`), navigasyon elle (`when`).

**Tech Stack:** Kotlin 2.4.10, Compose BOM 2026.06.01, Retrofit 3.0.0 + kotlinx.serialization, androidx.biometric 1.1.0, JUnit4 + kotlinx-coroutines-test (JVM), Compose UI test (instrumented).

## Global Constraints

Bu bölüm her task'ın gereksinimlerine **örtük olarak dahildir**.

- **Sürüm matrisi dokunulmaz:** AGP 9.3.0, Gradle 9.6.1, JDK 21, Kotlin/KGP 2.4.10, Compose BOM 2026.06.01, compileSdk/targetSdk **36**, minSdk **26**.
- **`org.jetbrains.kotlin.android` eklentisi YASAK** — AGP 9.x'in yerleşik Kotlin'i var, eklenti "no longer required" hatası verir.
- **`lifecycle` 2.10.0'da PİNLİ** — 2.11.0 compileSdk 37 istiyor (`checkDebugAarMetadata` patlar).
- **Gradle daima JDK 21 ile:** `JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew ...` (sistem varsayılanı JDK 26 ve `sdkmanager`/AGP onunla çalışmaz).
- **Kullanıcıya görünen her metin Türkçe.** Kod, tanımlayıcılar, yorumlar, commit mesajları İngilizce.
- **Sunucunun hata metni ezilmez.** 400/404 gövdesindeki `detail` alanı doğrudan kullanıcıya gösterilir (ör. "Son çapa silinemez: çapasız profil ses doğrulayamaz..."). İstemci kendi Türkçe karşılığını uydurmaz.
- **"Biyometri geçildi" başlığı HİÇBİR yerde gönderilmez** (spec §7). Doğrulanamayan istemci beyanı sinyal değildir; bu hatayı bu dilimde bir kez yaptık ve 3a spec §12'ye yazdık.
- **Gömme vektörleri (`vec`) istemciye hiç gelmez** ve istemci hiç istemez (spec §6). Wire modellerinde `vec` alanı **tanımlanmaz**.
- **testTag konvansiyonu:** mevcut `chat_input` / `send_button` gibi `snake_case`.
- **Etiket sözlüğü kapalı ve ASCII** (sunucu `SPEAKER_SAMPLE_LABELS`): `saglikli`, `hasta`, `yorgun`, `gurultulu`, `kulaklik`, `hoparlor`, `arac`. Bunlar API **değerleri**; ekranda Türkçe karşılıkları gösterilir, telde ASCII gider.
- **Beklenen bağımlılık yan etkisi:** `androidx.biometric:1.1.0`, `androidx.fragment:1.2.5`'i compile scope'ta (gerekli — `FragmentActivity` oradan gelir) ve `androidx.appcompat:1.2.0`'ı runtime scope'ta getirir. Saf-Compose projeye appcompat girmesi **sürpriz değil, beklenen**; sürüm düşürüp/yükseltip bunu "düzeltmeye" çalışma.

---

## Kaynaktan doğrulanmış olgular (varsayım değil)

Bu plan aşağıdakileri gerçek artefakta bakarak doğruladı; uygulayan tekrar araştırmasın.

**`androidx.biometric:1.1.0` (en son **stable**; 1.2.0/1.4.0 hattı alpha) — `javap` ile AAR'dan:**

```
public androidx.biometric.BiometricPrompt(androidx.fragment.app.FragmentActivity, AuthenticationCallback);
public androidx.biometric.BiometricPrompt(androidx.fragment.app.Fragment, AuthenticationCallback);
public androidx.biometric.BiometricPrompt(androidx.fragment.app.FragmentActivity, java.util.concurrent.Executor, AuthenticationCallback);
public androidx.biometric.BiometricPrompt(androidx.fragment.app.Fragment, java.util.concurrent.Executor, AuthenticationCallback);
public void authenticate(PromptInfo);
public void authenticate(PromptInfo, CryptoObject);

public interface BiometricManager$Authenticators {
  public static final int BIOMETRIC_STRONG = 15;      // 0x000F
  public static final int BIOMETRIC_WEAK   = 255;     // 0x00FF
  public static final int DEVICE_CREDENTIAL = 32768;  // 0x8000
}
```

- **`ComponentActivity` overload'ı YOK.** `MainActivity : ComponentActivity` → `MainActivity : FragmentActivity` olmak **zorunda** (Task 6). `FragmentActivity`, `ComponentActivity`'yi genişletir, dolayısıyla `setContent {}` çalışmaya devam eder.
- **`BIOMETRIC_WEAK` (255) bit maskesi olarak `BIOMETRIC_STRONG`'u (15) kapsar** → `BIOMETRIC_WEAK or DEVICE_CREDENTIAL` güçlü biyometriyi de kabul eder.
- **API ≤29'da desteklenmeyen kombinasyonlar:** `DEVICE_CREDENTIAL` tek başına ve `BIOMETRIC_STRONG or DEVICE_CREDENTIAL`. minSdk 26 olduğumuz için **`BIOMETRIC_WEAK or DEVICE_CREDENTIAL` tek doğru seçim.** (CryptoObject kullanmıyoruz — ekran kapısıyız, anahtar açmıyoruz — yani STRONG şartı yok.)
- **`setNegativeButtonText()` ile `DEVICE_CREDENTIAL` aynı anda kullanılamaz.** İptal düğmesini sistem sağlar; çağırırsak `build()` patlar.
- AAR kendi manifestinde `USE_BIOMETRIC` + `USE_FINGERPRINT` izinlerini beyan eder → **bizim `AndroidManifest.xml`'e izin eklenmeyecek**, manifest merger halleder.

**Sunucu sözleşmesi (`brain/app/voice_manage.py`, birebir):**

```python
_SAMPLE_FIELDS  = ("id", "source", "ts", "device_hint", "label", "note")
_HISTORY_FIELDS = ("id", "ts", "score", "verified", "device_hint", "presence",
                   "trust_level", "adapted_sample_id", "correction")
```

| Uç | Metot | Başarı gövdesi |
|---|---|---|
| `/api/voice/profile` | GET | `{counts:{anchors,auto,manual}, samples:[...], history:[...], quality:{...}}` |
| `/api/voice/sample/{id}` | PATCH | yansıtılmış tek örnek (`_SAMPLE_FIELDS`) |
| `/api/voice/sample/{id}` | DELETE | `{"deleted": "<id>"}` ← **string** |
| `/api/voice/history/{id}/confirm` | POST | `{"added_sample_id": str\|null, "already": bool}` |
| `/api/voice/history/{id}/reject` | POST | `{"removed_sample_id": str\|null, "already": bool}` |
| `/api/voice/profile` | DELETE | `{"deleted": true}` ← **bool** |

> **TUZAK 1:** `deleted` anahtarı iki uçta **farklı tipte** (string vs bool). Tek bir ortak model kullanmak `SerializationException` doğurur → iki ayrı model şart.
>
> **TUZAK 2:** `_project()` `row.get(f)` kullanır, yani **eksik her alan JSON `null` olarak gider**. Ayrıca `_normalize_sample` eski (3d öncesi) çıplak-vektör çapaları için `ts=None` üretir. Örnek `ts` alanı **nullable olmak zorunda**.
>
> **TUZAK 3:** Boş profil geçerli bir durumdur: `counts` hepsi 0, `samples`/`history` boş, `quality` alanları `null`, `by_device`/`by_label` boş sözlük. Ekran bunu çökmeden karşılamalı.

`quality` şekli (`quality_indicators`):

```json
{"mean_verified_score": 0.71, "fail_rate": 0.2,
 "by_device": {"buds": 0.68}, "by_label": {"kulaklik": 0.72},
 "trend": {"last10": 0.70, "previous10": 0.64}}
```

Hata gövdesi FastAPI standardı: `{"detail": "<Türkçe mesaj>"}`. 404 (örnek/kayıt yok), 400 (kural ihlali: son çapa / cap dolu / geçersiz etiket), 502 (altyapı), 401 (yetkisiz).

---

## File Structure

**Yeni (`android/app/src/main/java/com/jarvis/`):**

| Dosya | Sorumluluk |
|---|---|
| `data/net/VoiceModels.kt` | §6 uçlarının wire modelleri. Sadece veri, mantık yok. |
| `data/net/VoiceApi.kt` | Retrofit arayüzü — **`JarvisApi`'den ayrı** (aşağıdaki gerekçe). |
| `data/net/ApiError.kt` | `HttpException` → sunucunun Türkçe `detail` metni. |
| `data/voice/VoiceProfile.kt` | Domain modelleri (ekranın konuştuğu tipler). |
| `data/voice/VoiceProfileRepository.kt` | Wire→domain eşleme, bozuk satır ayıklama, hata çevirisi. |
| `ui/voice/QualitySummary.kt` | Saf fonksiyon: kalite göstergeleri → Türkçe yorum. |
| `ui/voice/VoiceProfileUiState.kt` | Ekranın değişmez durumu. |
| `ui/voice/VoiceProfileViewModel.kt` | Durum makinesi; Android bağımlılığı yok (JVM'de test edilebilir). |
| `ui/voice/VoiceProfileScreen.kt` | Durumsuz ekran: durum kartı + galeri + geçmiş + tehlikeli bölge. |
| `ui/voice/VoiceLabels.kt` | ASCII etiket ↔ Türkçe görünen ad; kaynak rozeti metinleri. |
| `data/auth/BiometricGate.kt` | `BiometricPrompt` sarmalayıcısı + `interface` (test edilebilirlik). |

**Değişecek:**

| Dosya | Değişiklik |
|---|---|
| `gradle/libs.versions.toml` | `biometric = "1.1.0"` + kütüphane girdisi |
| `app/build.gradle.kts` | `implementation(libs.androidx.biometric)` |
| `data/net/NetworkModule.kt` | Tek Retrofit örneğinden `JarvisApi` **ve** `VoiceApi` üretimi |
| `JarvisApp.kt` (`AppContainer`) | `voiceProfileRepository` + `biometricGate` |
| `MainActivity.kt` | `ComponentActivity` → `FragmentActivity`; ekran yönlendirme |
| `ui/Nav.kt` | `SIGNED_IN` altında Sohbet ↔ Ses Kimliği ayrımı |
| `ui/chat/ChatScreen.kt` | TopBar'a "Ses kimliğim" giriş noktası |

**Neden `VoiceApi` ayrı bir arayüz:** `JarvisApi`'yi üç ayrı testte elle yazılmış sahte (`FakeApi : JarvisApi`) uyguluyor — `ChatViewModelTest`, `ChatRepositoryTest`, `EndToEndTest`. Arayüze 6 metot eklemek üçünü birden derlenemez hale getirir ve bu dilimle ilgisi olmayan testleri kirletir. Ayrı arayüz: sıfır dokunuş, ve sorumluluk sınırı zaten doğru yerde.

---

### Task 1: Wire modelleri + `VoiceApi` + NetworkModule ikili üretim

**Files:**
- Create: `android/app/src/main/java/com/jarvis/data/net/VoiceModels.kt`
- Create: `android/app/src/main/java/com/jarvis/data/net/VoiceApi.kt`
- Modify: `android/app/src/main/java/com/jarvis/data/net/NetworkModule.kt`
- Test: `android/app/src/test/java/com/jarvis/data/net/VoiceSerializationTest.kt`

**Interfaces:**
- Consumes: mevcut `NetworkModule.create(tokenProvider, tokenRefresher)` imzası (geriye dönük korunacak).
- Produces: `VoiceApi` (6 suspend metot), `VoiceProfileResponse`, `VoiceSampleDto`, `VoiceHistoryDto`, `VoiceCountsDto`, `VoiceQualityDto`, `VoiceTrendDto`, `SampleDeletedResponse`, `ProfileDeletedResponse`, `ConfirmResponse`, `RejectResponse`, `SamplePatchRequest`; `NetworkModule.createApis(...) : ApiSet`.

- [ ] **Step 1: Write the failing test**

`android/app/src/test/java/com/jarvis/data/net/VoiceSerializationTest.kt`:

```kotlin
package com.jarvis.data.net

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The backend contract is fixed (brain/app/voice_manage.py). These tests pin the
 * exact JSON the server produces, including the two shapes that a single shared
 * model would get wrong.
 */
class VoiceSerializationTest {

    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun profileResponse_decodesFullShape() {
        val raw = """
            {"counts":{"anchors":7,"auto":2,"manual":1},
             "samples":[{"id":"s1","source":"enroll","ts":"2026-07-25T00:00:00Z",
                         "device_hint":"buds","label":"kulaklik","note":"ilk kayit"}],
             "history":[{"id":"h1","ts":"2026-07-25T01:00:00Z","score":0.71,"verified":true,
                         "device_hint":"buds","presence":"foreground","trust_level":"HIGH",
                         "adapted_sample_id":"s9","correction":null}],
             "quality":{"mean_verified_score":0.71,"fail_rate":0.2,
                        "by_device":{"buds":0.68},"by_label":{"kulaklik":0.72},
                        "trend":{"last10":0.70,"previous10":0.64}}}
        """.trimIndent()

        val p = json.decodeFromString<VoiceProfileResponse>(raw)

        assertEquals(7, p.counts.anchors)
        assertEquals(2, p.counts.auto)
        assertEquals(1, p.counts.manual)
        assertEquals("s1", p.samples[0].id)
        assertEquals("enroll", p.samples[0].source)
        assertEquals("kulaklik", p.samples[0].label)
        assertEquals("ilk kayit", p.samples[0].note)
        assertEquals("h1", p.history[0].id)
        assertEquals(0.71, p.history[0].score!!, 1e-9)
        assertEquals(true, p.history[0].verified)
        assertEquals("s9", p.history[0].adapted_sample_id)
        assertNull(p.history[0].correction)
        assertEquals(0.71, p.quality.mean_verified_score!!, 1e-9)
        assertEquals(0.68, p.quality.by_device["buds"]!!, 1e-9)
        assertEquals(0.70, p.quality.trend.last10!!, 1e-9)
    }

    /**
     * An empty profile is a legitimate state (nothing enrolled yet): every quality
     * figure is null and both maps are empty. The screen must be reachable then.
     */
    @Test
    fun profileResponse_decodesEmptyProfile() {
        val raw = """
            {"counts":{"anchors":0,"auto":0,"manual":0},"samples":[],"history":[],
             "quality":{"mean_verified_score":null,"fail_rate":null,
                        "by_device":{},"by_label":{},
                        "trend":{"last10":null,"previous10":null}}}
        """.trimIndent()

        val p = json.decodeFromString<VoiceProfileResponse>(raw)

        assertTrue(p.samples.isEmpty())
        assertTrue(p.history.isEmpty())
        assertNull(p.quality.mean_verified_score)
        assertTrue(p.quality.by_device.isEmpty())
        assertNull(p.quality.trend.previous10)
    }

    /**
     * `_project()` in voice_manage.py uses row.get(f), so ANY absent field arrives as
     * JSON null; `_normalize_sample` additionally produces ts=None for pre-3d bare
     * vector anchors. A non-nullable `ts` would blow up on Kadir's own legacy rows.
     */
    @Test
    fun sample_toleratesNullTsAndNullLabelAndNullNote() {
        val raw = """{"id":"s2","source":"auto","ts":null,"device_hint":"unknown",
                      "label":null,"note":null}"""
        val s = json.decodeFromString<VoiceSampleDto>(raw)
        assertEquals("s2", s.id)
        assertNull(s.ts)
        assertNull(s.label)
        assertNull(s.note)
    }

    /**
     * The two DELETE endpoints put DIFFERENT TYPES under the same "deleted" key:
     * sample deletion returns the id (string), profile deletion returns true (bool).
     * One shared model cannot decode both.
     */
    @Test
    fun deleteResponses_haveDifferentTypesUnderTheSameKey() {
        val sample = json.decodeFromString<SampleDeletedResponse>("""{"deleted":"s1"}""")
        assertEquals("s1", sample.deleted)

        val profile = json.decodeFromString<ProfileDeletedResponse>("""{"deleted":true}""")
        assertEquals(true, profile.deleted)
    }

    @Test
    fun correctionResponses_decodeNullableIdsAndAlreadyFlag() {
        val c = json.decodeFromString<ConfirmResponse>(
            """{"added_sample_id":"s5","already":false}""",
        )
        assertEquals("s5", c.added_sample_id)
        assertEquals(false, c.already)

        val r = json.decodeFromString<RejectResponse>(
            """{"removed_sample_id":null,"already":true}""",
        )
        assertNull(r.removed_sample_id)
        assertEquals(true, r.already)
    }

    /**
     * PATCH must be able to send an explicit null to CLEAR a label — the server
     * distinguishes "absent" from "null" via model_fields_set, and kotlinx by default
     * omits nulls, which would silently turn "clear the label" into a no-op.
     */
    @Test
    fun samplePatch_encodesExplicitNullsSoAClearIsNotSilentlyDropped() {
        val encoded = VoiceApiJson.encodePatch(SamplePatchRequest(label = null, note = null))
        assertTrue("label açıkça null gitmeli: $encoded", encoded.contains("\"label\":null"))
        assertTrue("note açıkça null gitmeli: $encoded", encoded.contains("\"note\":null"))
    }

    /** A malformed history row must decode, not throw: the repository drops it later. */
    @Test
    fun history_toleratesMissingScoreAndVerified() {
        val h = json.decodeFromString<VoiceHistoryDto>("""{"id":"h9","ts":null}""")
        assertEquals("h9", h.id)
        assertNull(h.score)
        assertNull(h.verified)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew testDebugUnitTest --tests '*VoiceSerializationTest*'
```

Expected: FAIL — `Unresolved reference: VoiceProfileResponse` (ve diğerleri).

- [ ] **Step 3: Write minimal implementation**

`android/app/src/main/java/com/jarvis/data/net/VoiceModels.kt`:

```kotlin
package com.jarvis.data.net

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * Wire models for the speaker-identity management contract (brain/app/voice_manage.py,
 * spec §6). Field names MUST match the JSON exactly — the backend is fixed.
 *
 * NOTE: `vec` is deliberately absent from every model here. Embeddings never leave the
 * server (spec §6) and the client has no use for them; not declaring the field is the
 * cheapest way to guarantee we never start asking for one.
 *
 * Nullability is not defensiveness for its own sake: `_project()` builds responses with
 * `row.get(field)`, so any absent field arrives as JSON null, and `_normalize_sample`
 * produces `ts=None` for pre-3d bare-vector anchors.
 */
@Serializable
data class VoiceCountsDto(
    val anchors: Int = 0,
    val auto: Int = 0,
    val manual: Int = 0,
)

@Serializable
data class VoiceSampleDto(
    val id: String,
    val source: String,
    val ts: String? = null,
    val device_hint: String? = null,
    val label: String? = null,
    val note: String? = null,
)

@Serializable
data class VoiceHistoryDto(
    val id: String,
    val ts: String? = null,
    val score: Double? = null,
    val verified: Boolean? = null,
    val device_hint: String? = null,
    val presence: String? = null,
    val trust_level: String? = null,
    val adapted_sample_id: String? = null,
    val correction: String? = null,
)

@Serializable
data class VoiceTrendDto(
    val last10: Double? = null,
    val previous10: Double? = null,
)

@Serializable
data class VoiceQualityDto(
    val mean_verified_score: Double? = null,
    val fail_rate: Double? = null,
    val by_device: Map<String, Double?> = emptyMap(),
    val by_label: Map<String, Double?> = emptyMap(),
    val trend: VoiceTrendDto = VoiceTrendDto(),
)

@Serializable
data class VoiceProfileResponse(
    val counts: VoiceCountsDto = VoiceCountsDto(),
    val samples: List<VoiceSampleDto> = emptyList(),
    val history: List<VoiceHistoryDto> = emptyList(),
    val quality: VoiceQualityDto = VoiceQualityDto(),
)

/**
 * PATCH body. The server tells "omitted" from "explicitly null" via Pydantic's
 * model_fields_set: an omitted label leaves the label alone, an explicit null CLEARS
 * it. kotlinx.serialization omits nulls by default, which would silently turn every
 * "clear the label" into a no-op — see [VoiceApiJson].
 */
@Serializable
data class SamplePatchRequest(
    val label: String? = null,
    val note: String? = null,
)

@Serializable
data class SampleDeletedResponse(val deleted: String)

/** Same key, different type from [SampleDeletedResponse] — deliberately separate. */
@Serializable
data class ProfileDeletedResponse(val deleted: Boolean)

@Serializable
data class ConfirmResponse(
    val added_sample_id: String? = null,
    val already: Boolean = false,
)

@Serializable
data class RejectResponse(
    val removed_sample_id: String? = null,
    val already: Boolean = false,
)

/**
 * The PATCH body is the ONE place the client must emit explicit nulls; everywhere else
 * the default (omit) is what we want. Kept as a separate Json instance so the choice is
 * visible and testable rather than a flag buried in NetworkModule.
 */
object VoiceApiJson {
    val patchJson = Json { explicitNulls = true; encodeDefaults = true }
    fun encodePatch(req: SamplePatchRequest): String = patchJson.encodeToString(req)
}
```

`android/app/src/main/java/com/jarvis/data/net/VoiceApi.kt`:

```kotlin
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
```

`android/app/src/main/java/com/jarvis/data/net/NetworkModule.kt` — tamamını şununla değiştir:

```kotlin
package com.jarvis.data.net

import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

/** Deployed jarvis-brain base URL (Katman 2b backend). */
const val BASE_URL = "https://jarvis-brain-000000000000.europe-west1.run.app"

/** Both API surfaces, sharing one OkHttp client and one Retrofit instance. */
class ApiSet(val chat: JarvisApi, val voice: VoiceApi)

object NetworkModule {
    /**
     * Builds both APIs over a Bearer-attaching OkHttp client. [tokenProvider] is read
     * per request; on a 401, [tokenRefresher] provides a fresh token for a single
     * silent retry.
     *
     * `explicitNulls = true` matters: PATCH /api/voice/sample/{id} uses an explicit
     * null to CLEAR a label, and kotlinx's default (omit nulls) would turn that into
     * "field absent", which the server reads as "leave it alone".
     */
    fun createApis(
        tokenProvider: () -> String?,
        tokenRefresher: () -> String? = { null },
    ): ApiSet {
        val json = Json { ignoreUnknownKeys = true; explicitNulls = true }
        val client = OkHttpClient.Builder()
            .addInterceptor(AuthInterceptor(tokenProvider))
            .authenticator(TokenAuthenticator(tokenRefresher))
            .build()
        val retrofit = Retrofit.Builder()
            .baseUrl("$BASE_URL/")
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
        return ApiSet(
            chat = retrofit.create(JarvisApi::class.java),
            voice = retrofit.create(VoiceApi::class.java),
        )
    }

    /** Kept so existing chat-side callers and tests are untouched. */
    fun create(
        tokenProvider: () -> String?,
        tokenRefresher: () -> String? = { null },
    ): JarvisApi = createApis(tokenProvider, tokenRefresher).chat
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew testDebugUnitTest
```

Expected: PASS — 21 mevcut + 7 yeni = **28 JVM testi**, sıfır regresyon.

- [ ] **Step 5: Mutation check (bu adım atlanamaz)**

`VoiceApiJson.patchJson` içindeki `explicitNulls = true`'yu geçici olarak `false` yap ve testi tekrar koş. `samplePatch_encodesExplicitNullsSoAClearIsNotSilentlyDropped` **KIRMIZI olmalı**. Değilse test taşıyıcı değil — düzelt. Sonra geri al.

- [ ] **Step 6: Commit**

```bash
cd /home/user/Projeler/JARVIS
git add android/app/src/main/java/com/jarvis/data/net/ android/app/src/test/java/com/jarvis/data/net/VoiceSerializationTest.kt
git commit -m "feat(voice-ui): wire models + VoiceApi for the speaker identity endpoints"
```

---

### Task 2: Sunucunun Türkçe hata metnini yüzeye çıkarma

**Files:**
- Create: `android/app/src/main/java/com/jarvis/data/net/ApiError.kt`
- Test: `android/app/src/test/java/com/jarvis/data/net/ApiErrorTest.kt`

**Interfaces:**
- Consumes: `retrofit2.HttpException` (Retrofit 3.0.0).
- Produces: `fun Throwable.userMessage(fallback: String): String`, `const val NETWORK_MESSAGE`.

**Neden ayrı task:** Bu, spec §9'un "hiçbir karar istemcide alınmaz" ilkesinin taşıyıcı parçası. Sunucu "Son çapa silinemez: çapasız profil ses doğrulayamaz..." diyor; istemci bunun yerine kendi uydurduğu bir metni gösterirse kural iki yerde yaşamaya başlar ve zamanla ayrışır.

- [ ] **Step 1: Write the failing test**

`android/app/src/test/java/com/jarvis/data/net/ApiErrorTest.kt`:

```kotlin
package com.jarvis.data.net

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException

class ApiErrorTest {

    private fun httpError(code: Int, body: String): HttpException =
        HttpException(Response.error<Any>(code, body.toResponseBody("application/json".toMediaType())))

    /**
     * The server owns the rules AND their wording (spec §9). "Son çapa silinemez"
     * must reach the user verbatim; inventing a client-side equivalent would put the
     * same rule in two places.
     */
    @Test
    fun httpError_surfacesTheServersTurkishDetailVerbatim() {
        val message = httpError(
            400,
            """{"detail":"Son çapa silinemez: çapasız profil ses doğrulayamaz."}""",
        ).userMessage("olmadı")
        assertEquals("Son çapa silinemez: çapasız profil ses doğrulayamaz.", message)
    }

    @Test
    fun httpError_surfacesTheCapMessageWithItsNumbers() {
        val message = httpError(
            400,
            """{"detail":"Elle eklenen örnek sınırı dolu (5/5). Yenisini eklemek için önce elle eklenmiş bir örneği sil."}""",
        ).userMessage("olmadı")
        assertTrue(message.contains("(5/5)"))
    }

    @Test
    fun httpError_withoutADetailField_fallsBackToTheCallersMessage() {
        assertEquals("olmadı", httpError(500, """{"oops":1}""").userMessage("olmadı"))
    }

    @Test
    fun httpError_withUnparseableBody_fallsBackInsteadOfThrowing() {
        assertEquals("olmadı", httpError(502, "<html>gateway</html>").userMessage("olmadı"))
    }

    @Test
    fun ioError_reportsAConnectionProblem_notTheRawException() {
        val message = IOException("failed to connect").userMessage("olmadı")
        assertEquals(NETWORK_MESSAGE, message)
        assertTrue(message.contains("Bağlantı"))
    }

    @Test
    fun unknownError_usesTheFallback() {
        assertEquals("olmadı", IllegalStateException("boom").userMessage("olmadı"))
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew testDebugUnitTest --tests '*ApiErrorTest*'
```

Expected: FAIL — `Unresolved reference: userMessage`.

- [ ] **Step 3: Write minimal implementation**

`android/app/src/main/java/com/jarvis/data/net/ApiError.kt`:

```kotlin
package com.jarvis.data.net

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import retrofit2.HttpException
import java.io.IOException

const val NETWORK_MESSAGE = "Bağlantı kurulamadı. İnterneti kontrol edip tekrar dene."

@Serializable
private data class ErrorBody(val detail: String? = null)

private val errorJson = Json { ignoreUnknownKeys = true }

/**
 * Turns a failed call into the message the user should read.
 *
 * The server owns every management rule AND its Turkish wording (spec §9): the
 * last-anchor guard, the manual-sample cap with its live numbers, the closed label
 * vocabulary. Re-deriving any of that here would put one rule in two places, and the
 * two would drift. So a 4xx body's `detail` is passed through VERBATIM; [fallback] only
 * covers responses that carry no detail at all.
 */
fun Throwable.userMessage(fallback: String): String = when (this) {
    is HttpException -> serverDetail() ?: fallback
    is IOException -> NETWORK_MESSAGE
    else -> fallback
}

private fun HttpException.serverDetail(): String? = try {
    // errorBody() is a one-shot stream; this is the only place that reads it.
    val raw = response()?.errorBody()?.string()
    if (raw.isNullOrBlank()) null
    else errorJson.decodeFromString<ErrorBody>(raw).detail?.takeIf { it.isNotBlank() }
} catch (_: Exception) {
    // A non-JSON body (an HTML gateway page, say) is not a reason to crash the screen.
    null
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew testDebugUnitTest
```

Expected: PASS — 34 JVM testi.

- [ ] **Step 5: Commit**

```bash
cd /home/user/Projeler/JARVIS
git add android/app/src/main/java/com/jarvis/data/net/ApiError.kt android/app/src/test/java/com/jarvis/data/net/ApiErrorTest.kt
git commit -m "feat(voice-ui): surface the server's Turkish error detail verbatim"
```

---

### Task 3: Domain modelleri + `VoiceProfileRepository`

**Files:**
- Create: `android/app/src/main/java/com/jarvis/data/voice/VoiceProfile.kt`
- Create: `android/app/src/main/java/com/jarvis/data/voice/VoiceProfileRepository.kt`
- Test: `android/app/src/test/java/com/jarvis/data/voice/VoiceProfileRepositoryTest.kt`

**Interfaces:**
- Consumes: `VoiceApi` ve Task 1'in DTO'ları; Task 2'nin `userMessage`.
- Produces: `VoiceProfile`, `VoiceSample`, `HistoryRow`, `VoiceQuality`, `VoiceCounts`, `SampleSource`, `Correction`; `VoiceProfileRepository` (`load`, `setLabel`, `setNote`, `deleteSample`, `confirm`, `reject`, `deleteProfile`).

**Tasarım kararı — bozuk satır ayıklama:** Wire modelleri hoşgörülü (her şey nullable), domain modelleri katı. Repository `score`/`verified` taşımayan geçmiş satırlarını **düşürür**. Gerekçe: sunucu tarafı backlog'da "malformed history row → raw 500" maddesi zaten duruyor; istemcide tek bir bozuk satırın **bütün ekranı** boş bırakmasına izin vermek, o hatayı ikinci kez ödemek olur.

- [ ] **Step 1: Write the failing test**

`android/app/src/test/java/com/jarvis/data/voice/VoiceProfileRepositoryTest.kt`:

```kotlin
package com.jarvis.data.voice

import com.jarvis.data.net.ConfirmResponse
import com.jarvis.data.net.ProfileDeletedResponse
import com.jarvis.data.net.RejectResponse
import com.jarvis.data.net.SampleDeletedResponse
import com.jarvis.data.net.SamplePatchRequest
import com.jarvis.data.net.VoiceApi
import com.jarvis.data.net.VoiceCountsDto
import com.jarvis.data.net.VoiceHistoryDto
import com.jarvis.data.net.VoiceProfileResponse
import com.jarvis.data.net.VoiceQualityDto
import com.jarvis.data.net.VoiceSampleDto
import com.jarvis.data.net.VoiceTrendDto
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceProfileRepositoryTest {

    private class FakeVoiceApi(
        var profile: VoiceProfileResponse = VoiceProfileResponse(),
    ) : VoiceApi {
        var patched: Pair<String, SamplePatchRequest>? = null
        var deletedSample: String? = null
        var confirmed: String? = null
        var rejected: String? = null
        var profileDeleted = false

        override suspend fun profile(): VoiceProfileResponse = profile
        override suspend fun patchSample(id: String, req: SamplePatchRequest): VoiceSampleDto {
            patched = id to req
            return VoiceSampleDto(id = id, source = "auto", label = req.label, note = req.note)
        }
        override suspend fun deleteSample(id: String): SampleDeletedResponse {
            deletedSample = id
            return SampleDeletedResponse(id)
        }
        override suspend fun confirm(id: String): ConfirmResponse {
            confirmed = id
            return ConfirmResponse("s-new", already = false)
        }
        override suspend fun reject(id: String): RejectResponse {
            rejected = id
            return RejectResponse(null, already = true)
        }
        override suspend fun deleteProfile(): ProfileDeletedResponse {
            profileDeleted = true
            return ProfileDeletedResponse(true)
        }
    }

    @Test
    fun load_mapsSamplesWithTheirSourceAndCounts() = runBlocking {
        val api = FakeVoiceApi(
            VoiceProfileResponse(
                counts = VoiceCountsDto(anchors = 2, auto = 1, manual = 0),
                samples = listOf(
                    VoiceSampleDto("s1", "enroll", "t1", "buds", "kulaklik", null),
                    VoiceSampleDto("s2", "auto", null, "unknown", null, null),
                ),
            ),
        )
        val profile = VoiceProfileRepository(api).load()

        assertEquals(2, profile.counts.anchors)
        assertEquals(SampleSource.ENROLL, profile.samples[0].source)
        assertEquals("kulaklik", profile.samples[0].label)
        assertEquals(SampleSource.AUTO, profile.samples[1].source)
        assertNull(profile.samples[1].ts)          // legacy anchors carry no timestamp
    }

    /** An unrecognised source must not crash the screen; it degrades to AUTO. */
    @Test
    fun load_mapsAnUnknownSourceToAuto() = runBlocking {
        val api = FakeVoiceApi(
            VoiceProfileResponse(samples = listOf(VoiceSampleDto("s3", "martian"))),
        )
        assertEquals(SampleSource.AUTO, VoiceProfileRepository(api).load().samples[0].source)
    }

    /**
     * One malformed history row must not blank the whole screen. The server-side
     * backlog already carries "malformed history row -> raw 500"; letting it also
     * break the client would be paying for the same defect twice.
     */
    @Test
    fun load_dropsHistoryRowsMissingScoreOrVerified_andKeepsTheRest() = runBlocking {
        val api = FakeVoiceApi(
            VoiceProfileResponse(
                history = listOf(
                    VoiceHistoryDto("h1", "t", 0.7, true, "buds", "foreground", "HIGH", "s9", null),
                    VoiceHistoryDto("h2", "t", null, true),        // no score -> dropped
                    VoiceHistoryDto("h3", "t", 0.2, null),         // no verdict -> dropped
                    VoiceHistoryDto("h4", "t", 0.4, false, correction = "rejected"),
                ),
            ),
        )
        val rows = VoiceProfileRepository(api).load().history

        assertEquals(listOf("h1", "h4"), rows.map { it.id })
        assertEquals(Correction.NONE, rows[0].correction)
        assertEquals(Correction.REJECTED, rows[1].correction)
        assertEquals(0.4, rows[1].score, 1e-9)
    }

    @Test
    fun load_mapsQualityIncludingNulls() = runBlocking {
        val api = FakeVoiceApi(
            VoiceProfileResponse(
                quality = VoiceQualityDto(
                    mean_verified_score = 0.71, fail_rate = 0.2,
                    by_device = mapOf("buds" to 0.68, "bozuk" to null),
                    by_label = mapOf("kulaklik" to 0.72),
                    trend = VoiceTrendDto(0.70, null),
                ),
            ),
        )
        val q = VoiceProfileRepository(api).load().quality

        assertEquals(0.71, q.meanVerifiedScore!!, 1e-9)
        assertEquals(0.68, q.byDevice["buds"]!!, 1e-9)
        assertTrue("null ortalamalar atılmalı", "bozuk" !in q.byDevice)
        assertNull(q.previous10)
    }

    @Test
    fun setLabel_sendsOnlyTheLabelField() = runBlocking {
        val api = FakeVoiceApi()
        VoiceProfileRepository(api).setLabel("s1", "yorgun")
        assertEquals("s1", api.patched!!.first)
        assertEquals("yorgun", api.patched!!.second.label)
    }

    @Test
    fun mutations_reachTheirEndpoints() = runBlocking {
        val api = FakeVoiceApi()
        val repo = VoiceProfileRepository(api)
        repo.deleteSample("s1")
        repo.confirm("h1")
        repo.reject("h2")
        repo.deleteProfile()
        assertEquals("s1", api.deletedSample)
        assertEquals("h1", api.confirmed)
        assertEquals("h2", api.rejected)
        assertTrue(api.profileDeleted)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew testDebugUnitTest --tests '*VoiceProfileRepositoryTest*'
```

Expected: FAIL — `Unresolved reference: VoiceProfileRepository`.

- [ ] **Step 3: Write minimal implementation**

`android/app/src/main/java/com/jarvis/data/voice/VoiceProfile.kt`:

```kotlin
package com.jarvis.data.voice

/** Where a gallery sample came from (spec §4.1). Wire values: enroll/auto/manual. */
enum class SampleSource { ENROLL, AUTO, MANUAL }

/** Whether the user has already ruled on a history row (spec §6). */
enum class Correction { NONE, CONFIRMED, REJECTED }

data class VoiceCounts(val anchors: Int, val auto: Int, val manual: Int) {
    val total: Int get() = anchors + auto + manual
}

data class VoiceSample(
    val id: String,
    val source: SampleSource,
    val ts: String?,
    val deviceHint: String?,
    val label: String?,
    val note: String?,
)

/**
 * One verified utterance. Unlike the wire model, `score` and `verified` are non-null
 * here: rows that lack them are dropped at the repository boundary, so nothing
 * downstream has to ask "what if there is no verdict".
 */
data class HistoryRow(
    val id: String,
    val ts: String?,
    val score: Double,
    val verified: Boolean,
    val deviceHint: String?,
    val trustLevel: String?,
    val adaptedSampleId: String?,
    val correction: Correction,
)

data class VoiceQuality(
    val meanVerifiedScore: Double?,
    val failRate: Double?,
    val byDevice: Map<String, Double>,
    val byLabel: Map<String, Double>,
    val last10: Double?,
    val previous10: Double?,
)

data class VoiceProfile(
    val counts: VoiceCounts,
    val samples: List<VoiceSample>,
    val history: List<HistoryRow>,
    val quality: VoiceQuality,
) {
    val isEmpty: Boolean get() = samples.isEmpty() && history.isEmpty()
}
```

`android/app/src/main/java/com/jarvis/data/voice/VoiceProfileRepository.kt`:

```kotlin
package com.jarvis.data.voice

import com.jarvis.data.net.SamplePatchRequest
import com.jarvis.data.net.VoiceApi
import com.jarvis.data.net.VoiceHistoryDto
import com.jarvis.data.net.VoiceQualityDto
import com.jarvis.data.net.VoiceSampleDto

/**
 * Data access for the speaker-identity management screen (spec §6).
 *
 * Every mutation is a plain pass-through: the last-anchor guard, the manual cap,
 * idempotency and label validation all live on the server, and re-deriving any of them
 * here would put one rule in two places (spec §9). What this layer DOES own is the
 * tolerant-wire / strict-domain boundary.
 */
class VoiceProfileRepository(private val api: VoiceApi) {

    suspend fun load(): VoiceProfile {
        val dto = api.profile()
        return VoiceProfile(
            counts = VoiceCounts(dto.counts.anchors, dto.counts.auto, dto.counts.manual),
            samples = dto.samples.map { it.toDomain() },
            // A row with no score or no verdict cannot be rendered or corrected;
            // dropping it keeps one bad row from blanking the entire screen.
            history = dto.history.mapNotNull { it.toDomainOrNull() },
            quality = dto.quality.toDomain(),
        )
    }

    suspend fun setLabel(sampleId: String, label: String?) {
        api.patchSample(sampleId, SamplePatchRequest(label = label))
    }

    suspend fun setNote(sampleId: String, note: String?) {
        api.patchSample(sampleId, SamplePatchRequest(note = note))
    }

    suspend fun deleteSample(sampleId: String) { api.deleteSample(sampleId) }

    suspend fun confirm(entryId: String) { api.confirm(entryId) }

    suspend fun reject(entryId: String) { api.reject(entryId) }

    suspend fun deleteProfile() { api.deleteProfile() }
}

private fun VoiceSampleDto.toDomain() = VoiceSample(
    id = id,
    source = when (source) {
        "enroll" -> SampleSource.ENROLL
        "manual" -> SampleSource.MANUAL
        // Anything else (including a future source we don't know yet) reads as auto
        // rather than crashing a screen whose whole job is to show what IS there.
        else -> SampleSource.AUTO
    },
    ts = ts,
    deviceHint = device_hint,
    label = label,
    note = note,
)

private fun VoiceHistoryDto.toDomainOrNull(): HistoryRow? {
    val s = score ?: return null
    val v = verified ?: return null
    return HistoryRow(
        id = id,
        ts = ts,
        score = s,
        verified = v,
        deviceHint = device_hint,
        trustLevel = trust_level,
        adaptedSampleId = adapted_sample_id,
        correction = when (correction) {
            "confirmed" -> Correction.CONFIRMED
            "rejected" -> Correction.REJECTED
            else -> Correction.NONE
        },
    )
}

private fun VoiceQualityDto.toDomain() = VoiceQuality(
    meanVerifiedScore = mean_verified_score,
    failRate = fail_rate,
    byDevice = by_device.filterValues { it != null }.mapValues { it.value!! },
    byLabel = by_label.filterValues { it != null }.mapValues { it.value!! },
    last10 = trend.last10,
    previous10 = trend.previous10,
)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew testDebugUnitTest
```

Expected: PASS — 41 JVM testi.

- [ ] **Step 5: Mutation check**

`history = dto.history.mapNotNull { it.toDomainOrNull() }` satırındaki `mapNotNull`'ı düşürme yapmayan bir hale getir (ör. `toDomainOrNull` içinde `score ?: 0.0`). `load_dropsHistoryRowsMissingScoreOrVerified_andKeepsTheRest` **KIRMIZI olmalı**. Geri al.

- [ ] **Step 6: Commit**

```bash
cd /home/user/Projeler/JARVIS
git add android/app/src/main/java/com/jarvis/data/voice/ android/app/src/test/java/com/jarvis/data/voice/
git commit -m "feat(voice-ui): domain models + repository with a tolerant-wire/strict-domain boundary"
```

---

### Task 4: Kalite yorumu — saf Türkçe metin üretimi

**Files:**
- Create: `android/app/src/main/java/com/jarvis/ui/voice/QualitySummary.kt`
- Create: `android/app/src/main/java/com/jarvis/ui/voice/VoiceLabels.kt`
- Test: `android/app/src/test/java/com/jarvis/ui/voice/QualitySummaryTest.kt`

**Interfaces:**
- Consumes: Task 3'ün `VoiceQuality`, `VoiceCounts`.
- Produces: `fun summarize(quality: VoiceQuality, counts: VoiceCounts): QualitySummary`; `data class QualitySummary(headline, detail, trend: TrendDirection)`; `enum class TrendDirection { UP, DOWN, FLAT, UNKNOWN }`; `fun labelDisplayName(ascii: String): String`; `fun sourceBadge(source: SampleSource): String`.

**Neden ayrı task:** Spec §9 "düz Türkçe yorum" istiyor. Bu, Compose'un içine gömülürse yalnızca instrumented testle sınanabilir; saf fonksiyon olarak JVM'de saniyeler içinde ve tüm sınır durumlarıyla sınanır. Eşikler burada **sunulur, karar verilmez** — sunucudaki `accept=0.35` / `adapt=0.60` kopyalanmaz; metin ham ortalamayı yorumlar.

- [ ] **Step 1: Write the failing test**

`android/app/src/test/java/com/jarvis/ui/voice/QualitySummaryTest.kt`:

```kotlin
package com.jarvis.ui.voice

import com.jarvis.data.voice.SampleSource
import com.jarvis.data.voice.VoiceCounts
import com.jarvis.data.voice.VoiceQuality
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class QualitySummaryTest {

    private fun quality(
        mean: Double? = null,
        fail: Double? = null,
        last10: Double? = null,
        previous10: Double? = null,
        byLabel: Map<String, Double> = emptyMap(),
    ) = VoiceQuality(mean, fail, emptyMap(), byLabel, last10, previous10)

    @Test
    fun noHistoryYet_saysSoPlainly_ratherThanShowingAFakeZero() {
        val s = summarize(quality(), VoiceCounts(7, 0, 0))
        assertEquals(TrendDirection.UNKNOWN, s.trend)
        assertTrue(s.headline.contains("Henüz"))
        assertTrue("sıfır skor uydurulmamalı: ${s.detail}", !s.detail.contains("0.00"))
    }

    @Test
    fun noProfileAtAll_tellsTheUserToEnroll() {
        val s = summarize(quality(), VoiceCounts(0, 0, 0))
        assertTrue(s.headline.contains("Ses kimliği yok"))
    }

    @Test
    fun highMean_readsAsStrongRecognition() {
        val s = summarize(quality(mean = 0.74, fail = 0.05), VoiceCounts(7, 2, 0))
        assertTrue(s.headline.contains("güçlü"))
        assertTrue(s.detail.contains("0.74"))
    }

    @Test
    fun middlingMean_readsAsUsableButImprovable() {
        val s = summarize(quality(mean = 0.48, fail = 0.2), VoiceCounts(7, 1, 0))
        assertTrue(s.headline.contains("orta"))
    }

    @Test
    fun lowMean_readsAsWeak_andMentionsTheFailureRate() {
        val s = summarize(quality(mean = 0.22, fail = 0.6), VoiceCounts(7, 0, 0))
        assertTrue(s.headline.contains("zayıf"))
        assertTrue("başarısız oran yüzde olarak görünmeli: ${s.detail}", s.detail.contains("%60"))
    }

    @Test
    fun risingTrend_isReportedAsUp() {
        assertEquals(
            TrendDirection.UP,
            summarize(quality(mean = 0.6, last10 = 0.70, previous10 = 0.60), VoiceCounts(7, 0, 0)).trend,
        )
    }

    @Test
    fun fallingTrend_isReportedAsDown() {
        assertEquals(
            TrendDirection.DOWN,
            summarize(quality(mean = 0.6, last10 = 0.55, previous10 = 0.68), VoiceCounts(7, 0, 0)).trend,
        )
    }

    /** A hair of movement is noise, not a trend — it must not flip the arrow. */
    @Test
    fun tinyMovement_readsAsFlat_notAsATrend() {
        assertEquals(
            TrendDirection.FLAT,
            summarize(quality(mean = 0.6, last10 = 0.701, previous10 = 0.700), VoiceCounts(7, 0, 0)).trend,
        )
    }

    /** Only one window of history exists yet — there is nothing to compare against. */
    @Test
    fun missingPreviousWindow_isUnknown_notFlat() {
        assertEquals(
            TrendDirection.UNKNOWN,
            summarize(quality(mean = 0.6, last10 = 0.70, previous10 = null), VoiceCounts(7, 0, 0)).trend,
        )
    }

    /** Label-linked means are what calibration actually uses (spec §6.1). */
    @Test
    fun labelBreakdown_appearsWithTurkishDisplayNames() {
        val s = summarize(
            quality(mean = 0.6, byLabel = mapOf("gurultulu" to 0.41)),
            VoiceCounts(7, 0, 0),
        )
        assertTrue(s.detail.contains("Gürültülü"))
        assertTrue(s.detail.contains("0.41"))
    }

    @Test
    fun labelDisplayNames_coverTheWholeServerVocabulary() {
        assertEquals("Sağlıklı", labelDisplayName("saglikli"))
        assertEquals("Hasta", labelDisplayName("hasta"))
        assertEquals("Yorgun", labelDisplayName("yorgun"))
        assertEquals("Gürültülü", labelDisplayName("gurultulu"))
        assertEquals("Kulaklık", labelDisplayName("kulaklik"))
        assertEquals("Hoparlör", labelDisplayName("hoparlor"))
        assertEquals("Araç", labelDisplayName("arac"))
    }

    /** An unknown label must still render — the server's vocabulary can grow. */
    @Test
    fun unknownLabel_rendersItselfRatherThanDisappearing() {
        assertEquals("bilinmeyen", labelDisplayName("bilinmeyen"))
    }

    @Test
    fun sourceBadges_areTheTurkishWordsFromTheSpec() {
        assertEquals("kayıt", sourceBadge(SampleSource.ENROLL))
        assertEquals("otomatik", sourceBadge(SampleSource.AUTO))
        assertEquals("elle", sourceBadge(SampleSource.MANUAL))
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew testDebugUnitTest --tests '*QualitySummaryTest*'
```

Expected: FAIL — `Unresolved reference: summarize`.

- [ ] **Step 3: Write minimal implementation**

`android/app/src/main/java/com/jarvis/ui/voice/VoiceLabels.kt`:

```kotlin
package com.jarvis.ui.voice

import com.jarvis.data.voice.SampleSource

/**
 * The server's label vocabulary is closed and ASCII on purpose (config
 * SPEAKER_SAMPLE_LABELS): those are API VALUES, not UI copy. This is the one place
 * they become Turkish, so the wire never carries display text and the display never
 * carries wire values.
 */
private val LABEL_NAMES = mapOf(
    "saglikli" to "Sağlıklı",
    "hasta" to "Hasta",
    "yorgun" to "Yorgun",
    "gurultulu" to "Gürültülü",
    "kulaklik" to "Kulaklık",
    "hoparlor" to "Hoparlör",
    "arac" to "Araç",
)

/** Every wire label the server accepts, in the order the picker should show them. */
val LABEL_VALUES: List<String> = LABEL_NAMES.keys.toList()

/** Unknown values render as themselves: the server's vocabulary may grow before we do. */
fun labelDisplayName(ascii: String): String = LABEL_NAMES[ascii] ?: ascii

/** Source badge copy from spec §9: kayıt / otomatik / elle. */
fun sourceBadge(source: SampleSource): String = when (source) {
    SampleSource.ENROLL -> "kayıt"
    SampleSource.AUTO -> "otomatik"
    SampleSource.MANUAL -> "elle"
}
```

`android/app/src/main/java/com/jarvis/ui/voice/QualitySummary.kt`:

```kotlin
package com.jarvis.ui.voice

import com.jarvis.data.voice.VoiceCounts
import com.jarvis.data.voice.VoiceQuality
import java.util.Locale

enum class TrendDirection { UP, DOWN, FLAT, UNKNOWN }

data class QualitySummary(
    val headline: String,
    val detail: String,
    val trend: TrendDirection,
)

/**
 * Movement smaller than this between the last-10 and previous-10 windows is noise, not
 * a trend. Without a dead band the arrow flips on rounding and the card looks unstable.
 */
private const val TREND_DEAD_BAND = 0.02

/**
 * Turns the server's raw indicators (spec §6.1) into the plain Turkish the status card
 * shows (spec §9).
 *
 * This deliberately does NOT restate the server's accept/adapt thresholds. Copying
 * 0.35/0.60 here would make the same number live in two places and drift after the
 * calibration pass; the bands below describe how recognition FEELS, and the exact
 * verdict stays where it is enforced.
 */
fun summarize(quality: VoiceQuality, counts: VoiceCounts): QualitySummary {
    if (counts.total == 0) {
        return QualitySummary(
            headline = "Ses kimliği yok",
            detail = "Henüz kayıtlı ses örneğin yok. Ses kaydı yapıldığında burada görünecek.",
            trend = TrendDirection.UNKNOWN,
        )
    }

    val mean = quality.meanVerifiedScore
    if (mean == null) {
        return QualitySummary(
            headline = "Henüz ölçüm yok",
            detail = "${counts.total} örnek kayıtlı, ama daha doğrulanmış bir söyleyiş yok. " +
                "Sesli konuştuğunda buraya skorlar düşmeye başlayacak.",
            trend = TrendDirection.UNKNOWN,
        )
    }

    val headline = when {
        mean >= 0.65 -> "Tanınma güçlü"
        mean >= 0.40 -> "Tanınma orta"
        else -> "Tanınma zayıf"
    }

    val parts = mutableListOf("Doğrulanmış ortalama ${fmt(mean)}")
    quality.failRate?.let { parts += "başarısız oran %${(it * 100).toInt()}" }
    if (quality.byLabel.isNotEmpty()) {
        parts += quality.byLabel.entries
            .sortedBy { it.key }
            .joinToString(", ") { "${labelDisplayName(it.key)}: ${fmt(it.value)}" }
    }

    return QualitySummary(
        headline = headline,
        detail = parts.joinToString(" · "),
        trend = trendOf(quality.last10, quality.previous10),
    )
}

private fun trendOf(last10: Double?, previous10: Double?): TrendDirection {
    // A null previous window means only one window of history exists — that is
    // "not comparable yet", which is different from "no change".
    if (last10 == null || previous10 == null) return TrendDirection.UNKNOWN
    val delta = last10 - previous10
    return when {
        delta > TREND_DEAD_BAND -> TrendDirection.UP
        delta < -TREND_DEAD_BAND -> TrendDirection.DOWN
        else -> TrendDirection.FLAT
    }
}

/** Locale.ROOT: a Turkish-locale device would otherwise render "0,74" and break parity
 *  with the scores logged server-side. */
private fun fmt(v: Double): String = String.format(Locale.ROOT, "%.2f", v)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew testDebugUnitTest
```

Expected: PASS — 54 JVM testi.

- [ ] **Step 5: Mutation check**

`TREND_DEAD_BAND`'i `0.0` yap. `tinyMovement_readsAsFlat_notAsATrend` **KIRMIZI olmalı**. Geri al.

- [ ] **Step 6: Commit**

```bash
cd /home/user/Projeler/JARVIS
git add android/app/src/main/java/com/jarvis/ui/voice/ android/app/src/test/java/com/jarvis/ui/voice/
git commit -m "feat(voice-ui): pure Turkish quality interpretation with a trend dead band"
```

---

### Task 5: `VoiceProfileViewModel` durum makinesi

**Files:**
- Create: `android/app/src/main/java/com/jarvis/ui/voice/VoiceProfileUiState.kt`
- Create: `android/app/src/main/java/com/jarvis/ui/voice/VoiceProfileViewModel.kt`
- Test: `android/app/src/test/java/com/jarvis/ui/voice/VoiceProfileViewModelTest.kt`

**Interfaces:**
- Consumes: `VoiceProfileRepository`, `summarize`.
- Produces: `VoiceProfileUiState(gate, loading, profile, summary, error, mutatingId, deleted)`, `enum class GatePhase { CHECKING, UNLOCKED, DENIED }`, `VoiceProfileViewModel(repo)` with `onUnlocked()`, `onUnlockFailed(reason)`, `load()`, `setLabel(id, label)`, `deleteSample(id)`, `confirm(id)`, `reject(id)`, `deleteProfile()`, `dismissError()`.

**İki taşıyıcı tasarım kararı:**

1. **Mutasyondan sonra yeniden yükle, iyimser güncelleme yok.** Sunucu `confirm`'de örneği yerinde terfi mi ettirdi yoksa yenisini mi ekledi, cap doldu mu, `reject` gerçekten bir örnek sildi mi — bunların hepsi sunucu durumuna bağlı. İstemcide tahmin etmek spec §9'u ihlal eder ve sessizce ayrışır.
2. **Aynı anda tek mutasyon (`mutatingId`).** Uçlar idempotent ama iki eşzamanlı mutasyon birbirinin `load()`'uyla yarışır ve kullanıcı, kaybolan bir satır görür. Tek uçuş kuralı ayrıca çift dokunuşun cap yakmasını da engeller.

- [ ] **Step 1: Write the failing test**

`android/app/src/test/java/com/jarvis/ui/voice/VoiceProfileViewModelTest.kt`:

```kotlin
package com.jarvis.ui.voice

import com.jarvis.data.net.ConfirmResponse
import com.jarvis.data.net.ProfileDeletedResponse
import com.jarvis.data.net.RejectResponse
import com.jarvis.data.net.SampleDeletedResponse
import com.jarvis.data.net.SamplePatchRequest
import com.jarvis.data.net.VoiceApi
import com.jarvis.data.net.VoiceCountsDto
import com.jarvis.data.net.VoiceHistoryDto
import com.jarvis.data.net.VoiceProfileResponse
import com.jarvis.data.net.VoiceSampleDto
import com.jarvis.data.voice.VoiceProfileRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response

@OptIn(ExperimentalCoroutinesApi::class)
class VoiceProfileViewModelTest {

    private val dispatcher = StandardTestDispatcher()

    @Before fun setUp() = Dispatchers.setMain(dispatcher)
    @After fun tearDown() = Dispatchers.resetMain()

    private fun http(code: Int, detail: String) = HttpException(
        Response.error<Any>(
            code,
            """{"detail":"$detail"}""".toResponseBody("application/json".toMediaType()),
        ),
    )

    private class FakeVoiceApi : VoiceApi {
        var response = VoiceProfileResponse(
            counts = VoiceCountsDto(anchors = 1),
            samples = listOf(VoiceSampleDto("s1", "enroll")),
            history = listOf(VoiceHistoryDto("h1", "t", 0.7, true)),
        )
        var profileCalls = 0
        var deleteSampleError: Throwable? = null
        var confirmError: Throwable? = null
        var confirmCalls = 0
        var profileDeleted = false

        override suspend fun profile(): VoiceProfileResponse {
            profileCalls++
            return response
        }
        override suspend fun patchSample(id: String, req: SamplePatchRequest) =
            VoiceSampleDto(id, "auto", label = req.label)
        override suspend fun deleteSample(id: String): SampleDeletedResponse {
            deleteSampleError?.let { throw it }
            return SampleDeletedResponse(id)
        }
        override suspend fun confirm(id: String): ConfirmResponse {
            confirmCalls++
            confirmError?.let { throw it }
            return ConfirmResponse("s-new")
        }
        override suspend fun reject(id: String) = RejectResponse(null)
        override suspend fun deleteProfile(): ProfileDeletedResponse {
            profileDeleted = true
            return ProfileDeletedResponse(true)
        }
    }

    private fun vm(api: FakeVoiceApi) = VoiceProfileViewModel(VoiceProfileRepository(api))

    /** The screen is gated: nothing is fetched before the biometric prompt succeeds. */
    @Test
    fun startsLocked_andFetchesNothingUntilUnlocked() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        val model = vm(api)
        advanceUntilIdle()
        assertEquals(GatePhase.CHECKING, model.state.value.gate)
        assertEquals(0, api.profileCalls)
    }

    @Test
    fun unlocking_loadsTheProfileAndBuildsTheSummary() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()

        assertEquals(GatePhase.UNLOCKED, model.state.value.gate)
        assertEquals(1, api.profileCalls)
        assertNotNull(model.state.value.profile)
        assertNotNull(model.state.value.summary)
        assertFalse(model.state.value.loading)
    }

    @Test
    fun unlockFailure_landsOnDenied_withATurkishReason_andNoFetch() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        val model = vm(api)
        model.onUnlockFailed("iptal edildi")
        advanceUntilIdle()

        assertEquals(GatePhase.DENIED, model.state.value.gate)
        assertTrue(model.state.value.error!!.contains("Kilit açılamadı"))
        assertEquals(0, api.profileCalls)
    }

    /**
     * Mutations do not patch local state: whether confirm promoted a sample in place or
     * appended a new one, whether the cap refused it, whether reject actually removed
     * anything -- all of that is server state (spec §9). Re-reading is the only honest
     * way to show the result.
     */
    @Test
    fun aSuccessfulMutation_reloadsFromTheServer() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()
        assertEquals(1, api.profileCalls)

        model.confirm("h1")
        advanceUntilIdle()

        assertEquals(2, api.profileCalls)
        assertNull(model.state.value.mutatingId)
    }

    @Test
    fun aMutationMarksItsRowBusy_thenClearsIt() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()

        model.deleteSample("s1")
        assertEquals("s1", model.state.value.mutatingId)   // set synchronously
        advanceUntilIdle()
        assertNull(model.state.value.mutatingId)
    }

    /**
     * The endpoints are idempotent, but two mutations in flight race each other's
     * reload and the user sees a row blink out. One at a time also stops a double tap
     * from burning a manual-cap slot.
     */
    @Test
    fun aSecondMutationIsIgnoredWhileOneIsInFlight() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()

        model.confirm("h1")
        model.confirm("h1")      // double tap, before the first resolves
        advanceUntilIdle()

        assertEquals(1, api.confirmCalls)
    }

    /** The server owns the rule AND its wording; the client must not paraphrase. */
    @Test
    fun aRefusedDeletion_showsTheServersOwnSentence() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        api.deleteSampleError = http(400, "Son çapa silinemez: çapasız profil ses doğrulayamaz.")
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()

        model.deleteSample("s1")
        advanceUntilIdle()

        assertEquals(
            "Son çapa silinemez: çapasız profil ses doğrulayamaz.",
            model.state.value.error,
        )
        assertNull(model.state.value.mutatingId)
    }

    @Test
    fun aCapRefusal_showsTheServersLiveNumbers() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        api.confirmError = http(400, "Elle eklenen örnek sınırı dolu (5/5).")
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()

        model.confirm("h1")
        advanceUntilIdle()

        assertTrue(model.state.value.error!!.contains("(5/5)"))
    }

    /** A failed mutation must still leave the screen usable and retryable. */
    @Test
    fun afterAFailedMutation_anotherMutationIsStillAccepted() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        api.confirmError = http(400, "olmaz")
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()

        model.confirm("h1")
        advanceUntilIdle()
        api.confirmError = null
        model.confirm("h1")
        advanceUntilIdle()

        assertEquals(2, api.confirmCalls)
        assertNull(model.state.value.error)
    }

    @Test
    fun loadFailure_showsTheServerMessage_andLeavesTheScreenRetryable() = runTest(dispatcher) {
        val api = object : VoiceApi by FakeVoiceApi() {
            override suspend fun profile(): VoiceProfileResponse =
                throw HttpException(
                    Response.error<Any>(
                        502,
                        """{"detail":"İşlem şu anda yapılamıyor (altyapı hatası). Az sonra tekrar dene."}"""
                            .toResponseBody("application/json".toMediaType()),
                    ),
                )
        }
        val model = VoiceProfileViewModel(VoiceProfileRepository(api))
        model.onUnlocked()
        advanceUntilIdle()

        assertTrue(model.state.value.error!!.contains("altyapı hatası"))
        assertFalse(model.state.value.loading)
        assertNull(model.state.value.profile)
    }

    @Test
    fun deletingTheProfile_marksItDeleted_soTheHostCanLeaveTheScreen() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()

        model.deleteProfile()
        advanceUntilIdle()

        assertTrue(api.profileDeleted)
        assertTrue(model.state.value.deleted)
    }

    @Test
    fun dismissError_clearsIt() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        api.confirmError = http(400, "olmaz")
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()
        model.confirm("h1")
        advanceUntilIdle()
        assertNotNull(model.state.value.error)

        model.dismissError()
        assertNull(model.state.value.error)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew testDebugUnitTest --tests '*VoiceProfileViewModelTest*'
```

Expected: FAIL — `Unresolved reference: VoiceProfileViewModel`.

- [ ] **Step 3: Write minimal implementation**

`android/app/src/main/java/com/jarvis/ui/voice/VoiceProfileUiState.kt`:

```kotlin
package com.jarvis.ui.voice

import com.jarvis.data.voice.VoiceProfile

/**
 * The biometric gate, modelled the same way AuthPhase models sign-in: the host owns
 * the Android-side prompt and reports back, so the ViewModel stays JVM-testable.
 */
enum class GatePhase {
    /** The prompt is up (or about to be). Show nothing sensitive. */
    CHECKING,

    /** The user proved themselves to the phone. Content may load. */
    UNLOCKED,

    /** Cancelled, failed, or no lock configured. Offer a retry, show no content. */
    DENIED,
}

data class VoiceProfileUiState(
    val gate: GatePhase = GatePhase.CHECKING,
    val loading: Boolean = false,
    val profile: VoiceProfile? = null,
    val summary: QualitySummary? = null,
    val error: String? = null,
    /** Id of the sample/history row whose mutation is in flight; null when idle. */
    val mutatingId: String? = null,
    /** The whole profile was deleted; the host should leave the screen. */
    val deleted: Boolean = false,
)
```

`android/app/src/main/java/com/jarvis/ui/voice/VoiceProfileViewModel.kt`:

```kotlin
package com.jarvis.ui.voice

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.data.net.userMessage
import com.jarvis.data.voice.VoiceProfileRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * State machine for the speaker-identity management screen (spec §9).
 *
 * No Android types here: the biometric prompt lives at the Activity layer and reports
 * in through [onUnlocked]/[onUnlockFailed], exactly as sign-in does for the chat screen.
 */
class VoiceProfileViewModel(private val repo: VoiceProfileRepository) : ViewModel() {

    private val _state = MutableStateFlow(VoiceProfileUiState())
    val state: StateFlow<VoiceProfileUiState> = _state.asStateFlow()

    /** The device lock was satisfied. Only now does anything get fetched. */
    fun onUnlocked() {
        _state.update { it.copy(gate = GatePhase.UNLOCKED, error = null) }
        load()
    }

    fun onUnlockFailed(reason: String?) {
        _state.update {
            it.copy(
                gate = GatePhase.DENIED,
                error = "Kilit açılamadı: ${reason ?: "doğrulama tamamlanmadı"}",
            )
        }
    }

    fun load() {
        _state.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            try {
                val profile = repo.load()
                _state.update {
                    it.copy(
                        loading = false,
                        profile = profile,
                        summary = summarize(profile.quality, profile.counts),
                    )
                }
            } catch (e: Exception) {
                _state.update {
                    it.copy(loading = false, error = e.userMessage("Ses kimliği yüklenemedi."))
                }
            }
        }
    }

    fun setLabel(sampleId: String, label: String?) =
        mutate(sampleId, "Etiket güncellenemedi.") { repo.setLabel(sampleId, label) }

    fun deleteSample(sampleId: String) =
        mutate(sampleId, "Örnek silinemedi.") { repo.deleteSample(sampleId) }

    fun confirm(entryId: String) =
        mutate(entryId, "İşlem tamamlanamadı.") { repo.confirm(entryId) }

    fun reject(entryId: String) =
        mutate(entryId, "İşlem tamamlanamadı.") { repo.reject(entryId) }

    fun deleteProfile() {
        if (_state.value.mutatingId != null) return
        _state.update { it.copy(mutatingId = PROFILE_MUTATION_ID, error = null) }
        viewModelScope.launch {
            try {
                repo.deleteProfile()
                _state.update { it.copy(mutatingId = null, deleted = true) }
            } catch (e: Exception) {
                _state.update {
                    it.copy(mutatingId = null, error = e.userMessage("Profil silinemedi."))
                }
            }
        }
    }

    fun dismissError() = _state.update { it.copy(error = null) }

    /**
     * One mutation at a time, then a full reload.
     *
     * RELOAD, not a local patch: whether confirm promoted an existing auto sample in
     * place or appended a fresh manual one, whether the cap refused it, whether reject
     * actually removed anything — every one of those is server state (spec §9).
     * Guessing here would put the rules in two places.
     *
     * ONE AT A TIME: the endpoints are idempotent, but two in-flight mutations race
     * each other's reload and the user watches a row blink out. It also stops a double
     * tap from spending a manual-cap slot twice.
     */
    private fun mutate(id: String, fallback: String, block: suspend () -> Unit) {
        if (_state.value.mutatingId != null) return
        _state.update { it.copy(mutatingId = id, error = null) }
        viewModelScope.launch {
            try {
                block()
                _state.update { it.copy(mutatingId = null) }
                load()
            } catch (e: Exception) {
                _state.update { it.copy(mutatingId = null, error = e.userMessage(fallback)) }
            }
        }
    }

    private companion object {
        /** Sentinel so whole-profile deletion shares the single-flight guard. */
        const val PROFILE_MUTATION_ID = "__profile__"
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew testDebugUnitTest
```

Expected: PASS — 66 JVM testi.

- [ ] **Step 5: Mutation check (iki ayrı mutasyon)**

1. `mutate()`'in başındaki `if (_state.value.mutatingId != null) return` satırını sil → `aSecondMutationIsIgnoredWhileOneIsInFlight` **KIRMIZI olmalı**.
2. `mutate()`'in `try` bloğundaki `load()` çağrısını sil → `aSuccessfulMutation_reloadsFromTheServer` **KIRMIZI olmalı**.

İkisi de kırmızı olmuyorsa test taşıyıcı değildir. Sonra geri al.

- [ ] **Step 6: Commit**

```bash
cd /home/user/Projeler/JARVIS
git add android/app/src/main/java/com/jarvis/ui/voice/ android/app/src/test/java/com/jarvis/ui/voice/VoiceProfileViewModelTest.kt
git commit -m "feat(voice-ui): profile view model — single-flight mutations, reload-not-guess"
```

---

### Task 6: Biyometrik kapı + `MainActivity` → `FragmentActivity`

**Files:**
- Modify: `android/gradle/libs.versions.toml`
- Modify: `android/app/build.gradle.kts`
- Create: `android/app/src/main/java/com/jarvis/data/auth/BiometricGate.kt`
- Modify: `android/app/src/main/java/com/jarvis/MainActivity.kt` (yalnızca üst sınıf)
- Test: `android/app/src/androidTest/java/com/jarvis/data/auth/BiometricGateTest.kt`

**Interfaces:**
- Consumes: `androidx.biometric` 1.1.0.
- Produces: `interface BiometricGate { fun isAvailable(): Boolean; fun prompt(activity: FragmentActivity, onResult: (Result<Unit>) -> Unit) }`, `class AndroidBiometricGate(context: Context) : BiometricGate`, `const val GATE_AUTHENTICATORS`.

**Neden bu task burada:** Ekranlar (Task 7-8) kapının **arkasında** yaşayacak; kapı önce derlenip cihazda doğrulanmalı. `MainActivity`'nin üst sınıfını burada değiştiriyoruz çünkü `BiometricPrompt`'un `FragmentActivity`'den başka girişi yok (yukarıda `javap` ile kanıtlandı) ve bu değişikliğin mevcut 3 instrumented testi kırmadığı burada, tek başına doğrulanmalı.

- [ ] **Step 1: Add the dependency**

`android/gradle/libs.versions.toml` — `[versions]` bloğuna ekle:

```toml
# 1.1.0 is the latest STABLE; the 1.2.0/1.4.0 lines are alpha only.
# Brings androidx.fragment (compile) — required, BiometricPrompt only accepts a
# FragmentActivity — and androidx.appcompat (runtime). The appcompat pull-in is
# expected, not a misconfiguration.
biometric = "1.1.0"
```

`[libraries]` bloğuna ekle:

```toml
androidx-biometric = { group = "androidx.biometric", name = "biometric", version.ref = "biometric" }
```

`android/app/build.gradle.kts` — `implementation(libs.androidx.lifecycle.viewmodel.compose)` satırının altına ekle:

```kotlin
    implementation(libs.androidx.biometric)
```

- [ ] **Step 2: Write the failing test**

`android/app/src/androidTest/java/com/jarvis/data/auth/BiometricGateTest.kt`:

```kotlin
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
```

- [ ] **Step 3: Run test to verify it fails**

Emülatörü hazırla (kapalıysa):

```bash
export ANDROID_HOME=$HOME/Android/Sdk
$ANDROID_HOME/emulator/emulator -avd jarvis_avd -no-window -gpu swiftshader_indirect -no-audio &
$ANDROID_HOME/platform-tools/adb wait-for-device
$ANDROID_HOME/platform-tools/adb shell getprop sys.boot_completed   # 1 olana kadar bekle
```

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew connectedDebugAndroidTest
```

Expected: FAIL — `Unresolved reference: GATE_AUTHENTICATORS`.

- [ ] **Step 4: Write minimal implementation**

`android/app/src/main/java/com/jarvis/data/auth/BiometricGate.kt`:

```kotlin
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
```

`android/app/src/main/java/com/jarvis/MainActivity.kt` — **yalnızca** üst sınıfı ve importu değiştir:

```kotlin
// import androidx.activity.ComponentActivity      <- SİL
import androidx.fragment.app.FragmentActivity      // <- EKLE
```

```kotlin
// class MainActivity : ComponentActivity() {      <- ESKİ
class MainActivity : FragmentActivity() {
```

> `FragmentActivity`, `androidx.activity.ComponentActivity`'yi genişletir, dolayısıyla `setContent {}` ve `viewModel {}` aynen çalışır. Bu değişiklik olmadan `BiometricPrompt` **derlenmez** — başka bir kurucu yok.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew testDebugUnitTest connectedDebugAndroidTest
```

Expected: PASS — 66 JVM + 5 instrumented (mevcut 3 + yeni 2). Mevcut `SmokeTest`/`ChatScreenTest`/`EndToEndTest` **kırılmamalı**; kırılırsa `FragmentActivity` geçişi bir şeyi bozmuş demektir, orada dur ve kök nedeni bul.

- [ ] **Step 6: Commit**

```bash
cd /home/user/Projeler/JARVIS
git add android/gradle/libs.versions.toml android/app/build.gradle.kts \
        android/app/src/main/java/com/jarvis/data/auth/BiometricGate.kt \
        android/app/src/main/java/com/jarvis/MainActivity.kt \
        android/app/src/androidTest/java/com/jarvis/data/auth/BiometricGateTest.kt
git commit -m "feat(voice-ui): biometric gate; MainActivity becomes a FragmentActivity"
```

---

### Task 7: Ekran A — iskelet, durum kartı, galeri listesi

**Files:**
- Create: `android/app/src/main/java/com/jarvis/ui/voice/VoiceProfileScreen.kt`
- Test: `android/app/src/androidTest/java/com/jarvis/ui/voice/VoiceProfileScreenTest.kt`

**Interfaces:**
- Consumes: `VoiceProfileUiState`, `QualitySummary`, `labelDisplayName`, `sourceBadge`, `LABEL_VALUES`.
- Produces: `@Composable fun VoiceProfileScreen(state, onBack, onRetryUnlock, onRetryLoad, onSetLabel, onDeleteSample, onConfirm, onReject, onDeleteProfile, onDismissError)`.

**testTag sözleşmesi** (Task 8 ve 9 bunlara dayanır): `voice_back`, `voice_locked`, `voice_retry_unlock`, `voice_status_headline`, `voice_status_detail`, `voice_empty`, `voice_sample_<id>`, `voice_sample_delete_<id>`, `voice_sample_label_<id>`, `voice_label_option_<value>`, `voice_history_<id>`, `voice_confirm_<id>`, `voice_reject_<id>`, `voice_danger_open`, `voice_danger_input`, `voice_danger_confirm`, `voice_error`, `voice_placeholder_record`, `voice_placeholder_retrain`.

- [ ] **Step 1: Write the failing test**

`android/app/src/androidTest/java/com/jarvis/ui/voice/VoiceProfileScreenTest.kt`:

```kotlin
package com.jarvis.ui.voice

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.jarvis.data.voice.Correction
import com.jarvis.data.voice.HistoryRow
import com.jarvis.data.voice.SampleSource
import com.jarvis.data.voice.VoiceCounts
import com.jarvis.data.voice.VoiceProfile
import com.jarvis.data.voice.VoiceQuality
import com.jarvis.data.voice.VoiceSample
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class VoiceProfileScreenTest {

    @get:Rule val compose = createComposeRule()

    private val profile = VoiceProfile(
        counts = VoiceCounts(anchors = 2, auto = 1, manual = 0),
        samples = listOf(
            VoiceSample("s1", SampleSource.ENROLL, "2026-07-25T00:00:00Z", "buds", "kulaklik", null),
            VoiceSample("s2", SampleSource.AUTO, "2026-07-25T01:00:00Z", "arctis", null, null),
        ),
        history = listOf(
            HistoryRow("h1", "2026-07-25T02:00:00Z", 0.71, true, "buds", "HIGH", "s2", Correction.NONE),
        ),
        quality = VoiceQuality(0.71, 0.1, mapOf("buds" to 0.7), emptyMap(), 0.72, 0.65),
    )

    private fun ready() = VoiceProfileUiState(
        gate = GatePhase.UNLOCKED,
        profile = profile,
        summary = summarize(profile.quality, profile.counts),
    )

    @Composable_TestHelper
    private fun render(
        state: VoiceProfileUiState,
        onSetLabel: (String, String?) -> Unit = { _, _ -> },
        onDeleteSample: (String) -> Unit = {},
    ) {
        compose.setContent {
            VoiceProfileScreen(
                state = state,
                onBack = {},
                onRetryUnlock = {},
                onRetryLoad = {},
                onSetLabel = onSetLabel,
                onDeleteSample = onDeleteSample,
                onConfirm = {},
                onReject = {},
                onDeleteProfile = {},
                onDismissError = {},
            )
        }
    }

    /** Nothing about the voice profile may render before the device lock is satisfied. */
    @Test
    fun whileChecking_showsTheLockedState_andNoProfileContent() {
        render(VoiceProfileUiState(gate = GatePhase.CHECKING))
        compose.onNodeWithTag("voice_locked").assertIsDisplayed()
        compose.onAllNodesWithTag("voice_sample_s1").assertCountEquals(0)
    }

    @Test
    fun whenDenied_offersARetry() {
        render(VoiceProfileUiState(gate = GatePhase.DENIED, error = "Kilit açılamadı: iptal"))
        compose.onNodeWithTag("voice_retry_unlock").assertIsDisplayed()
        compose.onNodeWithText("Kilit açılamadı: iptal").assertIsDisplayed()
    }

    @Test
    fun whenUnlocked_showsTheStatusHeadlineAndDetail() {
        render(ready())
        compose.onNodeWithTag("voice_status_headline").assertIsDisplayed()
        compose.onNodeWithText("Tanınma güçlü").assertIsDisplayed()
        compose.onNodeWithTag("voice_status_detail").assertIsDisplayed()
    }

    @Test
    fun galleryRowsShowTheirSourceBadgeAndDevice() {
        render(ready())
        compose.onNodeWithTag("voice_sample_s1").assertIsDisplayed()
        compose.onNodeWithText("kayıt").assertIsDisplayed()
        compose.onNodeWithText("otomatik").assertIsDisplayed()
        compose.onNodeWithText("buds").assertIsDisplayed()
    }

    @Test
    fun aLabelledSampleShowsItsTurkishLabel_notTheAsciiWireValue() {
        render(ready())
        compose.onNodeWithText("Kulaklık").assertIsDisplayed()
    }

    @Test
    fun choosingALabel_reportsTheAsciiWireValue_notTheDisplayName() {
        var chosen: Pair<String, String?>? = null
        render(ready(), onSetLabel = { id, label -> chosen = id to label })

        compose.onNodeWithTag("voice_sample_label_s2").performClick()
        compose.onNodeWithTag("voice_label_option_gurultulu").performClick()

        assertEquals("s2" to "gurultulu", chosen)
    }

    @Test
    fun deletingASample_reportsItsId() {
        var deleted: String? = null
        render(ready(), onDeleteSample = { deleted = it })
        compose.onNodeWithTag("voice_sample_delete_s2").performClick()
        assertEquals("s2", deleted)
    }

    /** A row already mutating must not accept a second tap (the VM would drop it anyway,
     *  but a live-looking button that does nothing reads as a broken screen). */
    @Test
    fun aRowBeingMutated_disablesItsActions() {
        render(ready().copy(mutatingId = "s2"))
        compose.onNodeWithTag("voice_sample_delete_s2").assertIsNotEnabled()
    }

    @Test
    fun anEmptyProfile_showsTheEmptyStateInsteadOfABlankScreen() {
        val empty = VoiceProfile(
            VoiceCounts(0, 0, 0), emptyList(), emptyList(),
            VoiceQuality(null, null, emptyMap(), emptyMap(), null, null),
        )
        render(
            VoiceProfileUiState(
                gate = GatePhase.UNLOCKED,
                profile = empty,
                summary = summarize(empty.quality, empty.counts),
            ),
        )
        compose.onNodeWithTag("voice_empty").assertIsDisplayed()
        compose.onNodeWithText("Ses kimliği yok").assertIsDisplayed()
    }

    /** Spec §9: the screen is built expecting 3b's actions from day one. */
    @Test
    fun the3bPlaceholdersArePresentButInert() {
        render(ready())
        compose.onNodeWithTag("voice_placeholder_record").assertIsNotEnabled()
        compose.onNodeWithTag("voice_placeholder_retrain").assertIsNotEnabled()
    }

    @Test
    fun anErrorIsShownWithTheServersWording() {
        render(ready().copy(error = "Son çapa silinemez: çapasız profil ses doğrulayamaz."))
        compose.onNodeWithTag("voice_error").assertIsDisplayed()
        compose.onNodeWithText("Son çapa silinemez: çapasız profil ses doğrulayamaz.")
            .assertIsDisplayed()
    }
}
```

> **Not:** Yukarıdaki `@Composable_TestHelper` bir yer tutucu DEĞİL — uygulayan bunu silip `render`'ı sade bir `private fun` yapacak (annotation gerekmez, `compose.setContent` zaten composable bağlamı açar). Ayrıca `onAllNodesWithTag`/`assertCountEquals` importlarını ekle: `androidx.compose.ui.test.onAllNodesWithTag`, `androidx.compose.ui.test.assertCountEquals`.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew connectedDebugAndroidTest --tests '*VoiceProfileScreenTest*'
```

Expected: FAIL — `Unresolved reference: VoiceProfileScreen`.

- [ ] **Step 3: Write minimal implementation**

`android/app/src/main/java/com/jarvis/ui/voice/VoiceProfileScreen.kt` — bu task'ta yalnızca **kilit durumu + durum kartı + galeri + hata şeridi + 3b yer tutucuları**. Geçmiş listesi ve tehlikeli bölge Task 8'de eklenecek; bu task'ta o iki bölüm için boş `Spacer` bırakma, sadece yazma.

```kotlin
package com.jarvis.ui.voice

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.jarvis.data.voice.SampleSource
import com.jarvis.data.voice.VoiceSample
import com.jarvis.ui.theme.JarvisBg
import com.jarvis.ui.theme.JarvisCyan
import com.jarvis.ui.theme.JarvisError
import com.jarvis.ui.theme.JarvisSurface
import com.jarvis.ui.theme.JarvisSurfaceHigh
import com.jarvis.ui.theme.JarvisTextMuted
import com.jarvis.ui.theme.JarvisTextPrimary
import com.jarvis.ui.theme.JarvisViolet

/**
 * Speaker-identity management screen (spec §9). Stateless — every decision is hoisted
 * to [VoiceProfileViewModel], and every RULE lives on the server.
 *
 * The content is gated: while [GatePhase.CHECKING] or [GatePhase.DENIED] nothing about
 * the profile renders at all. That gate is a real protection against a real threat (an
 * unlocked phone in someone else's hand) — and it is NEVER reported to the server, which
 * applies its own brakes independently (spec §7).
 */
@Composable
fun VoiceProfileScreen(
    state: VoiceProfileUiState,
    onBack: () -> Unit,
    onRetryUnlock: () -> Unit,
    onRetryLoad: () -> Unit,
    onSetLabel: (String, String?) -> Unit,
    onDeleteSample: (String) -> Unit,
    onConfirm: (String) -> Unit,
    onReject: (String) -> Unit,
    onDeleteProfile: () -> Unit,
    onDismissError: () -> Unit,
) {
    Column(
        Modifier.fillMaxSize().background(JarvisBg).systemBarsPadding(),
    ) {
        TopBar(onBack)

        when (state.gate) {
            GatePhase.CHECKING -> Locked(message = "Kimliğin doğrulanıyor...")
            GatePhase.DENIED -> Denied(state.error, onRetryUnlock)
            GatePhase.UNLOCKED -> Unlocked(
                state = state,
                onRetryLoad = onRetryLoad,
                onSetLabel = onSetLabel,
                onDeleteSample = onDeleteSample,
                onConfirm = onConfirm,
                onReject = onReject,
                onDeleteProfile = onDeleteProfile,
                onDismissError = onDismissError,
            )
        }
    }
}

@Composable
private fun TopBar(onBack: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TextButton(onClick = onBack, modifier = Modifier.testTag("voice_back")) {
            Text("‹ Geri", color = JarvisCyan)
        }
        Spacer(Modifier.size(4.dp))
        Text("Ses kimliğim", style = MaterialTheme.typography.titleLarge, color = JarvisTextPrimary)
    }
}

@Composable
private fun Locked(message: String) {
    Box(Modifier.fillMaxSize().testTag("voice_locked"), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            CircularProgressIndicator(color = JarvisCyan, modifier = Modifier.size(28.dp))
            Spacer(Modifier.size(14.dp))
            Text(message, color = JarvisTextMuted, style = MaterialTheme.typography.bodyLarge)
        }
    }
}

@Composable
private fun Denied(error: String?, onRetryUnlock: () -> Unit) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                error ?: "Kilit açılamadı.",
                color = JarvisError,
                style = MaterialTheme.typography.bodyLarge,
            )
            Spacer(Modifier.size(12.dp))
            TextButton(onClick = onRetryUnlock, modifier = Modifier.testTag("voice_retry_unlock")) {
                Text("Tekrar dene", color = JarvisCyan)
            }
        }
    }
}

@Composable
private fun Unlocked(
    state: VoiceProfileUiState,
    onRetryLoad: () -> Unit,
    onSetLabel: (String, String?) -> Unit,
    onDeleteSample: (String) -> Unit,
    onConfirm: (String) -> Unit,
    onReject: (String) -> Unit,
    onDeleteProfile: () -> Unit,
    onDismissError: () -> Unit,
) {
    val profile = state.profile
    Column(Modifier.fillMaxSize()) {
        state.error?.let { ErrorBanner(it, onDismissError) }

        if (profile == null) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                if (state.loading) {
                    CircularProgressIndicator(color = JarvisCyan, modifier = Modifier.size(28.dp))
                } else {
                    TextButton(onClick = onRetryLoad) { Text("Tekrar dene", color = JarvisCyan) }
                }
            }
            return@Column
        }

        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item { state.summary?.let { StatusCard(it, state.profile.counts.total) } }

            if (profile.isEmpty) {
                item { EmptyState() }
            } else {
                item { SectionTitle("Ses örneklerim (${profile.counts.total})") }
                items(profile.samples, key = { it.id }) { sample ->
                    SampleRow(
                        sample = sample,
                        busy = state.mutatingId != null,
                        onSetLabel = onSetLabel,
                        onDelete = onDeleteSample,
                    )
                }
            }

            item { Placeholders() }
            // Task 8 adds: history section + danger zone.
        }
    }
}

@Composable
private fun StatusCard(summary: QualitySummary, sampleCount: Int) {
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp))
            .background(JarvisSurface).padding(16.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                summary.headline,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                color = JarvisTextPrimary,
                modifier = Modifier.testTag("voice_status_headline"),
            )
            Spacer(Modifier.size(8.dp))
            Text(trendGlyph(summary.trend), color = trendColor(summary.trend))
        }
        Spacer(Modifier.size(6.dp))
        Text(
            summary.detail,
            style = MaterialTheme.typography.bodyMedium,
            color = JarvisTextMuted,
            modifier = Modifier.testTag("voice_status_detail"),
        )
    }
}

private fun trendGlyph(t: TrendDirection) = when (t) {
    TrendDirection.UP -> "↑"
    TrendDirection.DOWN -> "↓"
    TrendDirection.FLAT -> "→"
    TrendDirection.UNKNOWN -> ""
}

@Composable
private fun trendColor(t: TrendDirection) = when (t) {
    TrendDirection.UP -> JarvisCyan
    TrendDirection.DOWN -> JarvisError
    else -> JarvisTextMuted
}

@Composable
private fun SectionTitle(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.titleSmall,
        color = JarvisTextPrimary,
        modifier = Modifier.padding(top = 8.dp),
    )
}

@Composable
private fun SampleRow(
    sample: VoiceSample,
    busy: Boolean,
    onSetLabel: (String, String?) -> Unit,
    onDelete: (String) -> Unit,
) {
    var menuOpen by remember { mutableStateOf(false) }
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp))
            .background(JarvisSurface).padding(14.dp).testTag("voice_sample_${sample.id}"),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Badge(sourceBadge(sample.source), sample.source)
            Spacer(Modifier.size(8.dp))
            Text(
                sample.deviceHint ?: "bilinmiyor",
                style = MaterialTheme.typography.bodyMedium,
                color = JarvisTextPrimary,
            )
            Spacer(Modifier.weight(1f))
            Text(
                shortDate(sample.ts),
                style = MaterialTheme.typography.bodySmall,
                color = JarvisTextMuted,
            )
        }
        Spacer(Modifier.size(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box {
                TextButton(
                    onClick = { menuOpen = true },
                    enabled = !busy,
                    modifier = Modifier.testTag("voice_sample_label_${sample.id}"),
                ) {
                    Text(
                        sample.label?.let { labelDisplayName(it) } ?: "Etiket ekle",
                        color = if (sample.label == null) JarvisTextMuted else JarvisViolet,
                    )
                }
                DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                    LABEL_VALUES.forEach { value ->
                        DropdownMenuItem(
                            text = { Text(labelDisplayName(value)) },
                            modifier = Modifier.testTag("voice_label_option_$value"),
                            onClick = {
                                menuOpen = false
                                // The ASCII wire value goes out, never the display name.
                                onSetLabel(sample.id, value)
                            },
                        )
                    }
                    if (sample.label != null) {
                        DropdownMenuItem(
                            text = { Text("Etiketi kaldır") },
                            modifier = Modifier.testTag("voice_label_option_none"),
                            onClick = {
                                menuOpen = false
                                onSetLabel(sample.id, null)
                            },
                        )
                    }
                }
            }
            Spacer(Modifier.weight(1f))
            TextButton(
                onClick = { onDelete(sample.id) },
                enabled = !busy,
                modifier = Modifier.testTag("voice_sample_delete_${sample.id}"),
            ) {
                Text("Sil", color = JarvisError)
            }
        }
        sample.note?.takeIf { it.isNotBlank() }?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = JarvisTextMuted)
        }
    }
}

@Composable
private fun Badge(text: String, source: SampleSource) {
    val tint = when (source) {
        SampleSource.ENROLL -> JarvisCyan
        SampleSource.MANUAL -> JarvisViolet
        SampleSource.AUTO -> JarvisTextMuted
    }
    Text(
        text,
        style = MaterialTheme.typography.labelMedium,
        color = tint,
        modifier = Modifier.clip(RoundedCornerShape(8.dp)).background(JarvisSurfaceHigh)
            .padding(horizontal = 8.dp, vertical = 3.dp),
    )
}

@Composable
private fun EmptyState() {
    Box(
        Modifier.fillMaxWidth().padding(vertical = 32.dp).testTag("voice_empty"),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            "Kayıtlı ses örneğin yok.",
            color = JarvisTextMuted,
            style = MaterialTheme.typography.bodyLarge,
        )
    }
}

/**
 * Spec §9: the screen is built expecting 3b's actions from the start, so adding them
 * later is filling in an onClick rather than re-laying out the page. Disabled on
 * purpose — an enabled button that does nothing is worse than a visibly pending one.
 */
@Composable
private fun Placeholders() {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        TextButton(
            onClick = {},
            enabled = false,
            modifier = Modifier.testTag("voice_placeholder_record"),
        ) { Text("Ses kaydet (yakında)") }
        TextButton(
            onClick = {},
            enabled = false,
            modifier = Modifier.testTag("voice_placeholder_retrain"),
        ) { Text("Yeniden eğit (yakında)") }
    }
}

@Composable
private fun ErrorBanner(message: String, onDismiss: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp).testTag("voice_error"),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            message,
            color = JarvisError,
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.weight(1f),
        )
        TextButton(onClick = onDismiss) { Text("Tamam", color = JarvisCyan) }
    }
}

/** ISO-8601 timestamps arrive from the server; only the date part is useful here, and
 *  a null ts is normal for pre-3d anchors. Substring beats a date parser for this. */
internal fun shortDate(ts: String?): String = ts?.take(10) ?: "—"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew connectedDebugAndroidTest
```

Expected: PASS — 16 instrumented (3 mevcut + 2 kapı + 11 ekran).

- [ ] **Step 5: Mutation check**

`onSetLabel(sample.id, value)` çağrısını `onSetLabel(sample.id, labelDisplayName(value))` yap. `choosingALabel_reportsTheAsciiWireValue_notTheDisplayName` **KIRMIZI olmalı** — bu test, telin ASCII kalması sözleşmesini tutan tek şey. Geri al.

- [ ] **Step 6: Commit**

```bash
cd /home/user/Projeler/JARVIS
git add android/app/src/main/java/com/jarvis/ui/voice/VoiceProfileScreen.kt android/app/src/androidTest/java/com/jarvis/ui/voice/
git commit -m "feat(voice-ui): screen shell, status card and gallery list behind the gate"
```

---

### Task 8: Ekran B — son söyleyişler + tehlikeli bölge

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/ui/voice/VoiceProfileScreen.kt`
- Modify: `android/app/src/androidTest/java/com/jarvis/ui/voice/VoiceProfileScreenTest.kt`

**Interfaces:**
- Consumes: Task 7'nin `VoiceProfileScreen` imzası (değişmiyor), `HistoryRow`, `Correction`.
- Produces: yeni testTag'ler `voice_history_<id>`, `voice_confirm_<id>`, `voice_reject_<id>`, `voice_danger_open`, `voice_danger_input`, `voice_danger_confirm`.

**Taşıyıcı karar — yazarak onay metni ASCII "SIL":** Türkçe'de büyük/küçük harf dönüşümü yereldir (`I`↔`ı`, `İ`↔`i`). Türkçe yerelli bir cihazda `"SİL".lowercase()` birleşik noktalı bir karakter üretir ve karşılaştırma **sessizce hiç eşleşmez** — kullanıcı doğru yazar, düğme hiç açılmaz. Bu yüzden onay kelimesi **ASCII `SIL`** ve karşılaştırma **hiçbir harf dönüşümü yapmadan** birebir. Ekranda gösterilen metin de tam olarak `SIL`.

- [ ] **Step 1: Write the failing test**

`VoiceProfileScreenTest.kt`'ye ekle (mevcut testleri değiştirme):

```kotlin
    @Test
    fun historyRowsShowScoreAndVerdict() {
        render(ready())
        compose.onNodeWithTag("voice_history_h1").assertIsDisplayed()
        compose.onNodeWithText("0.71").assertIsDisplayed()
        compose.onNodeWithText("tanındı").assertIsDisplayed()
    }

    @Test
    fun historyRowsOfferBothCorrections() {
        var confirmed: String? = null
        var rejected: String? = null
        compose.setContent {
            VoiceProfileScreen(
                state = ready(), onBack = {}, onRetryUnlock = {}, onRetryLoad = {},
                onSetLabel = { _, _ -> }, onDeleteSample = {},
                onConfirm = { confirmed = it }, onReject = { rejected = it },
                onDeleteProfile = {}, onDismissError = {},
            )
        }
        compose.onNodeWithTag("voice_confirm_h1").performClick()
        assertEquals("h1", confirmed)
        compose.onNodeWithTag("voice_reject_h1").performClick()
        assertEquals("h1", rejected)
    }

    /** An already-ruled row must show its verdict and stop offering the same action —
     *  the endpoint is idempotent, but a live button that changes nothing reads broken. */
    @Test
    fun anAlreadyConfirmedRow_showsItsVerdict_andDoesNotOfferConfirmAgain() {
        val ruled = profile.copy(
            history = listOf(
                HistoryRow("h2", "2026-07-25T03:00:00Z", 0.8, true, "buds", "HIGH", "s3",
                    Correction.CONFIRMED),
            ),
        )
        render(
            VoiceProfileUiState(
                gate = GatePhase.UNLOCKED, profile = ruled,
                summary = summarize(ruled.quality, ruled.counts),
            ),
        )
        compose.onNodeWithText("bendim").assertIsDisplayed()
        compose.onNodeWithTag("voice_confirm_h2").assertIsNotEnabled()
        compose.onNodeWithTag("voice_reject_h2").assertIsEnabled()   // reversal stays legal
    }

    /**
     * Profile deletion is irreversible and takes the history with it, so it must not be
     * one stray tap away.
     */
    @Test
    fun profileDeletion_requiresTypingTheConfirmationWord() {
        var deleted = false
        compose.setContent {
            VoiceProfileScreen(
                state = ready(), onBack = {}, onRetryUnlock = {}, onRetryLoad = {},
                onSetLabel = { _, _ -> }, onDeleteSample = {}, onConfirm = {}, onReject = {},
                onDeleteProfile = { deleted = true }, onDismissError = {},
            )
        }
        compose.onNodeWithTag("voice_danger_open").performClick()
        compose.onNodeWithTag("voice_danger_confirm").assertIsNotEnabled()

        compose.onNodeWithTag("voice_danger_input").performTextInput("sil")
        compose.onNodeWithTag("voice_danger_confirm").assertIsNotEnabled()   // wrong case

        compose.onNodeWithTag("voice_danger_input").performTextClearance()
        compose.onNodeWithTag("voice_danger_input").performTextInput(DELETE_CONFIRM_WORD)
        compose.onNodeWithTag("voice_danger_confirm").assertIsEnabled()
        compose.onNodeWithTag("voice_danger_confirm").performClick()
        assertTrue(deleted)
    }

    /**
     * Turkish case folding is locale-dependent (I<->ı, İ<->i): a dotted "SİL" lowercased
     * on a Turkish-locale device produces a combining sequence that silently never
     * matches, so the user types the right word and the button never unlocks. The
     * confirmation word is ASCII and compared with no case transformation at all.
     */
    @Test
    fun theConfirmationWordIsAsciiSoTurkishCaseFoldingCannotBreakIt() {
        assertEquals("SIL", DELETE_CONFIRM_WORD)
        assertTrue(DELETE_CONFIRM_WORD.all { it.code < 128 })
    }
```

Gerekli ek importlar: `androidx.compose.ui.test.assertIsEnabled`, `androidx.compose.ui.test.performTextInput`, `androidx.compose.ui.test.performTextClearance`.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew connectedDebugAndroidTest --tests '*VoiceProfileScreenTest*'
```

Expected: FAIL — `Unresolved reference: DELETE_CONFIRM_WORD`.

- [ ] **Step 3: Write minimal implementation**

`VoiceProfileScreen.kt`'de `Unlocked`'ın `LazyColumn`'unda `item { Placeholders() }`'ın **öncesine** ekle:

```kotlin
                if (profile.history.isNotEmpty()) {
                    item { SectionTitle("Son söyleyişler (${profile.history.size})") }
                    items(profile.history, key = { it.id }) { row ->
                        HistoryRowView(
                            row = row,
                            busy = state.mutatingId != null,
                            onConfirm = onConfirm,
                            onReject = onReject,
                        )
                    }
                }
```

ve `item { Placeholders() }`'ın **sonrasına**:

```kotlin
            item { DangerZone(busy = state.mutatingId != null, onDeleteProfile = onDeleteProfile) }
```

Dosyanın sonuna ekle:

```kotlin
/**
 * ASCII on purpose. Turkish case folding is locale-dependent (I<->ı, İ<->i), so a
 * dotted "SİL" lowercased on a Turkish-locale device yields a combining sequence that
 * silently never matches: the user types exactly what is on screen and the button stays
 * dead. This word is compared with NO case transformation.
 */
const val DELETE_CONFIRM_WORD = "SIL"

@Composable
private fun HistoryRowView(
    row: com.jarvis.data.voice.HistoryRow,
    busy: Boolean,
    onConfirm: (String) -> Unit,
    onReject: (String) -> Unit,
) {
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp))
            .background(JarvisSurface).padding(14.dp).testTag("voice_history_${row.id}"),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                String.format(java.util.Locale.ROOT, "%.2f", row.score),
                style = MaterialTheme.typography.titleSmall,
                color = if (row.verified) JarvisCyan else JarvisError,
            )
            Spacer(Modifier.size(10.dp))
            Text(
                if (row.verified) "tanındı" else "tanınmadı",
                style = MaterialTheme.typography.bodyMedium,
                color = JarvisTextPrimary,
            )
            Spacer(Modifier.weight(1f))
            Text(shortDate(row.ts), style = MaterialTheme.typography.bodySmall, color = JarvisTextMuted)
        }
        row.deviceHint?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = JarvisTextMuted)
        }
        Spacer(Modifier.size(6.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            // The verdict is shown as text so an already-ruled row still reads clearly
            // even though its matching button is now inert.
            when (row.correction) {
                com.jarvis.data.voice.Correction.CONFIRMED ->
                    Text("bendim", color = JarvisCyan, style = MaterialTheme.typography.labelMedium)
                com.jarvis.data.voice.Correction.REJECTED ->
                    Text("ben değildim", color = JarvisError, style = MaterialTheme.typography.labelMedium)
                com.jarvis.data.voice.Correction.NONE -> Unit
            }
            Spacer(Modifier.weight(1f))
            // Reversal in BOTH directions stays legal (spec §6): changing your mind is a
            // legitimate use, and forbidding it would lock the user onto a wrong record.
            TextButton(
                onClick = { onConfirm(row.id) },
                enabled = !busy && row.correction != com.jarvis.data.voice.Correction.CONFIRMED,
                modifier = Modifier.testTag("voice_confirm_${row.id}"),
            ) { Text("Bendim", color = JarvisCyan) }
            TextButton(
                onClick = { onReject(row.id) },
                enabled = !busy && row.correction != com.jarvis.data.voice.Correction.REJECTED,
                modifier = Modifier.testTag("voice_reject_${row.id}"),
            ) { Text("Ben değildim", color = JarvisError) }
        }
    }
}

@Composable
private fun DangerZone(busy: Boolean, onDeleteProfile: () -> Unit) {
    var open by remember { mutableStateOf(false) }
    var typed by remember { mutableStateOf("") }

    Column(Modifier.fillMaxWidth().padding(top = 20.dp, bottom = 32.dp)) {
        Text("Tehlikeli bölge", style = MaterialTheme.typography.titleSmall, color = JarvisError)
        Spacer(Modifier.size(6.dp))
        Text(
            "Profili silmek ses örneklerini VE doğrulama geçmişini birlikte kaldırır. Geri alınamaz.",
            style = MaterialTheme.typography.bodySmall,
            color = JarvisTextMuted,
        )
        Spacer(Modifier.size(10.dp))
        if (!open) {
            TextButton(
                onClick = { open = true },
                enabled = !busy,
                modifier = Modifier.testTag("voice_danger_open"),
            ) { Text("Ses kimliğimi sil", color = JarvisError) }
        } else {
            Text(
                "Onaylamak için $DELETE_CONFIRM_WORD yaz:",
                style = MaterialTheme.typography.bodyMedium,
                color = JarvisTextPrimary,
            )
            Spacer(Modifier.size(6.dp))
            OutlinedTextField(
                value = typed,
                onValueChange = { typed = it },
                singleLine = true,
                modifier = Modifier.fillMaxWidth().testTag("voice_danger_input"),
                colors = TextFieldDefaults.colors(
                    focusedContainerColor = JarvisSurface,
                    unfocusedContainerColor = JarvisSurface,
                    focusedTextColor = JarvisTextPrimary,
                    unfocusedTextColor = JarvisTextPrimary,
                    cursorColor = JarvisCyan,
                ),
            )
            Spacer(Modifier.size(8.dp))
            Row {
                TextButton(onClick = { open = false; typed = "" }) {
                    Text("Vazgeç", color = JarvisTextMuted)
                }
                Spacer(Modifier.weight(1f))
                TextButton(
                    // Exact match, no lowercase()/uppercase() anywhere: see
                    // DELETE_CONFIRM_WORD for why case folding is unsafe here.
                    enabled = !busy && typed.trim() == DELETE_CONFIRM_WORD,
                    onClick = onDeleteProfile,
                    modifier = Modifier.testTag("voice_danger_confirm"),
                ) { Text("Kalıcı olarak sil", color = JarvisError) }
            }
        }
    }
}
```

Ek importlar (`VoiceProfileScreen.kt` başına): `androidx.compose.material3.OutlinedTextField`, `androidx.compose.material3.TextFieldDefaults`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew testDebugUnitTest connectedDebugAndroidTest
```

Expected: PASS — 66 JVM + 21 instrumented.

- [ ] **Step 5: Mutation check**

`typed.trim() == DELETE_CONFIRM_WORD` yerine `typed.trim().equals(DELETE_CONFIRM_WORD, ignoreCase = true)` yaz. `profileDeletion_requiresTypingTheConfirmationWord` **KIRMIZI olmalı** ("sil" yazıldığında düğme etkinleşir). Geri al.

- [ ] **Step 6: Commit**

```bash
cd /home/user/Projeler/JARVIS
git add android/app/src/main/java/com/jarvis/ui/voice/VoiceProfileScreen.kt android/app/src/androidTest/java/com/jarvis/ui/voice/VoiceProfileScreenTest.kt
git commit -m "feat(voice-ui): recent utterances with both corrections, and a typed-confirmation danger zone"
```

---

### Task 9: Bağlama — AppContainer, navigasyon, sohbet girişi, uçtan uca

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/JarvisApp.kt`
- Modify: `android/app/src/main/java/com/jarvis/ui/Nav.kt`
- Modify: `android/app/src/main/java/com/jarvis/ui/chat/ChatScreen.kt`
- Modify: `android/app/src/main/java/com/jarvis/MainActivity.kt`
- Test: `android/app/src/androidTest/java/com/jarvis/ui/voice/VoiceProfileFlowTest.kt`

**Interfaces:**
- Consumes: her önceki task.
- Produces: `AppContainer.voiceProfileRepository`, `AppContainer.biometricGate`; `Nav(..., route, onOpenVoiceProfile, onBack, voiceState, voiceActions)`; `Route` enum.

**Navigasyon kararı:** navigation-compose eklenmiyor. Mevcut `Nav.kt` elle yazılmış bir `when`; iki ekranlık bir geçiş için kütüphane getirmek, projede kurulu "Hilt yok, nav kütüphanesi yok" desenini bir kazanç olmadan bozar (YAGNI).

- [ ] **Step 1: Write the failing test**

`android/app/src/androidTest/java/com/jarvis/ui/voice/VoiceProfileFlowTest.kt`:

```kotlin
package com.jarvis.ui.voice

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.jarvis.data.net.ConfirmResponse
import com.jarvis.data.net.ProfileDeletedResponse
import com.jarvis.data.net.RejectResponse
import com.jarvis.data.net.SampleDeletedResponse
import com.jarvis.data.net.SamplePatchRequest
import com.jarvis.data.net.VoiceApi
import com.jarvis.data.net.VoiceCountsDto
import com.jarvis.data.net.VoiceHistoryDto
import com.jarvis.data.net.VoiceProfileResponse
import com.jarvis.data.net.VoiceSampleDto
import com.jarvis.data.voice.VoiceProfileRepository
import com.jarvis.ui.Route
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Drives the REAL ViewModel + repository against a fake backend, through the real
 * screen — the biometric prompt itself is the only thing stubbed, because a system
 * prompt cannot be driven from an instrumented test on a lockless emulator.
 */
@RunWith(AndroidJUnit4::class)
class VoiceProfileFlowTest {

    @get:Rule val compose = createComposeRule()

    private class FakeVoiceApi : VoiceApi {
        var confirmed: String? = null
        var profileCalls = 0
        override suspend fun profile(): VoiceProfileResponse {
            profileCalls++
            return VoiceProfileResponse(
                counts = VoiceCountsDto(anchors = 1, auto = 1, manual = 0),
                samples = listOf(
                    VoiceSampleDto("s1", "enroll", "2026-07-25T00:00:00Z", "buds", "kulaklik"),
                ),
                history = listOf(VoiceHistoryDto("h1", "2026-07-25T01:00:00Z", 0.71, true, "buds")),
            )
        }
        override suspend fun patchSample(id: String, req: SamplePatchRequest) =
            VoiceSampleDto(id, "auto", label = req.label)
        override suspend fun deleteSample(id: String) = SampleDeletedResponse(id)
        override suspend fun confirm(id: String): ConfirmResponse {
            confirmed = id
            return ConfirmResponse("s-new")
        }
        override suspend fun reject(id: String) = RejectResponse(null)
        override suspend fun deleteProfile() = ProfileDeletedResponse(true)
    }

    @Test
    fun unlockingLoadsTheProfile_andACorrectionReachesTheBackendThenReloads() {
        val api = FakeVoiceApi()
        val vm = VoiceProfileViewModel(VoiceProfileRepository(api))

        compose.setContent {
            val state = vm.state.collectAsState().value
            VoiceProfileScreen(
                state = state,
                onBack = {}, onRetryUnlock = { vm.onUnlocked() }, onRetryLoad = { vm.load() },
                onSetLabel = vm::setLabel, onDeleteSample = vm::deleteSample,
                onConfirm = vm::confirm, onReject = vm::reject,
                onDeleteProfile = vm::deleteProfile, onDismissError = vm::dismissError,
            )
        }

        // Gate closed: nothing fetched, nothing shown.
        compose.onNodeWithTag("voice_locked").assertIsDisplayed()
        assertEquals(0, api.profileCalls)

        vm.onUnlocked()
        compose.waitForIdle()

        compose.onNodeWithText("kayıt").assertIsDisplayed()
        compose.onNodeWithText("Kulaklık").assertIsDisplayed()
        compose.onNodeWithTag("voice_history_h1").assertIsDisplayed()
        assertEquals(1, api.profileCalls)

        compose.onNodeWithTag("voice_confirm_h1").performClick()
        compose.waitForIdle()

        assertEquals("h1", api.confirmed)
        assertEquals("düzeltme sonrası sunucudan yeniden okunmalı", 2, api.profileCalls)
    }

    @Test
    fun theChatScreenOffersAWayIntoTheVoiceProfile() {
        var route = Route.CHAT
        compose.setContent {
            com.jarvis.ui.chat.ChatScreen(
                state = com.jarvis.ui.chat.ChatUiState(
                    authPhase = com.jarvis.ui.chat.AuthPhase.SIGNED_IN,
                ),
                onInput = {}, onSend = {}, onRetry = {},
                onOpenVoiceProfile = { route = Route.VOICE_PROFILE },
            )
        }
        compose.onNodeWithTag("open_voice_profile").performClick()
        assertEquals(Route.VOICE_PROFILE, route)
    }
}
```

Gerekli import: `androidx.compose.runtime.collectAsState`.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew connectedDebugAndroidTest --tests '*VoiceProfileFlowTest*'
```

Expected: FAIL — `Unresolved reference: Route`.

- [ ] **Step 3: Write minimal implementation**

`JarvisApp.kt` — `AppContainer` gövdesini şu hale getir:

```kotlin
class AppContainer(context: Context) {
    private val appContext = context.applicationContext

    val authManager = AuthManager(appContext)

    private val apis = NetworkModule.createApis(
        tokenProvider = { authManager.currentToken() },
        // Runs on OkHttp's background thread, so blocking here is fine.
        tokenRefresher = { runBlocking { authManager.silentSignIn().getOrNull() } },
    )

    val chatRepository = ChatRepository(apis.chat, DataStoreSessionStore(appContext))
    val voiceProfileRepository = VoiceProfileRepository(apis.voice)
    val biometricGate: BiometricGate = AndroidBiometricGate(appContext)
}
```

Importları güncelle: `com.jarvis.data.auth.AndroidBiometricGate`, `com.jarvis.data.auth.BiometricGate`, `com.jarvis.data.voice.VoiceProfileRepository`; artık kullanılmayan `com.jarvis.data.net.JarvisApi` importunu sil.

`ui/Nav.kt` — tamamını değiştir:

```kotlin
package com.jarvis.ui

import androidx.compose.runtime.Composable
import com.jarvis.ui.auth.BootSplash
import com.jarvis.ui.auth.SignInScreen
import com.jarvis.ui.chat.AuthPhase
import com.jarvis.ui.chat.ChatScreen
import com.jarvis.ui.chat.ChatUiState
import com.jarvis.ui.voice.VoiceProfileScreen
import com.jarvis.ui.voice.VoiceProfileUiState

/** Which signed-in screen is showing. Hand-rolled: two destinations do not justify
 *  pulling in navigation-compose, and the project deliberately has no nav library. */
enum class Route { CHAT, VOICE_PROFILE }

/** The voice screen's callbacks, bundled so Nav's signature stays readable. */
class VoiceActions(
    val onRetryUnlock: () -> Unit,
    val onRetryLoad: () -> Unit,
    val onSetLabel: (String, String?) -> Unit,
    val onDeleteSample: (String) -> Unit,
    val onConfirm: (String) -> Unit,
    val onReject: (String) -> Unit,
    val onDeleteProfile: () -> Unit,
    val onDismissError: () -> Unit,
)

/**
 * Top-level switch over the four auth phases, then over [Route] once signed in.
 *
 * It switches on [AuthPhase], not on a `signedIn` boolean, because "not signed in yet"
 * and "not signed in" are different screens: the first launch frame is CHECKING, and
 * showing the sign-in screen then flashed a login prompt at an already-authorized user
 * on every single warm start.
 */
@Composable
fun Nav(
    state: ChatUiState,
    route: Route,
    voiceState: VoiceProfileUiState,
    voiceActions: VoiceActions,
    onSignIn: () -> Unit,
    onInput: (String) -> Unit,
    onSend: () -> Unit,
    onRetry: () -> Unit,
    onOpenVoiceProfile: () -> Unit,
    onBack: () -> Unit,
) {
    when (state.authPhase) {
        AuthPhase.CHECKING -> BootSplash()
        AuthPhase.SIGNED_OUT, AuthPhase.SIGNING_IN -> SignInScreen(
            onSignIn = onSignIn,
            signingIn = state.authPhase == AuthPhase.SIGNING_IN,
            error = state.error,
        )
        AuthPhase.SIGNED_IN -> when (route) {
            Route.CHAT -> ChatScreen(
                state = state,
                onInput = onInput,
                onSend = onSend,
                onRetry = onRetry,
                onOpenVoiceProfile = onOpenVoiceProfile,
            )
            Route.VOICE_PROFILE -> VoiceProfileScreen(
                state = voiceState,
                onBack = onBack,
                onRetryUnlock = voiceActions.onRetryUnlock,
                onRetryLoad = voiceActions.onRetryLoad,
                onSetLabel = voiceActions.onSetLabel,
                onDeleteSample = voiceActions.onDeleteSample,
                onConfirm = voiceActions.onConfirm,
                onReject = voiceActions.onReject,
                onDeleteProfile = voiceActions.onDeleteProfile,
                onDismissError = voiceActions.onDismissError,
            )
        }
    }
}
```

`ui/chat/ChatScreen.kt` — imzaya parametre ekle ve TopBar'a giriş noktası koy:

```kotlin
fun ChatScreen(
    state: ChatUiState,
    onInput: (String) -> Unit,
    onSend: () -> Unit,
    onRetry: () -> Unit,
    onOpenVoiceProfile: () -> Unit = {},
) {
```

`TopBar()` çağrısını `TopBar(onOpenVoiceProfile)` yap ve `TopBar`'ı şu hale getir:

```kotlin
@Composable
private fun TopBar(onOpenVoiceProfile: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Image(
            painter = painterResource(R.drawable.logo_kj),
            contentDescription = null,
            modifier = Modifier.size(30.dp).clip(RoundedCornerShape(8.dp)),
        )
        Spacer(Modifier.size(10.dp))
        Text("Jarvis", style = MaterialTheme.typography.titleLarge, color = JarvisTextPrimary)
        Spacer(Modifier.weight(1f))
        TextButton(
            onClick = onOpenVoiceProfile,
            modifier = Modifier.testTag("open_voice_profile")
                .semantics { contentDescription = "Ses kimliğim" },
        ) {
            Text("Ses kimliğim", color = JarvisCyan, style = MaterialTheme.typography.bodyMedium)
        }
    }
}
```

`MainActivity.kt` — `setContent` gövdesine ekle (mevcut sohbet bağlantıları aynen kalır):

```kotlin
                val voiceVm: VoiceProfileViewModel =
                    viewModel { VoiceProfileViewModel(container.voiceProfileRepository) }
                val voiceState by voiceVm.state.collectAsState()
                var route by remember { mutableStateOf(Route.CHAT) }

                // Whole-profile deletion leaves nothing to show; drop back to the chat.
                LaunchedEffect(voiceState.deleted) {
                    if (voiceState.deleted) route = Route.CHAT
                }

                // The gate runs on ENTERING the screen, not once per process: leaving and
                // coming back must ask again, otherwise the lock protects only the first
                // visit of the session.
                fun openVoiceProfile() {
                    route = Route.VOICE_PROFILE
                    container.biometricGate.prompt(this@MainActivity) { result ->
                        result.fold(
                            onSuccess = { voiceVm.onUnlocked() },
                            onFailure = { voiceVm.onUnlockFailed(it.message) },
                        )
                    }
                }
```

ve `Nav(...)` çağrısını genişlet:

```kotlin
                Nav(
                    state = state,
                    route = route,
                    voiceState = voiceState,
                    voiceActions = VoiceActions(
                        onRetryUnlock = { openVoiceProfile() },
                        onRetryLoad = voiceVm::load,
                        onSetLabel = voiceVm::setLabel,
                        onDeleteSample = voiceVm::deleteSample,
                        onConfirm = voiceVm::confirm,
                        onReject = voiceVm::reject,
                        onDeleteProfile = voiceVm::deleteProfile,
                        onDismissError = voiceVm::dismissError,
                    ),
                    onSignIn = { /* mevcut blok aynen */ },
                    onInput = vm::onInputChange,
                    onSend = vm::send,
                    onRetry = vm::refreshHistory,
                    onOpenVoiceProfile = { openVoiceProfile() },
                    onBack = { route = Route.CHAT },
                )
```

Ek importlar: `androidx.compose.runtime.LaunchedEffect` (zaten var), `androidx.compose.runtime.mutableStateOf`, `androidx.compose.runtime.remember`, `androidx.compose.runtime.setValue`, `com.jarvis.ui.Route`, `com.jarvis.ui.VoiceActions`, `com.jarvis.ui.voice.VoiceProfileViewModel`.

- [ ] **Step 4: Run the whole suite**

```bash
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew testDebugUnitTest connectedDebugAndroidTest
```

Expected: PASS — 66 JVM + 23 instrumented. `EndToEndTest` `ChatScreen`'i doğrudan kuruyorsa yeni parametrenin varsayılanı sayesinde **derlenmeye devam etmeli**; etmiyorsa çağrıyı güncelle ama **iddiaları değiştirme**.

- [ ] **Step 5: Mutation check**

`openVoiceProfile()` içinden `container.biometricGate.prompt(...)` çağrısını silip yerine doğrudan `voiceVm.onUnlocked()` yaz, sonra uygulamayı elle aç: ekran **kapı sormadan** açılıyorsa kapı gerçekten üretimde bağlıydı. Bu, projede üç kez tekrarlanan "guard bir fonksiyon eksik bağlanmış" kusur sınıfının kontrolü. Geri al.

- [ ] **Step 6: Commit**

```bash
cd /home/user/Projeler/JARVIS
git add android/app/src/main/java/com/jarvis/ android/app/src/androidTest/java/com/jarvis/ui/voice/VoiceProfileFlowTest.kt
git commit -m "feat(voice-ui): wire the voice profile screen into the app behind the biometric gate"
```

---

## HITL — saha doğrulaması (kod tamamlandıktan sonra)

Emülatörde Google hesabı ve cihaz kilidi yok; bu adımlar Kadir'in gerçek telefonunda yapılır (Galaxy S23, Tailscale kablosuz adb: `100.64.0.11`).

- [ ] `./gradlew installDebug` ve uygulamayı aç, giriş yap.
- [ ] "Ses kimliğim" → biyometrik/PIN sorulmalı. **İptal et** → "Kilit açılamadı..." + "Tekrar dene" görünmeli, profil içeriği görünmemeli.
- [ ] Tekrar dene → doğrula → durum kartında Kadir'in gerçek 7 çapası ve canlı skorları (0.53–0.70) görünmeli.
- [ ] Bir örneğe etiket ata (`kulaklik`) → sayfa yeniden yüklenmeli, etiket "Kulaklık" olarak kalmalı.
- [ ] Bir çapayı silmeyi dene; **son çapa değilse** silinir. 7 çapa olduğu için bu güvenli.
- [ ] Bir geçmiş satırında "Bendim" → hata yoksa liste yenilenmeli. Aynı satırda düğme artık pasif olmalı.
- [ ] "Ben değildim" ile geri al → tersine düzeltmenin serbest olduğu doğrulanmalı (spec §6).
- [ ] Tehlikeli bölge: `SIL` yazılmadan düğme pasif kalmalı. **Gerçekten silme** — Kadir'in enrollment'ı gidiyor.
- [ ] Ekrandan çık, tekrar gir → kapı **yeniden** sormalı (oturum başına bir kez değil).

## Notlar / bilinen borç

- `TODO(debt)`: 401 yenileme yolu `VoiceApi` için de aynı `TokenAuthenticator`'dan geçiyor; ayrı bir test yok (sohbet tarafında `TokenRefreshTest` kapsıyor, mekanizma paylaşılıyor).
- Geçmiş halka tamponu 50 ile sınırlı, sayfalama yok — spec §6 bunu bilinçli erken karmaşıklık sayıyor. Liste büyürse `LazyColumn` zaten tembel.
- 3b geldiğinde: `voice_placeholder_record` / `voice_placeholder_retrain` düğmelerinin `enabled` ve `onClick`'ini doldurmak yeterli; yerleşim değişmeyecek.

## Self-Review

**1. Spec kapsamı (§9 madde madde):**

| Spec §9 maddesi | Task |
|---|---|
| Sohbetten "Ayarlar → Ses kimliğim" girişi | 9 |
| Açılışta biyometrik kapı | 6 (mekanizma) + 9 (bağlama) |
| Durum kartı: kalite + eğilim + düz Türkçe | 4 (metin) + 7 (kart) |
| Galeri: kaynak rozeti / tarih / cihaz / etiket | 7 |
| Etiket düzenlenebilir | 7 |
| Örnek silinebilir | 7 |
| Son söyleyişler: skor + tanındı/tanınmadı | 8 |
| Her satırda "bendim" / "ben değildim" | 8 |
| Tehlikeli bölge: yazarak onaylı profil silme | 8 |
| 3b yer tutucuları ("Ses kaydet" / "Yeniden eğit") | 7 |
| Yalnızca §6 uçları tüketilir, karar istemcide alınmaz | 2 (metin sunucudan) + 3 (geçiş) + 5 (reload-not-guess) |
| §7: "biyometri yapıldı" başlığı gönderilmez | 6 (arayüz yorumu + hiçbir yerde başlık yok) |

Boşluk yok.

**2. Yer tutucu taraması:** Task 7'deki `@Composable_TestHelper` bilerek işaretlenip nasıl silineceği yazıldı; onun dışında "TBD"/"uygun hata yönetimi ekle"/"yukarıdakiler için test yaz" kalıbı yok. Her adım gerçek kod içeriyor.

**3. Tip tutarlılığı:** `VoiceSampleDto.device_hint` (wire, snake_case) ↔ `VoiceSample.deviceHint` (domain, camelCase) — dönüşüm tek yerde (`toDomain`). `mutatingId` adı Task 5'te tanımlandı, Task 7 ve 8'de aynı adla kullanıldı. `summarize(quality, counts)` parametre sırası Task 4'te tanımlandı, Task 5/7/9'da aynı sırada. `sourceBadge`/`labelDisplayName` Task 4'te tanımlandı, Task 7'de kullanıldı. testTag sözleşmesi Task 7'de listelendi, Task 8 ve 9 birebir aynı adları kullanıyor.
