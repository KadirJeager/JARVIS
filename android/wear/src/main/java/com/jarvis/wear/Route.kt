package com.jarvis.wear

/**
 * Kök ekran kararı (spec §5): token yoksa dürüst "Telefondan eşleştir" ekranı — sessiz
 * boş ekran DEĞİL; token varsa Sohbet. Task 5 gerçek Chat ekranını [Route.Chat] koluna
 * takar.
 */
enum class Route { Pair, Chat }

fun rootRoute(hasToken: Boolean): Route = if (hasToken) Route.Chat else Route.Pair
