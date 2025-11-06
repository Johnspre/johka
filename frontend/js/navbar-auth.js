// Same logic as index page
const profileBtn = document.getElementById("profileBtn");
const dropdown = document.getElementById("profileDropdown");
const content = dropdown.querySelector(".dropdown-content");
const token = localStorage.getItem("token");
const username = localStorage.getItem("username");

// Toggle dropdown
profileBtn?.addEventListener("click", () => {
  dropdown.classList.toggle("show");
});

// Determine UI
function tokenExpired() {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return payload.exp * 1000 < Date.now();
  } catch {
    return true;
  }
}

if (!token || tokenExpired()) {
  profileBtn.textContent = "👤 Anonymous ▾";
  content.innerHTML = `
    <a href="/login.html">Log In</a>
    <a href="/register.html">Sign Up</a>
  `;
} else {
  profileBtn.textContent = `👤 ${username} ▾`;
  content.innerHTML = `
    <p><strong>Status:</strong> Basic Member</p>
    <p id="tokenLine"><strong>Tokens:</strong> Laden...</p>
    <a href="/wallet.html" style="color:#ff7a00;font-weight:bold;">Get more ➕</a>
    <hr>
    <a href="/profile.html">My Profile</a>
    <a href="#" onclick="logout();return false;" style="color:#ff7300;">Log Out</a>
  `;

  // 🪙 Saldo ophalen en tonen
  fetch("https://api.johka.be/api/wallet", {
    headers: { Authorization: "Bearer " + token }
  })
    .then(r => r.json())
    .then(data => {
      const balance = data.balance ?? 0;
      const tokenLine = document.getElementById("tokenLine");
      if (tokenLine) tokenLine.innerHTML = `<strong>Tokens:</strong> ${balance}`;
    })
    .catch(err => {
      console.warn("Kon wallet niet ophalen:", err);
    });
}


function logout() {
  localStorage.removeItem("username");
  localStorage.removeItem("token");
  location.reload();
}

// Broadcast button logic
document.getElementById("broadcastBtn")
  ?.addEventListener("click", () => {
    if (!token || tokenExpired()) {
      location.href = "/login.html";
    } else {
      location.href = "/room.html";
    }
  });

  setInterval(() => {
  if (!tokenExpired()) {
    fetch("https://api.johka.be/api/wallet", {
      headers: { Authorization: "Bearer " + token }
    })
      .then(r => r.json())
      .then(data => {
        const tokenLine = document.getElementById("tokenLine");
        if (tokenLine) tokenLine.innerHTML = `<strong>Tokens:</strong> ${data.balance ?? 0}`;
      });
  }
}, 60000);
