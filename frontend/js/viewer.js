import {
  Room,
  RoomEvent,
  Track,
  DataPacket_Kind,
} from "https://cdn.jsdelivr.net/npm/livekit-client@2.0.10/+esm";

import { EmojiButton } from "https://cdn.jsdelivr.net/npm/@joeattardi/emoji-button@4.6.2/dist/index.min.js";


const API = "https://api.johka.be/api";
const params = new URLSearchParams(window.location.search);
const requestedRoom = params.get("room");
const requestedUsername = params.get("u");


function setStreamerName(name) {
  if (!name) return;
  const normalized = String(name).trim();
  if (!normalized) return;
  window.streamerName = normalized;
  window.streamerNameLower = normalized.toLowerCase();
}

window.streamerName = null;
window.streamerNameLower = null;
setStreamerName(requestedUsername);


const overlay = document.getElementById("playerOverlay");
const statusBadge = document.getElementById("statusBadge");
const statusText = document.getElementById("statusText");
const creatorNameEl = document.getElementById("creatorName");
const viewerCountEl = document.getElementById("viewerCount");
const leaveBtn = document.getElementById("leaveBtn");
const remoteVideo = document.getElementById("remoteVideo");
const messageLog = document.getElementById("messageLog");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const chatSendBtn = document.getElementById("chatSendBtn");
const chatNotice = document.getElementById("chatNotice");
const emojiBtn = document.getElementById("viewerEmojiBtn");
const donateBtn = document.getElementById("donateBtn");
const donateInput = document.getElementById("donateInput");

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function getViewerRole(entry) {
  const myName = localStorage.getItem("username");

  return {
    isSelf: entry.name === myName,

    // Als metadata vertelt dat de user moderator is:
    isModerator: window.TEST_FORCE_MODERATOR ? true : (entry.isModerator || false),

    isStreamer: false
  };
}


// TESTMODE: MAAK JEZELF MODERATOR IN DE VIEWER
window.TEST_FORCE_MODERATOR = true;


let normalizedSlug = null;
let activeSlug = null;
let lkRoom = null;
let isLeaving = false;
let reconnectTimer = null;
let reconnectAttempts = 0;
let reconnecting = false;
let viewingActive = false;
let chatAllowed = Boolean(localStorage.getItem("token"));
let guestNoticeShown = false;
let manualPlayHandler = null;
let audioUnlocked = false;
let audioUnlockHandler = null;
let currentVideoTrack = null;

if (remoteVideo) {
  remoteVideo.setAttribute("playsinline", "true");
  remoteVideo.setAttribute("webkit-playsinline", "true");
  remoteVideo.setAttribute("autoplay", "true");
  remoteVideo.playsInline = true;
  remoteVideo.autoplay = true;
  if ("disablePictureInPicture" in remoteVideo) {
    remoteVideo.disablePictureInPicture = true;
  }
  if ("disableRemotePlayback" in remoteVideo) {
    remoteVideo.disableRemotePlayback = true;
  }
  // iOS Safari blokkeert vaak audio tot er een user gesture heeft plaatsgevonden.
  // We starten daarom gedempt en proberen het geluid later vrij te geven.
  remoteVideo.muted = true;
  remoteVideo.defaultMuted = true;
  remoteVideo.setAttribute("muted", "true");
}

function clearAudioUnlockHandler() {
  if (!audioUnlockHandler) return;
  ["click", "touchend"].forEach((evt) => {
    document.removeEventListener(evt, audioUnlockHandler, true);
  });
  audioUnlockHandler = null;
}

async function unlockAudio() {
  if (!lkRoom) return;
  if (!audioUnlocked) {
    try {
      await lkRoom.startAudio();
      audioUnlocked = true;
    } catch (err) {
      console.warn("Audio unlock failed", err);
    }
  }
  if (remoteVideo) {
    remoteVideo.muted = false;
    remoteVideo.removeAttribute("muted");
  }
  if (audioUnlocked) {
    clearAudioUnlockHandler();
  }
}

function registerAudioUnlockHandler() {
  if (audioUnlocked || audioUnlockHandler) return;
  audioUnlockHandler = () => {
    unlockAudio();
  };
  ["click", "touchend"].forEach((evt) => {
    document.addEventListener(evt, audioUnlockHandler, true);
  });
}

function setStatus({ live = false, text = "Offline" }) {
  statusBadge.classList.toggle("offline", !live);
  statusText.textContent = text;
}

function showOverlay(message) {
  if (!overlay) return;
  overlay.textContent = message;
  overlay.style.display = "flex";
  overlay.classList.remove("interactive");
}

function hideOverlay() {
  if (!overlay) return;
  overlay.style.display = "none";
}

function clearManualPlaybackHandler() {
  if (!manualPlayHandler) return;
  if (overlay) {
    overlay.removeEventListener("click", manualPlayHandler);
    overlay.removeEventListener("touchend", manualPlayHandler);
    overlay.classList.remove("interactive");
  }
  if (remoteVideo) {
    remoteVideo.removeEventListener("click", manualPlayHandler);
    remoteVideo.removeEventListener("touchend", manualPlayHandler);
  }
  manualPlayHandler = null;
}

function requestManualPlayback() {
  if (!overlay || !remoteVideo) return;
  showOverlay("▶️ Tik om de stream te starten");
  clearManualPlaybackHandler();
  overlay.classList.add("interactive");
  registerAudioUnlockHandler();
  manualPlayHandler = () => {
    const retry = remoteVideo.play();
    if (retry && typeof retry.then === "function") {
      retry
        .then(() => {
          hideOverlay();
          clearManualPlaybackHandler();
          unlockAudio()
        })
        .catch((err) => {
          console.warn("Handmatige afspeelpoging mislukt", err);
          setTimeout(() => requestManualPlayback(), 0);
        });
    } else {
      hideOverlay();
      clearManualPlaybackHandler();
      unlockAudio();
    }
  };
  overlay.addEventListener("click", manualPlayHandler, { once: true });
  overlay.addEventListener("touchend", manualPlayHandler, { once: true });
  remoteVideo.addEventListener("click", manualPlayHandler, { once: true });
  remoteVideo.addEventListener("touchend", manualPlayHandler, { once: true });
}

function ensureVideoPlayback() {
  if (!remoteVideo) return;
  registerAudioUnlockHandler();
  const attempt = remoteVideo.play();
  if (attempt && typeof attempt.then === "function") {
    attempt
      .then(() => {
        hideOverlay();
        clearManualPlaybackHandler();
        unlockAudio();
      })
      .catch((err) => {
        console.warn("Autoplay geblokkeerd", err);
        requestManualPlayback();
      });
  } else {
    hideOverlay();
    unlockAudio();
  }
}

function updateChatNotice(message) {
  if (chatNotice) {
    chatNotice.textContent = message;
  }
}

function logMessage(text, type = "system") {
  if (!messageLog) return;
  const item = document.createElement("div");
  item.className = `chat-message ${type}`;
  item.textContent = text;
  messageLog.appendChild(item);
  messageLog.scrollTop = messageLog.scrollHeight;
}

function setChatEnabled(enabled) {
 const canInteract = enabled && chatAllowed;
  if (chatInput) chatInput.disabled = !canInteract;
  if (chatSendBtn) chatSendBtn.disabled = !canInteract;
  if (emojiBtn) emojiBtn.disabled = !canInteract;
  if (!chatAllowed) {
    updateChatNotice("Log in om deel te nemen aan de chat.");
  } else if (canInteract) {
    updateChatNotice("Je bent verbonden met de chat.");
  } else {
    updateChatNotice("Chat is tijdelijk niet beschikbaar tijdens het verbinden.");
  }
}

function attachVideoTrack(videoTrack) {
  if (!remoteVideo || !videoTrack) return;
  if (currentVideoTrack && currentVideoTrack !== videoTrack) {
    try {
      currentVideoTrack.detach(remoteVideo);
    } catch (err) {
      console.warn("Kon vorige videotrack niet loskoppelen", err);
    }
  }
  currentVideoTrack = videoTrack;
  videoTrack.attach(remoteVideo);
  if (remoteVideo.readyState >= 2) {
    ensureVideoPlayback();
  } else {
    const handleMetadata = () => {
      remoteVideo.removeEventListener("loadedmetadata", handleMetadata);
      ensureVideoPlayback();
    };
    remoteVideo.addEventListener("loadedmetadata", handleMetadata);
  }
}

async function notifyView(endpoint) {
  if (!activeSlug) return;
  if (endpoint === "start" && viewingActive) return;
  if (endpoint === "end" && !viewingActive) return;

  try {
    const res = await fetch(`${API}/room/view-${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ room: activeSlug }),
    });
    if (!res.ok) throw new Error(res.statusText);
    if (endpoint === "start") {
      viewingActive = true;
    }
  } catch (err) {
    console.warn(`Viewer ${endpoint} notify failed`, err);
    } finally {
    if (endpoint === "end") {
      viewingActive = false;
    }
  }
}

async function loadRoomInfo() {
  if (!requestedRoom) {
    showOverlay("❌ Geen kamer opgegeven");
    throw new Error("No room parameter");
  }

  try {
    const res = await fetch(`${API}/rooms/${encodeURIComponent(requestedRoom)}`);
    if (!res.ok) {
      throw new Error("Room niet gevonden");
    }
    const info = await res.json();
    normalizedSlug = info.slug;
    activeSlug = info.live_slug;
    const creatorDisplayName = requestedUsername || info.owner;
    creatorNameEl.textContent = creatorDisplayName;
    const previousStreamer = window.streamerNameLower;
    setStreamerName(window.streamerName || creatorDisplayName);
    if (
      window.streamerNameLower &&
      window.streamerNameLower !== previousStreamer &&
      typeof refreshPMInbox === "function"
    ) {
      refreshPMInbox();
    }
    if (info.is_live) {
      setStatus({ live: true, text: `Live — ${info.viewers || 0} kijkers` });
      viewerCountEl.textContent = `👁 ${info.viewers || 0} kijkers`;
    } else {
      setStatus({ live: false, text: "Offline" });
      viewerCountEl.textContent = "👁 0 kijkers";
    }
    return info;
  } catch (err) {
    showOverlay(`❌ ${err.message}`);
    throw err;
  }
}

// ======================================================
// 🔽 Creator bio ophalen
// ======================================================
async function loadCreatorBio() {
  try {
    const username = requestedUsername || ""; // komt uit URL ?u=
    if (!username) {
      console.warn("Geen username in URL, kan bio niet laden");
      return;
    }

    const res = await fetch(`${API}/creator/${encodeURIComponent(username)}`);
    if (!res.ok) throw new Error("Kan creator info niet laden");
    const data = await res.json();

    const bioEl = document.getElementById("creatorBio");
    const nameEl = document.getElementById("creatorName");
    if (bioEl) bioEl.textContent = data.bio || "Geen bio beschikbaar.";
    if (nameEl) nameEl.textContent = data.username || username;
  } catch (err) {
    console.error("❌ Fout bij ophalen bio:", err);
    const bioEl = document.getElementById("creatorBio");
    if (bioEl) bioEl.textContent = "❌ Bio kon niet geladen worden.";
  }
}


async function fetchRoomInfoQuiet() {
  if (!requestedRoom) throw new Error("No room parameter");
  const res = await fetch(`${API}/rooms/${encodeURIComponent(requestedRoom)}`);
  if (!res.ok) throw new Error("Room niet gevonden");
  const info = await res.json();
  normalizedSlug = info.slug;
  activeSlug = info.live_slug;
  return info;
}

async function obtainToken() {
  const storedToken = localStorage.getItem("token");
  const headers = { "Content-Type": "application/json" };
  if (storedToken) {
    headers.Authorization = `Bearer ${storedToken}`;
  }

  const res = await fetch(`${API}/livekit-token`, {
    method: "POST",
    headers,
    body: JSON.stringify({ room_slug: normalizedSlug }),
  });

  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    data = null;
  }

  if (res.status === 401 && storedToken) {
    localStorage.removeItem("token");
    localStorage.removeItem("username");
    chatAllowed = false;
    logMessage("ℹ️ Je sessie is verlopen. Je bent nu als gast verbonden.", "system");
    return obtainToken();
  }

  if (!res.ok) {
    const detail = data?.detail || res.statusText;
    showOverlay(`❌ ${detail}`);
    updateChatNotice("Chat niet beschikbaar: token ophalen mislukt.");
    throw new Error(detail);
  }

  chatAllowed = Boolean(data?.can_chat);
  if (chatAllowed) {
    guestNoticeShown = false;
  } else {
    updateChatNotice("Log in om deel te nemen aan de chat.");
    if (!guestNoticeShown) {
      logMessage("🔒 Log in om deel te nemen aan de chat.", "system");
      guestNoticeShown = true;
    }
  }
  return data;
}

function setupRoomEvents(room) {
  room
    .on(RoomEvent.TrackSubscribed, (track, publication) => {
      if (track.kind === Track.Kind.Video) {
        attachVideoTrack(track);
        logMessage("🎬 Stream gestart.");
      } else if (track.kind === Track.Kind.Audio) {
        const audioEl = track.attach();
        if (audioEl instanceof HTMLMediaElement) {
          audioEl.autoplay = true;
          audioEl.muted = false;
        }
      }
    })
    .on(RoomEvent.TrackUnsubscribed, (track, publication) => {
      if (publication.kind === Track.Kind.Video) {
        if (currentVideoTrack) {
          try {
            currentVideoTrack.detach(remoteVideo);
          } catch (err) {
            console.warn("Kon videotrack niet loskoppelen", err);
          }
          currentVideoTrack = null;
        }
        remoteVideo.srcObject = null;
        clearManualPlaybackHandler();
        showOverlay("⏳ Wachten op video...");
        } else if (publication.kind === Track.Kind.Audio) {
        try {
          track?.detach();
        } catch (err) {
          console.warn("Kon audiotrack niet loskoppelen", err);
        }
      }
    })
    .on(RoomEvent.ParticipantConnected, updateViewerCount)
    .on(RoomEvent.ParticipantDisconnected, updateViewerCount)
    .on(RoomEvent.ParticipantConnected, () => { try { syncRosterFromRoom(); } catch {} })
    .on(RoomEvent.ParticipantDisconnected, () => { try { syncRosterFromRoom(); } catch {} })
    .on(RoomEvent.DataReceived, handleIncomingData)
    .on(RoomEvent.Disconnected, () => handleRoomDisconnected(room));
}

function updateViewerCount() {
  if (!lkRoom) return;
  const participantCollection = lkRoom.participants;
  const remoteCount =
    typeof participantCollection?.size === "number"
      ? participantCollection.size
      : Array.isArray(participantCollection)
        ? participantCollection.length
        : 0;
  const total = Math.max(remoteCount + 1, 0);
  viewerCountEl.textContent = `👁 ${total} kijkers`;
  if (lkRoom.state === "connected") {
    setStatus({ live: true, text: `Live — ${total} kijkers` });
  }
}

function handleIncomingData(payload, participant) {
  try {
    const decoded = JSON.parse(decoder.decode(payload));
    if (decoded.type === "chat" && decoded.text) {
      const sender = decoded.sender || participant?.identity || "onbekend";
      logMessage(`${sender}: ${decoded.text}`, "remote");
    } else if (decoded.type === "tip") {
      const sender = decoded.sender || participant?.identity || "onbekend";
      const amount = decoded.amount ?? "?";
      const recipient = decoded.recipient || "de creator";
      logMessage(`💸 ${sender} tipte ${amount} tokens aan ${recipient}!`, "tip");
    }
  } catch (err) {
    console.warn("Data parse fout", err);
  }
}

function handleRoomDisconnected(roomInstance) {
  if (lkRoom !== roomInstance) return;
  if (isLeaving) return;
  remoteVideo.srcObject = null;
  setChatEnabled(false);
  setStatus({ live: false, text: "Verbinding verbroken" });
  showOverlay("Verbinding verbroken – opnieuw verbinden...");
  logMessage("⚠️ Verbinding verbroken. Probeer opnieuw te verbinden...", "system");
  notifyView("end");
  clearManualPlaybackHandler();
  scheduleReconnect();
}

function clearReconnectTimer() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function scheduleReconnect() {
  if (isLeaving) return;
  clearReconnectTimer();
  const delay = Math.min(3000 * Math.max(reconnectAttempts, 1), 15000);
  reconnectTimer = setTimeout(attemptReconnect, delay);
}

async function attemptReconnect() {
  if (isLeaving || reconnecting) return;
  reconnecting = true;
  reconnectAttempts += 1;
  const label = `poging ${reconnectAttempts}`;
  showOverlay(`Opnieuw verbinden… (${label})`);
  logMessage(`🔄 Opnieuw verbinden (${label})...`, "system");
  try {
    const info = await fetchRoomInfoQuiet().catch(() => null);
    if (info && !info.is_live) {
      setStatus({ live: false, text: "Offline" });
      viewerCountEl.textContent = "👁 0 kijkers";
      showOverlay("🔴 Deze creator is momenteel offline");
      logMessage("ℹ️ De stream is offline gegaan.", "system");
      viewingActive = false;
      return;
    }
    const tokenData = await obtainToken();
    await joinRoomWithToken(tokenData);
    logMessage("✅ Verbinding hersteld.", "system");
  } catch (err) {
    console.error("Reconnect failed", err);
    logMessage(`❌ Verbinding mislukt: ${err.message}`, "system");
    if (reconnectAttempts < 5) {
      scheduleReconnect();
    } else {
      showOverlay("❌ Verbinding kon niet hersteld worden");
    }
  } finally {
    reconnecting = false;
  }
}

async function joinRoomWithToken(tokenData) {
  clearReconnectTimer();
  reconnectAttempts = 0;
  if (lkRoom) {
    lkRoom.removeAllListeners();
    try {
      await lkRoom.disconnect();
    } catch (_) {}
  }
  audioUnlocked = false;
  clearAudioUnlockHandler();
  const room = new Room({ adaptiveStream: true, dynacast: true });
  lkRoom = room;
  window.lkRoom = room;
  setChatEnabled(false);
  showOverlay("Verbonden – wachten op video...");
  setupRoomEvents(room);
  await room.connect(tokenData.url, tokenData.token, { autoSubscribe: true });
  registerAudioUnlockHandler();
  updateViewerCount();
  setChatEnabled(true);
  await notifyView("start");
}

async function connectToLiveKit() {
  const tokenData = await obtainToken();
  await joinRoomWithToken(tokenData);
}

async function sendChatMessage() {
  if (!chatInput) return;
  const text = chatInput.value.trim();
  if (!text) return;
  if (!chatAllowed) {
    window.location.href = "/login.html";
    return;
  }
  if (!lkRoom || lkRoom.state !== "connected") {
    logMessage("❌ Niet verbonden met de chat.", "system");
    return;
  }
  chatInput.value = "";
  const payload = {
    type: "chat",
    text,
    sender:
      lkRoom.localParticipant?.identity ||
      localStorage.getItem("username") ||
      "ik",
  };
  try {
    await lkRoom.localParticipant.publishData(
      encoder.encode(JSON.stringify(payload)),
      DataPacket_Kind.RELIABLE
    );
    logMessage(`Jij: ${text}`, "self");
  } catch (err) {
    logMessage(`❌ Bericht niet verzonden (${err.message})`, "system");
  }
}

async function submitTip(amount, toUser) {
  const token = localStorage.getItem("token");
  if (!token) {
    alert("Je moet ingelogd zijn om te tippen.");
    return;
  }

  try {
    const res = await fetch("https://api.johka.be/api/tip", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ to_username: toUser, amount }),
    });

    if (!res.ok) {
      const errText = await res.text().catch(() => "");
      throw new Error(errText || res.status);
    }

    logMessage(`💸 Jij tipte ${amount} tokens aan ${toUser}!`, "tip");

    if (lkRoom?.state === "connected" && lkRoom.localParticipant) {
      const payload = {
        type: "tip",
        amount,
        recipient: toUser,
        sender:
          lkRoom.localParticipant.identity ||
          localStorage.getItem("username") ||
          "ik",
      };

      try {
        await lkRoom.localParticipant.publishData(
          encoder.encode(JSON.stringify(payload)),
          DataPacket_Kind.RELIABLE,
        );
      } catch (err) {
        console.warn("Tip broadcast mislukt", err);
      }
    } else {
      console.warn("Tip niet verzonden via LiveKit – niet verbonden");
    }

    alert(`💸 ${amount} tokens getipt!`);
  } catch (err) {
    console.error("Tip mislukt", err);
    alert(`❌ Tip mislukt: ${err.message}`);
  }
}

function setupDonationControls() {
  if (!donateBtn || !donateInput) return;

  if (!donateInput.value) {
    donateInput.value = "10";
  }

  donateBtn.addEventListener("click", async (event) => {
    event.preventDefault();
    const raw = (donateInput.value || "").toString().trim();
    const amount = Number(raw);

    if (!Number.isFinite(amount) || amount < 1) {
      alert("Vul een geldig aantal tokens in (bv. 5).");
      donateInput.focus();
      return;
    }

    const params = new URLSearchParams(window.location.search);
    const toUser = params.get("u");

    if (!toUser) {
      alert("Geen streamer gevonden om te tippen.");
      return;
    }

    await submitTip(amount, toUser);
  });
}
// ✅ Toon naam & tokens in topbar als user ingelogd is
function initViewerTopbar() {
  try {
    const username = localStorage.getItem("username");
    const tokens = localStorage.getItem("tokens") || 0;

    if (username) {
      const n = document.getElementById("viewerName");
      const t = document.getElementById("viewerTokens");
      if (n) n.textContent = `👤 ${username}`;
      if (t) t.textContent = `${tokens} Tokens`;
    }
  } catch (e) {
    console.warn("Topbar init failed", e);
  }
}

// 🔥 Run direct
initViewerTopbar();
setupDonationControls();



async function start() {
  setChatEnabled(false);
  logMessage("💬 Verbinden met stream...", "system");

  try {
    const info = await loadRoomInfo();
    await loadCreatorBio();

    // 1️⃣ Room niet live
    if (!info.is_live) {
      showOverlay("🔴 Deze creator is momenteel offline");
      logMessage("ℹ️ Deze stream is momenteel offline.", "system");
      return;
    }

    // 2️⃣ Privé-room controle
    if (info.is_private) {
      const mode = info.access_mode;
      let key = null;

      if (mode === "password" || mode === "invite") {
        key = prompt("🔒 Deze room is privé.\nVoer het wachtwoord of invite-code in:");
        if (!key) return alert("Geen toegangscode ingevoerd.");
      }

      if (mode === "token") {
        const price = info.token_price || 0;
        const confirmPay = confirm(`💰 Deze privéroom kost ${price} tokens.\nWil je binnenkomen?`);
        if (!confirmPay) return logMessage("❌ Toegang geannuleerd door gebruiker.", "system");
      }

      // 3️⃣ backend-aanroep voor toegang
      const token = localStorage.getItem("token");
      if (!token) return alert("Je bent niet ingelogd.");

      const res = await fetch("https://api.johka.be/api/room/join-private", {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + token,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ slug: info.slug, key }),
      });

      if (res.status === 402) {
        alert("❌ Niet genoeg tokens om deze room te betreden.");
        return;
      }

      if (!res.ok) {
        const errText = await res.text().catch(() => "");
        throw new Error(errText || res.statusText);
      }

      const joinData = await res.json();
      console.log("✅ Privé-toegang bevestigd:", joinData);
    }

    // 4️⃣ Verbinden met LiveKit
    await connectToLiveKit();
    logMessage("✅ Verbonden met LiveKit.", "system");
  } catch (err) {
    console.error(err);
    logMessage(`❌ ${err.message}`, "system");
    showOverlay(`❌ ${err.message}`);
  }
}

if (chatForm) {
  chatForm.addEventListener("submit", (event) => {
    event.preventDefault();
    sendChatMessage();
  });
}

if (chatSendBtn) {
  chatSendBtn.addEventListener("click", (event) => {
    event.preventDefault();
    sendChatMessage();
  });
}


chatInput?.addEventListener("keydown", (event) => {
  event.stopPropagation();
});

leaveBtn.addEventListener("click", async () => {
  isLeaving = true;
  clearReconnectTimer();
  setChatEnabled(false);
  await notifyView("end");
  if (lkRoom) {
    try {
      await lkRoom.disconnect();
    } catch (_) {}
  }
  window.location.href = "/index.html";
});

window.addEventListener("beforeunload", () => {
  if (!isLeaving && viewingActive && activeSlug) {
    const blob = new Blob(
      [JSON.stringify({ room: activeSlug })],
      { type: "application/json" }
    );
    navigator.sendBeacon?.(`${API}/room/view-end`, blob);
  }
});

start();


// === EMOJI PICKER VOOR VIEWER CHAT ===
(function setupViewerEmojiPicker () {
  const btn = document.getElementById("viewerEmojiBtn");
  const input = document.getElementById("chatInput");
  if (!btn || !input) return;

  const picker = new EmojiButton({
    position: "top-start",
    theme: "light",
    zIndex: 9999
  });

  btn.addEventListener("click", () => picker.togglePicker(btn));

  picker.on("emoji", (selection) => {
    const emoji = selection.emoji || selection;
    const start = input.selectionStart ?? input.value.length;
    const end   = input.selectionEnd   ?? input.value.length;
    input.setRangeText(emoji, start, end, "end");
    input.focus();
  });
})();

// ======================================================
// 🔽 JOHKA LIVE – Creator bio laden
// ======================================================
(async function loadCreatorBio() {
  try {
    const params = new URLSearchParams(window.location.search);
    const username = params.get("u");
    if (!username) {
      console.warn("Geen 'u' parameter in URL, bio niet geladen");
      return;
    }

    const url = `https://api.johka.be/api/creator/${encodeURIComponent(username)}`;
    console.log("🌐 Ophalen creator info van:", url);

    const res = await fetch(url);
    console.log("📡 Status:", res.status);
    if (!res.ok) throw new Error("Kon creator info niet laden");
    const data = await res.json();
    console.log("📦 Creator data:", data);

    const nameEl = document.getElementById("creatorName");
    const bioEl = document.getElementById("creatorBio");
    if (nameEl) nameEl.textContent = data.username || username;
    if (bioEl) bioEl.textContent = data.bio || "Geen bio beschikbaar.";
  } catch (err) {
    console.error("❌ Fout bij laden bio:", err);
    const bioEl = document.getElementById("creatorBio");
    if (bioEl) bioEl.textContent = "❌ Kon bio niet laden.";
  }
})();


// ======================================================
// 🔽 Creatorpage embedded laden (geïsoleerd via iframe)
// ======================================================
function loadEmbeddedCreatorPage() {
  const params = new URLSearchParams(window.location.search);
  const username = params.get("u");
  if (!username) {
    console.warn("Geen 'u' parameter – embed wordt overgeslagen");
    return;
  }

  const box = document.getElementById("creatorContent");
  if (!box) return;

  // iframe gebruiken zodat de CSS van creatorpage.html je viewer niet breekt
  const iframe = document.createElement("iframe");
  iframe.src = `/creatorpage.html?u=${encodeURIComponent(username)}&embed=true`;
  iframe.style.width = "100%";
  iframe.style.border = "none";
  iframe.style.display = "block";
  iframe.style.minHeight = "900px"; // pas aan naar smaak

  box.innerHTML = "";
  box.appendChild(iframe);
}

// === Gebruikerslijst via LiveKit (voor Users-tab) ===
const rosterByKey = new Map();

function getParticipantKey(participant) {
  if (!participant) return null;
  if (participant.sid) return participant.sid;
  if (participant.identity) return `id:${participant.identity}`;
  if (!participant.__viewerRosterKey) {
    participant.__viewerRosterKey = `tmp-${Math.random().toString(36).slice(2, 10)}`;
  }
  return participant.__viewerRosterKey;
}
  function trackParticipant(participant) {
  const key = getParticipantKey(participant);
  if (!participant || !key) return;

  let meta = {};
  try {
    meta = JSON.parse(participant.metadata || "{}");
    const isModerator = Boolean(meta.is_mod);

  } catch (err) {
    console.warn("Kon participant metadata niet parsen:", err);
  }

  const gender =
    typeof meta.gender === "string" && meta.gender.trim()
      ? meta.gender.toLowerCase()
      : "unknown";
  const displayName =
    typeof meta.display_name === "string" && meta.display_name.trim()
      ? meta.display_name.trim()
      : typeof meta.username === "string" && meta.username.trim()
        ? meta.username.trim()
        : typeof participant.identity === "string" && participant.identity.trim()
          ? participant.identity.split("#")[0].split("_")[0].trim()
          : typeof participant.name === "string"
            ? participant.name.trim()
            : "viewer";

  const entry = {
    key,
    name: displayName || "viewer",
    gender,
    isAnonymous: Boolean(meta.isAnonymous),
    isLocal: Boolean(participant.isLocal ?? participant === window.lkRoom?.localParticipant),
    isModerator: Boolean(meta.is_mod)
  };
  rosterByKey.set(key, entry);
  return entry;
}


function updateViewerList() {
  const list = document.getElementById("userList");
  if (!list) return;
  list.innerHTML = "";
  
  const entries = Array.from(rosterByKey.values());
  entries.sort((a, b) => {
    if (a.isLocal !== b.isLocal) return a.isLocal ? -1 : 1;
    return a.name.localeCompare(b.name, undefined, { sensitivity: "accent", numeric: true });
  });

  const ICON_MAP = {
    anonymous: "/img/anon.png",
    female: "/img/female-icon.png",
    male: "/img/male-icon.png",
    trans: "/img/trans.png",
    default: "/img/anon.png",
  };

  const getIcon = (entry) => {
    if (entry.isAnonymous) return ICON_MAP.anonymous;
    if (entry.gender === "female") return ICON_MAP.female;
    if (entry.gender === "male") return ICON_MAP.male;
    if (entry.gender === "trans") return ICON_MAP.trans;
    return ICON_MAP.default;
  };

  entries.forEach((entry) => {
    const icon = getIcon(entry);

    const label = entry.isLocal ? `${entry.name} (jij)` : entry.name;
    const li = document.createElement("li");
    li.innerHTML = `
  <img src="${icon}" width="22" height="22" style="margin-right:6px; vertical-align:middle;">
  <span class="username" data-name="${label}">${label}</span>
`;
  
const nameSpan = li.querySelector(".username");
nameSpan.addEventListener("click", (event) => {
  const rawName = event.target.dataset.name || "";
  const cleanName = rawName.replace(" (jij)", "");

  const key = [...rosterByKey.values()].find(e => e.name === cleanName)?.key;
  const entry = rosterByKey.get(key);

  const role = entry ? getViewerRole(entry) : {};

  openViewerUserPopup(
    cleanName,
    event.clientX + 8,
    event.clientY + 8,
    role
  );
});


    list.appendChild(li);
  });
}

function syncRosterFromRoom() {
  rosterByKey.clear();
  const r = window.lkRoom;
  if (!r) {
    updateViewerList();
    return;
  }
  if (r.localParticipant) trackParticipant(r.localParticipant);
  const remoteMap =
    r.remoteParticipants instanceof Map
      ? r.remoteParticipants
      : r.participants instanceof Map
        ? r.participants
        : null;

  if (remoteMap) {
    remoteMap.forEach((participant) => trackParticipant(participant));
  } else if (Array.isArray(r.participants)) {
    r.participants.forEach((participant) => trackParticipant(participant));
  }

  updateViewerList();
}

// 👉 start zodra de room klaar is
if (window.lkRoom) {
  window.lkRoom.on(RoomEvent.ParticipantConnected, syncRosterFromRoom);
  window.lkRoom.on(RoomEvent.ParticipantDisconnected, syncRosterFromRoom);
  window.lkRoom.on(RoomEvent.ParticipantMetadataChanged, syncRosterFromRoom);
  syncRosterFromRoom();
} else {
  const wait = setInterval(() => {
    if (window.lkRoom) {
      clearInterval(wait);
      syncRosterFromRoom();
      window.lkRoom.on(RoomEvent.ParticipantConnected, syncRosterFromRoom);
      window.lkRoom.on(RoomEvent.ParticipantDisconnected, syncRosterFromRoom);
      window.lkRoom.on(RoomEvent.ParticipantMetadataChanged, syncRosterFromRoom);
    }
  }, 500);
}
// laat andere scripts de roster manueel verversen
window.refreshViewerRoster = function refreshViewerRoster() {
  try {
    syncRosterFromRoom();
  } catch (err) {
    console.warn("Kon viewer roster niet verversen", err);
  }
};

// =====================================================
// 📨 PM Logica voor viewer DM berichten
// =====================================================

// PM UI elementen
const pmTabBtn = document.querySelector("[data-tab='pm']");
const pmInbox = document.getElementById("pmInbox");
const pmLockedBox = document.getElementById("pmLockedBox");
const pmInput = document.getElementById("pmInput");
const pmSendBtn = document.getElementById("pmSendBtn");

// Standaard LOCKED (CB-gedrag)
let pmUnlocked = false;

function updatePMLockUI() {
  if (pmUnlocked) {
    pmLockedBox.style.display = "none";
    pmInput.disabled = false;
    pmSendBtn.disabled = false;
    pmInput.placeholder = "Typ een privébericht…";
  } else {
    pmLockedBox.style.display = "block";
    pmInput.disabled = true;
    pmSendBtn.disabled = true;
    pmInput.placeholder = "Privéberichten geblokkeerd";
  }
}

updatePMLockUI();

// Bericht toevoegen
function addPMMessage(from, msg) {
    const div = document.createElement("div");
    div.style.margin = "5px 0";
    div.innerHTML = `<b>${from}:</b> ${msg}`;
    pmInbox.appendChild(div);
    pmInbox.scrollTop = pmInbox.scrollHeight;
}

// Inbox + unlock check
function refreshPMInbox() {
    fetch("https://api.johka.be/api/dm/inbox", {
        headers: { "Authorization": "Bearer " + localStorage.getItem("token") }
    })
    .then(r => r.json())
    .then(list => {
        pmInbox.innerHTML = "";

        list.reverse().forEach(m => addPMMessage(m.from, m.message));

        const targetName =
          window.streamerNameLower ||
          (window.streamerName ? window.streamerName.toLowerCase() : null);

        const prevUnlocked = pmUnlocked;
        pmUnlocked = Boolean(
          targetName &&
            list.some(
              (m) =>
                typeof m.from === "string" &&
                m.from.trim().toLowerCase() === targetName,
            ),
        );

        if (pmUnlocked !== prevUnlocked) {
          updatePMLockUI();
        }
    });
}

// Iedere 5 sec inbox verversen
setInterval(refreshPMInbox, 5000);


// PM versturen
pmSendBtn.addEventListener("click", () => {
    const text = pmInput.value.trim();
    if (!text || !pmUnlocked) return;

    fetch("https://api.johka.be/api/dm/send", {
        method: "POST",
        headers: {
            "Authorization": "Bearer " + localStorage.getItem("token"),
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            to_username: window.streamerName,
            message: text
        })
    })
    .then(() => {
        addPMMessage("jij", text);
        pmInput.value = "";
    });
});

// aanroepen zodra de pagina klaar is
window.addEventListener("DOMContentLoaded", loadEmbeddedCreatorPage);

// =========================================
// 👤 SIMPLE USER POPUP (alleen viewer-side)
// =========================================
const viewerUserPopupEl = document.getElementById("userPopup");

function closeViewerUserPopup() {
  if (!viewerUserPopupEl) return;
  viewerUserPopupEl.classList.add("hidden");
}

function openViewerUserPopup(username, x, y, role = {}) {
  const { isSelf = false, isModerator = false } = role;

  let html = `
    <div class="user-popup-header">${username}</div>

    <div class="user-popup-section">
      <button data-act="pm">💌 Send private message</button>
      <button data-act="dm">💬 Direct message</button>
      <button data-act="mention">@ Mention</button>
      <button data-act="ignore">🚫 Ignore</button>
    </div>
  `;

  // Extra moderator-functies
  if (isModerator && !isSelf) {
    html += `
      <div class="user-popup-section">
        <b>Moderator tools</b><br><br>
        <button data-act="kick">👢 Kick user</button>
        <button data-act="timeout5">⏱ Timeout 5m</button>
        <button data-act="timeout60">⏱ Timeout 1h</button>
        <button data-act="timeout24">⏱ Timeout 24h</button>
      </div>
    `;
  }

  viewerUserPopupEl.innerHTML = html;
  viewerUserPopupEl.style.left = x + "px";
  viewerUserPopupEl.style.top = y + "px";
  viewerUserPopupEl.classList.remove("hidden");

  viewerUserPopupEl.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", () => {
      const act = btn.dataset.act;
      handleViewerPopupAction(act, username);
    });
  });
}


// Acties voorlopig alleen als demo (alerts)
// Later koppelen we dit aan je echte PM/DM systemen
function handleViewerPopupAction(action, username) {
  switch (action) {
    case "pm":
      alert("PM naar: " + username + " (later echte functie)");
      break;
    case "dm":
      alert("DM naar: " + username + " (later echte functie)");
      break;
    case "mention": {
      const input = document.getElementById("chatInput");
      if (input) {
        input.value = "@" + username + " ";
        input.focus();
      }
      break;
    }
    case "ignore":
      alert("Gebruiker genegeerd (UI): " + username);
      break;

        case "kick":
      alert("Moderator: Kick user → backend later");
      break;

    case "timeout5":
      alert("Moderator timeout 5 min → backend later");
      break;

    case "timeout60":
      alert("Moderator timeout 1 uur → backend later");
      break;

    case "timeout24":
      alert("Moderator timeout 24 uur → backend later");
      break;

  }
     
  closeViewerUserPopup();
}

// Klik buiten popup = sluiten
document.addEventListener("click", (event) => {
  if (!viewerUserPopupEl) return;
  const isUsername = event.target.classList?.contains("username");
  if (!isUsername && !viewerUserPopupEl.contains(event.target)) {
    closeViewerUserPopup();
  }
});

// Escape = sluiten
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeViewerUserPopup();
  }
});
