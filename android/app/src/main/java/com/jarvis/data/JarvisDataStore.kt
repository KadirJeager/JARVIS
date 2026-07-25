package com.jarvis.data

import android.content.Context
import androidx.datastore.preferences.preferencesDataStore

/**
 * The app's ONE Preferences DataStore.
 *
 * Declared here, once, on purpose: `preferencesDataStore(name = ...)` may be created only
 * a single time per file name in a process. A second delegate with the same name — say a
 * new store class copying the line from an existing one — throws at runtime ("There are
 * multiple DataStores active for the same file"), and only when that second store is
 * first touched, which is long after the code looks fine.
 */
internal val Context.jarvisDataStore by preferencesDataStore(name = "jarvis")
