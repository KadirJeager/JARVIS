package com.jarvis.wear.data

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.map

/** In-memory [Prefs] stand-in for JVM tests — the real DataStore-backed impl needs an
 * Android [android.content.Context] and lands in Task 4's Application wiring. */
class FakePrefs : Prefs {
    private val map = LinkedHashMap<String, String>()
    private val flow = MutableStateFlow<Map<String, String>>(emptyMap())
    override suspend fun get(key: String) = map[key]
    override suspend fun put(key: String, value: String) { map[key] = value; flow.value = map.toMap() }
    override suspend fun remove(key: String) { map.remove(key); flow.value = map.toMap() }
    override fun watch(key: String) = flow.map { it[key] }
    fun dump() = map.toString()
}
