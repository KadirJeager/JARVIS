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
