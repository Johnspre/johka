import {
  Room,
  RoomEvent,
  Track,
  DataPacket_Kind,
} from "https://cdn.jsdelivr.net/npm/livekit-client@2.0.10/+esm";

const API = "https://api.johka.be/api";
const params = new URLSearchParams(window.location.search);
const requestedRoom = params.get("room");
const requestedUsername = params.get("u");

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

const encoder = new TextEncoder();
const decoder = new TextDecoder();

let normalizedSlug = null;
let activeSlug = null;
let lkRoom = null;
let isLeaving = false;
let reconnectTimer = null;
let reconnectAttempts = 0;
let reconnecting = false;
let viewingActive = false;

function setStatus({ live = false, text = "Offline" }) {
  statusBadge.classList.toggle("offline", !live);
  statusText.textContent = text;
}

function showOverlay(message) {
  if (!overlay) return;
  overlay.textContent = message;
  overlay.style.display = "flex";
}

function hideOverlay() {
  if (!overlay) return;
  overlay.style.display = "none";
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
  if (chatInput) chatInput.disabled = !enabled;
  if (chatSendBtn) chatSendBtn.disabled = !enabled;
  updateChatNotice(
    enabled
      ? "Je bent verbonden met de chat."
      : "Chat is tijdelijk niet beschikbaar tijdens het verbinden."
  );
}

function attachVideoTrack(videoTrack) {
  const stream = new MediaStream([videoTrack.mediaStreamTrack]);
  remoteVideo.srcObject = stream;
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
    creatorNameEl.textContent = requestedUsername || info.owner;
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
  const token = localStorage.getItem("token");
  if (!token) {
    showOverlay("🔒 Inloggen vereist om streams te bekijken");
    updateChatNotice("Log in om de chat te gebruiken.");
    setTimeout(() => {
      window.location.href = "/login.html";
    }, 1500);
    throw new Error("not authenticated");
  }

  const res = await fetch(`${API}/livekit-token`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ room_slug: normalizedSlug }),
  });

  const data = await res.json();
  if (!res.ok) {
    const detail = data?.detail || res.statusText;
    showOverlay(`❌ ${detail}`);
    updateChatNotice("Chat niet beschikbaar: token ophalen mislukt.");
    throw new Error(detail);
  }
  return data;
}

function setupRoomEvents(room) {
  room
    .on(RoomEvent.TrackSubscribed, (_track, publication) => {
      if (publication.kind === Track.Kind.Video && publication.videoTrack) {
        attachVideoTrack(publication.videoTrack);
        hideOverlay();
        logMessage("🎬 Stream gestart.");
      } else if (publication.kind === Track.Kind.Audio && publication.audioTrack) {
        publication.audioTrack.attach(new Audio());
      }
    })
    .on(RoomEvent.TrackUnsubscribed, (_track, publication) => {
      if (publication.kind === Track.Kind.Video) {
        remoteVideo.srcObject = null;
        showOverlay("⏳ Wachten op video...");
      }
    })
    .on(RoomEvent.ParticipantConnected, updateViewerCount)
    .on(RoomEvent.ParticipantDisconnected, updateViewerCount)
    .on(RoomEvent.DataReceived, handleIncomingData)
    .on(RoomEvent.Disconnected, () => handleRoomDisconnected(room));
}

function updateViewerCount() {
  if (!lkRoom) return;
  const total = Math.max(lkRoom.participants.size + 1, 0);
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
      logMessage(`💸 ${sender} tipte ${amount} tokens!`, "tip");
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
  const room = new Room({ adaptiveStream: true, dynacast: true });
  lkRoom = room;
  setChatEnabled(false);
  showOverlay("Verbonden – wachten op video...");
  setupRoomEvents(room);
  await room.connect(tokenData.url, tokenData.token, { autoSubscribe: true });
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



async function start() {
    setChatEnabled(false);
    logMessage("💬 Verbinden met stream...", "system");
  try {
    const info = await loadRoomInfo();
    if (!info.is_live) {
      showOverlay("🔴 Deze creator is momenteel offline");
      logMessage("ℹ️ Deze stream is momenteel offline.", "system");
      return;
    }
    await connectToLiveKit();
  } catch (err) {
    console.error(err);
    logMessage(`❌ ${err.message}`, "system");
  }
}
if (chatForm) {
  chatForm.addEventListener("submit", (event) => {
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