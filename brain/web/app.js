let idToken = null;
const sessionId = "web-" + new Date().toISOString().slice(0, 10);

window.onload = () => {
  google.accounts.id.initialize({
    client_id: window.JARVIS_CLIENT_ID,
    callback: (resp) => {
      idToken = resp.credential;
      document.getElementById("signin").hidden = true;
      document.getElementById("log").hidden = false;
      document.getElementById("f").hidden = false;
    },
  });
  google.accounts.id.renderButton(document.getElementById("gbtn"), { theme: "filled_black" });
};

function addMsg(text, cls) {
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.textContent = text;
  const log = document.getElementById("log");
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const inp = document.getElementById("inp");
  const message = inp.value.trim();
  if (!message) return;
  inp.value = "";
  addMsg(message, "me");
  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + idToken },
      body: JSON.stringify({ session_id: sessionId, message }),
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    addMsg((await r.json()).reply, "jarvis");
  } catch (err) {
    addMsg("Hata: " + err.message + " (oturum süresi dolduysa sayfayı yenile)", "jarvis");
  }
});
