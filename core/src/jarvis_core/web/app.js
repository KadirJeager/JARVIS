// JARVIS PWA: sign-in, conversations, model settings and memory.
// Conversation state is read live from Firestore (security rules scope it to
// the signed-in user); every change goes through the authenticated API.
// All server and model text is rendered with textContent, never as HTML.
import { initializeApp } from 'https://www.gstatic.com/firebasejs/12.19.0/firebase-app.js';
import {
  getAuth, GoogleAuthProvider, onAuthStateChanged, signInWithPopup, signOut,
} from 'https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js';
import {
  collection, doc, getFirestore, limit, onSnapshot, orderBy, query,
} from 'https://www.gstatic.com/firebasejs/12.19.0/firebase-firestore.js';

const $ = (id) => document.getElementById(id);
const TURN_STATUS = {
  queued: 'Sırada', running: 'İşleniyor', completed: 'Tamamlandı',
  failed: 'Başarısız', reconciling: 'Doğrulama bekliyor', cancelled: 'İptal edildi',
};
// Plain-language hints for known error types and provider reason codes.
const ERROR_HINTS = [
  ['SettingsMissing', 'Model ayarı yok; Beyin sekmesinden kaydet.'],
  ['VALIDATION_REQUIRED', 'Model sağlayıcısı hesabın doğrulanmasını istiyor; aynı hesapla sağlayıcının resmî uygulamasından doğrulamayı tamamla.'],
  ['RESOURCE_EXHAUSTED', 'Model kotası dolu; kota yenilenince tekrar dene.'],
  ['UsageLimitExceeded', 'Tur sınırına ulaşıldı; Beyin sekmesinden sınırları artırabilirsin.'],
  ['unresolved_tool_effects', 'Bir aracın işlemi yapıp yapmadığı bilinmiyor; tekrar denemeden önce sonucu kontrol et.'],
];

function errorText(message) {
  const technical = `${message.type}: ${message.detail}`;
  const hint = ERROR_HINTS.find(([code]) => technical.includes(code))?.[1];
  return hint ? `${hint}\n(${technical})` : `Hata (${technical})`;
}

const config = await (await fetch('/config.json')).json();
const app = initializeApp(config.firebase);
const auth = getAuth(app);
const db = getFirestore(app);

let user = null;
let conversationId = null;
let unsubscribers = [];
let settingsVersion = null;
let memoryFile = null;

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: {
      Authorization: `Bearer ${await user.getIdToken()}`,
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? data);
    throw Object.assign(new Error(detail), { status: response.status });
  }
  return data;
}

function setStatus(id, text, isError = false) {
  const element = $(id);
  element.textContent = text;
  element.classList.toggle('error', isError);
}

function stopListening() {
  unsubscribers.forEach((unsubscribe) => unsubscribe());
  unsubscribers = [];
}

// --- Authentication --------------------------------------------------------

$('sign-in').addEventListener('click', async () => {
  setStatus('sign-in-error', '');
  try {
    await signInWithPopup(auth, new GoogleAuthProvider());
  } catch (error) {
    setStatus('sign-in-error', `Giriş yapılamadı: ${error.code ?? error.message}`, true);
  }
});
$('sign-out').addEventListener('click', () => signOut(auth));

onAuthStateChanged(auth, (current) => {
  stopListening();
  user = current;
  $('signed-out').hidden = Boolean(user);
  $('signed-in').hidden = !user;
  if (user) {
    listenConversations();
    showView('chat');
  }
});

// --- Navigation ------------------------------------------------------------

function showView(name) {
  for (const button of document.querySelectorAll('nav button')) {
    button.toggleAttribute('aria-current', button.dataset.view === name);
    if (button.dataset.view === name) button.setAttribute('aria-current', 'page');
  }
  for (const view of ['chat', 'brain', 'memory']) $(`view-${view}`).hidden = view !== name;
  if (name === 'brain') loadSettings();
  if (name === 'memory') loadMemory();
}
for (const button of document.querySelectorAll('nav button')) {
  button.addEventListener('click', () => showView(button.dataset.view));
}

// --- Conversations ---------------------------------------------------------

function listenConversations() {
  const conversations = query(
    collection(db, 'users', user.uid, 'conversations'), orderBy('updated_at', 'desc'), limit(50),
  );
  unsubscribers.push(onSnapshot(conversations, (snapshot) => {
    const list = $('conversations');
    list.replaceChildren();
    for (const entry of snapshot.docs) {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      const { title, updated_at: updatedAt } = entry.data();
      button.textContent = title || updatedAt?.toDate().toLocaleString() || entry.id;
      if (title && updatedAt) button.title = updatedAt.toDate().toLocaleString();
      button.toggleAttribute('aria-current', entry.id === conversationId);
      button.addEventListener('click', () => openConversation(entry.id));
      item.append(button);
      list.append(item);
    }
  }, (error) => setStatus('chat-status', `Konuşmalar okunamadı: ${error.code}`, true)));
}

let messagesUnsubscribe = null;
let turnUnsubscribe = null;

function openConversation(id) {
  conversationId = id;
  messagesUnsubscribe?.();
  turnUnsubscribe?.();
  setStatus('chat-status', '');
  const messages = query(
    collection(db, 'users', user.uid, 'conversations', id, 'messages'), orderBy('position'),
  );
  messagesUnsubscribe = onSnapshot(messages, (snapshot) => {
    const list = $('messages');
    list.replaceChildren();
    for (const entry of snapshot.docs) {
      const message = entry.data();
      const item = document.createElement('li');
      item.className = message.role;
      item.textContent = message.role === 'error' ? errorText(message) : message.text;
      list.append(item);
    }
    list.lastElementChild?.scrollIntoView({ block: 'end' });
  }, (error) => setStatus('chat-status', `Mesajlar okunamadı: ${error.code}`, true));
  unsubscribers.push(() => messagesUnsubscribe?.(), () => turnUnsubscribe?.());
}

function watchTurn(turnId) {
  turnUnsubscribe?.();
  turnUnsubscribe = onSnapshot(doc(db, 'turns', turnId), (snapshot) => {
    const turn = snapshot.data();
    if (turn) setStatus('chat-status', TURN_STATUS[turn.status] ?? turn.status, turn.status === 'failed');
  });
}

$('new-conversation').addEventListener('click', () => {
  openConversation(crypto.randomUUID());
  $('message').focus();
});

$('composer').addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = $('message').value.trim();
  if (!text) return;
  if (!conversationId) openConversation(crypto.randomUUID());
  const button = event.submitter;
  button.disabled = true;
  try {
    const result = await api('POST', `/v1/conversations/${conversationId}/messages`, {
      client_message_id: crypto.randomUUID(), text,
    });
    $('message').value = '';
    watchTurn(result.turn_id);
    if (result.dispatch === 'deferred') setStatus('chat-status', 'Sıraya alındı; iletim birkaç dakika içinde yeniden denenecek.');
  } catch (error) {
    setStatus('chat-status', `Gönderilemedi: ${error.message}`, true);
  } finally {
    button.disabled = false;
  }
});

// --- Brain (model settings) ------------------------------------------------

const settingsForm = $('settings');

function endpointFromForm() {
  const form = settingsForm.elements;
  return {
    protocol: form.protocol.value,
    base_url: form.base_url.value.trim() || null,
    api_key_ref: form.api_key_ref.value.trim() || null,
  };
}

async function loadSettings() {
  setStatus('settings-status', '');
  const form = settingsForm.elements;
  try {
    const stored = await api('GET', '/v1/settings');
    const { settings } = stored;
    settingsVersion = stored.version;
    form.protocol.value = settings.model.protocol;
    form.base_url.value = settings.model.base_url ?? '';
    form.api_key_ref.value = settings.model.api_key_ref ?? '';
    form.model.value = settings.model.model;
    form.strategy.value = settings.tool_selection.strategy;
    form.jev_api_key_ref.value = settings.tool_selection.api_key_ref ?? '';
    form.instructions.value = settings.instructions;
    form.request_limit.value = settings.limits.request_limit;
    form.tool_calls_limit.value = settings.limits.tool_calls_limit;
    setStatus('settings-status', `Kayıtlı sürüm ${stored.version}`);
  } catch (error) {
    if (error.status !== 404) {
      setStatus('settings-status', `Ayarlar okunamadı: ${error.message}`, true);
      return;
    }
    settingsVersion = null;
    form.request_limit.value ||= 12;
    form.tool_calls_limit.value ||= 16;
    setStatus('settings-status', 'Henüz ayar kaydedilmedi.');
  }
}

$('test-connection').addEventListener('click', async () => {
  setStatus('connection-status', 'Deneniyor…');
  try {
    const result = await api('POST', '/v1/connections/test', endpointFromForm());
    if (!result.ok) {
      const status = result.http_status ? `, HTTP ${result.http_status}` : '';
      setStatus('connection-status', `Bağlanılamadı (${result.error}${status})`, true);
      return;
    }
    const options = $('model-options');
    options.replaceChildren(...result.models.map((id) => Object.assign(document.createElement('option'), { value: id })));
    setStatus('connection-status', `Bağlantı çalışıyor: ${result.models.length} model listelendi.`);
  } catch (error) {
    setStatus('connection-status', `Denenemedi: ${error.message}`, true);
  }
});

settingsForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = settingsForm.elements;
  const body = {
    expected_version: settingsVersion,
    settings: {
      instructions: form.instructions.value,
      model: { ...endpointFromForm(), model: form.model.value.trim() },
      tool_selection: {
        strategy: form.strategy.value,
        api_key_ref: form.jev_api_key_ref.value.trim() || null,
      },
      limits: {
        request_limit: Number(form.request_limit.value),
        tool_calls_limit: Number(form.tool_calls_limit.value),
      },
    },
  };
  try {
    const stored = await api('PUT', '/v1/settings', body);
    settingsVersion = stored.version;
    setStatus('settings-status', `Kaydedildi: sürüm ${stored.version}. Sonraki mesajdan itibaren kullanılır.`);
  } catch (error) {
    const hint = error.status === 409 ? ' Ayarlar başka yerde değişti; sayfayı yenileyip tekrar dene.' : '';
    setStatus('settings-status', `Kaydedilemedi: ${error.message}.${hint}`, true);
  }
});

// --- Memory (vault) --------------------------------------------------------

async function loadMemory() {
  $('memory-editor').hidden = true;
  const list = $('memory-files');
  list.replaceChildren();
  try {
    const { paths } = await api('GET', '/v1/memory');
    if (!paths.length) {
      list.append(Object.assign(document.createElement('li'), { textContent: 'Henüz hafıza kaydı yok.' }));
    }
    for (const path of paths) {
      const button = Object.assign(document.createElement('button'), { type: 'button', textContent: path });
      button.addEventListener('click', () => openMemory(path));
      const item = document.createElement('li');
      item.append(button);
      list.append(item);
    }
  } catch (error) {
    list.append(Object.assign(document.createElement('li'), { textContent: `Okunamadı: ${error.message}` }));
  }
}

const memoryUrl = (path) => `/v1/memory/${path.split('/').map(encodeURIComponent).join('/')}`;

async function openMemory(path) {
  setStatus('memory-status', '');
  try {
    memoryFile = await api('GET', memoryUrl(path));
    $('memory-path').textContent = path;
    $('memory-content').value = memoryFile.content;
    $('memory-editor').hidden = false;
    if (memoryFile.truncated) setStatus('memory-status', 'Dosya çok büyük; yalnız başı gösteriliyor, düzenleme kapalı.', true);
    $('memory-editor').querySelector('button[type=submit]').disabled = memoryFile.truncated;
  } catch (error) {
    setStatus('memory-status', `Açılamadı: ${error.message}`, true);
  }
}

$('memory-editor').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const result = await api('PUT', memoryUrl(memoryFile.path), {
      content: $('memory-content').value, expected_version: memoryFile.version,
    });
    memoryFile.version = result.version;
    setStatus('memory-status', 'Düzeltme kaydedildi.');
  } catch (error) {
    const hint = error.status === 409 ? ' Kayıt bu arada değişti; yeniden aç.' : '';
    setStatus('memory-status', `Kaydedilemedi: ${error.message}.${hint}`, true);
  }
});

$('memory-forget').addEventListener('click', async () => {
  if (!confirm(`"${memoryFile.path}" kalıcı olarak unutulsun mu?`)) return;
  try {
    await api('DELETE', `${memoryUrl(memoryFile.path)}?expected_version=${encodeURIComponent(memoryFile.version)}`);
    await loadMemory();
  } catch (error) {
    setStatus('memory-status', `Silinemedi: ${error.message}`, true);
  }
});

if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js');
