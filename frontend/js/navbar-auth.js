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
    <p><strong>Tokens:</strong> 0</p>
    <hr>
    <a href="/profile.html">My Profile</a>
    <a href="#" onclick="logout();return false;" style="color:#ff7300;">Log Out</a>
  `;
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
