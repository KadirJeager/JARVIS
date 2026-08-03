buildscript {
    repositories {
        google()
        mavenCentral()
    }
    dependencies {
        // Raise AGP 9.x built-in Kotlin (KGP) to match the Compose compiler plugin version,
        // since the Compose compiler version must equal the Kotlin compiler version.
        classpath(libs.kotlin.gradle.plugin)
    }
}

plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.google.services) apply false
}
