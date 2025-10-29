// ===============================
// CONFIG
// ===============================
const API = "https://api.johka.be";

function el(id) {
  return document.getElementById(id);
}

function qs(sel) {
  return document.querySelector(sel);
}

// ===============================
// REGISTREREN
// ===============================
async function register() {
  const f = qs("#regForm");
  if (!f) return;

  f.addEventListener("submit", async (e) => {
    e.preventDefault();

    const body = {
      username: f.username.value.trim(),
      email: f.email.value.trim(),
      password: f.password.value
    };

    console.log("🚀 Verzenden naar API:", body);

    try {
      const res = await fetch(`${API}/api/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });

      console.log("📩 Response status:", res.status);

      const data = await res.json().catch(() => ({}));
      console.log("📦 Response body:", data);

      el("msg").textContent = res.ok
        ? "✅ Geregistreerd! Je kan nu inloggen."
        : (data.detail || "❌ Fout bij registratie.");

    } catch (err) {
      console.error("💥 Netwerkfout of server niet bereikbaar:", err);
      el("msg").textContent = "Server niet bereikbaar.";
    }
  });
}

// ===============================
// LOGIN
// ===============================
async function login() {
  const f = qs("#loginForm");
  if (!f) return;

  f.addEventListener("submit", async (e) => {
    e.preventDefault();

    const body = {
      username: f.username.value.trim(),
      password: f.password.value
    };

    console.log("🔐 Inlogpoging:", body);

    try {
      const res = await fetch(`${API}/api/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });

      console.log("📩 Response status:", res.status);

      const data = await res.json().catch(() => ({}));
      console.log("📦 Response body:", data);

      if (res.ok && data.access_token) {
        // ✅ Login gelukt → opslaan
        localStorage.setItem("token", data.access_token);
        localStorage.setItem("username", body.username);
        el("msg").textContent = "✅ Ingelogd!";

        // 🧩 Controleer of dit in een popup (iframe) draait
        if (window.top !== window) {
          console.log("📤 In popup: stuur bericht naar hoofdvenster");
          window.top.postMessage({ type: "LOGIN_SUCCESS", username: body.username }, "*");
        } else {
          console.log("🌍 Normale loginpagina: redirect naar index");
          setTimeout(() => (location.href = "/index.html"), 600);
        }

      } else {
        el("msg").textContent = data.detail || "❌ Login fout.";
      }

    } catch (err) {
      console.error("💥 Netwerkfout of server niet bereikbaar:", err);
      el("msg").textContent = "Server niet bereikbaar.";
    }
  });
}

// ===============================
// INIT
// ===============================
register();
login();
