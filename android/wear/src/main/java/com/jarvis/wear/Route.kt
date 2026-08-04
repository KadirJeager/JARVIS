package com.jarvis.wear

/**
 * Kök ekran kararı (spec §5). Üç durum bilinçli (Task 4 review fix): token bilgisi
 * DataStore'dan henüz gelmediyse (ilk okuma ASENKRON bir dosya G/Ç'dir) [Route.Unknown]
 * döner — ASLA [Route.Pair] değil. Aksi halde zaten eşleştirilmiş bir saat bile HER
 * açılışta bir an için yanlış "eşleştir" ekranını görür: "henüz bakmadım" durumu "baktım,
 * token yok" durumuyla karışır. Token yoksa (bakıldı, gerçekten yok) dürüst "Telefondan
 * eşleştir" ekranı; token varsa Sohbet. Task 5 gerçek Chat ekranını [Route.Chat] koluna
 * takar.
 */
enum class Route { Unknown, Pair, Chat }

fun rootRoute(hasToken: Boolean?): Route = when (hasToken) {
    null -> Route.Unknown
    false -> Route.Pair
    true -> Route.Chat
}
