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

        // Wear W1 pairing contract (Task 4 review fix): same single gradle.properties
        // literal :app's WatchPairing reads -- see that module's identical
        // buildConfigField and android/gradle.properties#jarvis.wearDeviceTokenMessagePath.
        // .get() throws at configuration time if the property is missing -- loud failure,
        // not a silently-empty placeholder -- and BOTH consumers below read this ONE
        // local val, so the Kotlin constant and the manifest's intent-filter pathPrefix
        // cannot drift into a third hand-typed copy (residual gap from the prior review
        // round: the manifest literal was still unshared).
        val wearDeviceTokenMessagePath = providers.gradleProperty("jarvis.wearDeviceTokenMessagePath").get()
        buildConfigField("String", "WEAR_DEVICE_TOKEN_MESSAGE_PATH", "\"$wearDeviceTokenMessagePath\"")
        manifestPlaceholders["wearDeviceTokenMessagePath"] = wearDeviceTokenMessagePath
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_21
        targetCompatibility = JavaVersion.VERSION_21
    }

    kotlin {
        compilerOptions {
            jvmTarget = JvmTarget.JVM_21
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }
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
