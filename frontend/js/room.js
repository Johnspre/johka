// ======= JOHKA LIVE v9.6 – Snapshots + Chat + Tips + Live UI (patched) =======
import {
  Room,
  RoomEvent,
  Track,
  DataPacket_Kind,
  createLocalTracks,
} from "https://cdn.jsdelivr.net/npm/livekit-client@2.0.10/+esm";

import { EmojiButton } from "https://cdn.jsdelivr.net/npm/@joeattardi/emoji-button@4.6.2/dist/index.min.js";



const API = "https://api.johka.be/api";
const DEFAULT_LIVEKIT_URL = "wss://live.johka.be";

const el = (id) => document.getElementById(id);
let room;
let localTracks = [];
const rosterByKey = new Map();
let reconnectAttempts = 0;
let reconnectTimer = null;
let micEnabled = true;
let liveIndicator = null;
let miniPreview = null;
let snapshotTimer = null;
let tipTotal = 0;
let isLeaving = false; // ⬅️ nieuw: intentional leave flag
let isLive = false;
let isStarting = false;
let livekitUrl = DEFAULT_LIVEKIT_URL;
let livekitRoomName = null;
let liveRoomSlug = null;
let authToken = null;
let storedUsername = null;
let defaultRoomSubject = "My room";
let currentRoomSubject = defaultRoomSubject;

window.roomDefaultSubject = defaultRoomSubject;
window.roomCurrentSubject = currentRoomSubject;

function getParticipantKey(participant) {
  if (!participant) return null;
  if (participant.sid) return participant.sid;
  if (participant.identity) return `id:${participant.identity}`;
  if (!participant.__johkaRosterKey) {
    participant.__johkaRosterKey = `tmp-${Math.random().toString(36).slice(2, 10)}`;
  }
  return participant.__johkaRosterKey;
}

function parseParticipantMetadata(raw) {
  let meta = {};
  if (typeof raw === "string" && raw.trim()) {
    try {
      meta = JSON.parse(raw);
    } catch (err) {
      console.warn("Kon participant metadata niet parsen:", err);
    }
  }
  return meta;
}

function buildRosterEntry(participant) {
  if (!participant) return null;
  const meta = parseParticipantMetadata(participant.metadata);
  const identity = typeof participant.identity === "string" ? participant.identity.trim() : "";
  const username =
    typeof meta.display_name === "string" && meta.display_name.trim()
      ? meta.display_name.trim()
      : typeof meta.username === "string"
        ? meta.username.trim()
        : "";
  const gender = typeof meta.gender === "string" ? meta.gender.toLowerCase() : "unknown";
  const name = username || identity || "guest";
  const isLocal = Boolean(participant.isLocal ?? participant === room?.localParticipant);
  return {
    key: getParticipantKey(participant),
    name,
    isAnonymous: Boolean(meta.isAnonymous),
    gender,
    isLocal,
  };
}

function trackParticipant(participant) {
  const entry = buildRosterEntry(participant);
  if (!entry || !entry.key) return entry;
  rosterByKey.set(entry.key, entry);
  return entry;
}

function removeParticipantFromRoster(participant) {
  const key = getParticipantKey(participant);
  if (!key) return;
  rosterByKey.delete(key);
}

function getParticipantDisplayName(participant) {
  const key = getParticipantKey(participant);
  const stored = key ? rosterByKey.get(key) : null;
  if (stored?.name) return stored.name;
  const meta = parseParticipantMetadata(participant?.metadata);
  const identity = typeof participant?.identity === "string" ? participant.identity.trim() : "";
  const username =
    typeof meta.display_name === "string" && meta.display_name.trim()
      ? meta.display_name.trim()
      : typeof meta.username === "string"
        ? meta.username.trim()
        : "";
  return username || identity || "viewer";
}

function updateViewerList() {
  const entries = Array.from(rosterByKey.values());
  entries.sort((a, b) => {
    if (a.isLocal !== b.isLocal) return a.isLocal ? -1 : 1;
    return a.name.localeCompare(b.name, undefined, { sensitivity: "accent", numeric: true });
  });

  const viewCountEl = el("viewCount");
  if (viewCountEl) {
    const viewerCount = entries.filter((entry) => !entry.isLocal).length;
    viewCountEl.textContent = viewerCount.toString();
  }

  const userList = el("userList");
  if (userList) {
    userList.innerHTML = "";
    entries.forEach((entry) => {
       // Kies juiste icoon afhankelijk van gender of anonimiteit
      let icon = "/img/anon.png";

      if (entry.isAnonymous) {
        icon = "/img/anon.png";
      } else if (entry.gender === "female") {
        icon = "/img/female.png";
      } else if (entry.gender === "male") {
        icon = "/img/male.png";
      } else if (entry.gender === "trans") {
        icon = "/img/trans.png";
      }

      // Tekstlabel (voeg “(jij)” toe bij lokale gebruiker)
      const label = entry.isLocal ? `${entry.name} (jij)` : entry.name;
      // Bouw list-item
      const li = document.createElement("li");
      li.innerHTML = `
        <img src="${icon}" width="22" height="22" style="margin-right:6px; vertical-align:middle;">
        <span class="username">${label}</span>
      `;
      userList.appendChild(li);
    });
  }

// 👥 Tellen van anonieme kijkers
  const anon = el("anonCount");
  if (anon) {
    const anonCount = entries.filter(
      (entry) => entry.isAnonymous && !entry.isLocal
    ).length;
    anon.textContent = `+${anonCount} anonymous users`;
  }
}


function syncRosterFromRoom() {
  rosterByKey.clear();
  const activeRoom = window.lkRoom || room;
  if (!activeRoom) {
    updateViewerList();
    return;
  }
  if (activeRoom.localParticipant) {
    trackParticipant(activeRoom.localParticipant);
  }
  const remoteMap =
    activeRoom.remoteParticipants instanceof Map
      ? activeRoom.remoteParticipants
      : activeRoom.participants instanceof Map
        ? activeRoom.participants
        : null;

  if (remoteMap) {
    remoteMap.forEach((participant) => {
      trackParticipant(participant);
    });
  } else if (Array.isArray(activeRoom.participants)) {
    activeRoom.participants.forEach((participant) => {
      trackParticipant(participant);
    });
  }
  updateViewerList();
}


// Globale LiveKit room referentie (kan door andere blokken gebruikt worden)
window.lkRoom = window.lkRoom || null;

// ========== STATUSBALK ==========
function updateStatusBar(text, color = "#ccc") {
  const status = el("status");
  if (status) {
    status.textContent = text;
    status.style.color = color;
  }
}

// ========== LIVE OVERLAY ==========
function showLiveIndicator(active = false) {
  if (!liveIndicator) {
    liveIndicator = document.createElement("div");
    liveIndicator.id = "liveIndicator";
    liveIndicator.textContent = "● LIVE";
    Object.assign(liveIndicator.style, {
      position: "absolute",
      top: "10px",
      left: "10px",
      padding: "5px 10px",
      background: "rgba(229,57,53,0.9)",
      color: "#fff",
      fontWeight: "bold",
      fontFamily: "Segoe UI, sans-serif",
      borderRadius: "4px",
      fontSize: "14px",
      zIndex: "10",
      letterSpacing: "1px",
      display: "none",
      animation: "blink 1.5s infinite",
    });
    const style = document.createElement("style");
    style.textContent = `
      @keyframes blink {0%,100%{opacity:1;}50%{opacity:.6;}}
      @keyframes pulse {
        0%{box-shadow:0 0 0 0 rgba(229,57,53,.6);}
        70%{box-shadow:0 0 0 10px rgba(229,57,53,0);}
        100%{box-shadow:0 0 0 0 rgba(229,57,53,0);}
      }
      #goLive.live {
        background-color:#e53935!important;
        animation:pulse 1.2s infinite;
      }
    `;
    document.head.appendChild(style);
    el("stage")?.appendChild(liveIndicator);
  }
  liveIndicator.style.display = active ? "inline-block" : "none";
  el("goLive")?.classList.toggle("live", active);
}

// ========== UI HELPERS ==========
function addMsg(txt, cls = "") {
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  div.textContent = txt;
  el("chat")?.appendChild(div);
  const c = el("chat");
  if (c) c.scrollTop = c.scrollHeight;
}

function sendDataJSON(obj, kind = DataPacket_Kind.RELIABLE) {
  if (!room || !room.localParticipant) return;
  const payload = new TextEncoder().encode(JSON.stringify(obj));
  return room.localParticipant.publishData(payload, kind);
}

function attachVideo(videoTrack, identity) {
  const video = document.createElement("video");
  video.autoplay = true;
  video.playsInline = true;
  video.style.width = "100%";
  video.style.borderRadius = "8px";
  const stream = new MediaStream([videoTrack.mediaStreamTrack]);
  video.srcObject = stream;
  el("remoteArea")?.appendChild(video);
}
function applyRoomMetadata(meta = {}) {
  if (!meta || typeof meta !== "object") return;

  const slugFromResponse =
    typeof meta.room_slug === "string" && meta.room_slug.trim()
      ? meta.room_slug.trim()
      : typeof meta.room === "string"
        ? meta.room.replace(/-room$/, "").trim()
        : null;

  if (slugFromResponse) {
    liveRoomSlug = slugFromResponse;
    window.liveRoomSlug = liveRoomSlug;
  }

  if (meta.room_id !== undefined && meta.room_id !== null && meta.room_id !== "") {
    window.roomId = meta.room_id;
    try {
      localStorage.setItem("room_id", String(meta.room_id));
    } catch (err) {
      console.warn("Kon room_id niet opslaan in localStorage:", err);
    }
  }

  if (typeof meta.room_name === "string" && meta.room_name.trim()) {
    defaultRoomSubject = meta.room_name.trim();
  }

  let nextSubject = null;
  if (typeof meta.room_subject === "string" && meta.room_subject.trim()) {
    nextSubject = meta.room_subject.trim();
  } else if (typeof meta.room_name === "string" && meta.room_name.trim()) {
    nextSubject = meta.room_name.trim();
  }

  if (nextSubject) {
    currentRoomSubject = nextSubject;
    const subjectEl = el("roomSubject");
    if (subjectEl) subjectEl.textContent = nextSubject;
  }

  window.roomDefaultSubject = defaultRoomSubject;
  window.roomCurrentSubject = currentRoomSubject;
  if (slugFromResponse) {
    window.roomSlug = slugFromResponse;
  }
}

// ========== INIT ==========
async function init() {
  authToken = localStorage.getItem("token");
  storedUsername = localStorage.getItem("username");
  if (!authToken || !storedUsername) {
    alert("Geen token gevonden — log opnieuw in.");
    location.href = "/login.html";
    return;
  }

  updateStatusBar("Camera starten...");
  await startCameraPreview();

  // ✅ LiveKit token
  let lkToken;
  try {
    const res = await fetch(`${API}/livekit-token`, {
      method: "POST",
      headers: { Authorization: `Bearer ${authToken}`, "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    if (data && !data.room_slug) {
      const inferredSlug = typeof data.room === "string" ? data.room.replace(/-room$/, "").trim() : null;
      if (inferredSlug) data.room_slug = inferredSlug;
    }
    lkToken = data.token;
    livekitRoomName = data.room || null;
    liveRoomSlug = data.room_slug || liveRoomSlug;
    livekitUrl = data.url || DEFAULT_LIVEKIT_URL;
    applyRoomMetadata(data);
    console.log("🎬 LiveKit token ontvangen:", data);
  } catch (err) {
    addMsg(`❌ Token ophalen mislukt: ${err.message}`);
    return;
  }

  // ✅ Room setup
  room = new Room({
    adaptiveStream: true,
    dynacast: true,
    publishDefaults: { simulcast: true },
  });

  // ---------- EVENTS ----------
  room
    .on(RoomEvent.ParticipantConnected, (participant) => {
      const entry = trackParticipant(participant);
      updateViewerList();
      const name = entry?.name || getParticipantDisplayName(participant);
      addMsg(`👋 ${name} is joined`);
    })
    .on(RoomEvent.ParticipantDisconnected, (participant) => {
      const name = getParticipantDisplayName(participant);
      removeParticipantFromRoster(participant);
      updateViewerList();
      addMsg(`🚪 ${name} heeft verlaten`);
    })
    .on(RoomEvent.ParticipantMetadataChanged, (participant) => {
      trackParticipant(participant);
      updateViewerList();
    })
    .on(RoomEvent.TrackSubscribed, (_t, pub, part) => {
      if (pub.kind === Track.Kind.Video) attachVideo(pub.videoTrack, part.identity);
      else if (pub.kind === Track.Kind.Audio) pub.audioTrack.attach(new Audio());
    })
    .on(RoomEvent.DataReceived, (payload, participant) => {
      const from = participant?.identity || "onbekend";
      try {
        const msg = JSON.parse(new TextDecoder().decode(payload));
        handleDataMessage(msg, from);
      } catch (e) {
        console.warn("Data parse fout:", e);
      }
    })
    .on(RoomEvent.Disconnected, handleDisconnect); // ⬅️ één centrale handler

  await connectLiveKit(lkToken);

  // Globale LiveKit-referentie voor andere modules
  window.lkRoom = room;
  window.Johka?.updateViewerList?.();

  // Extra failsafe: refresh 1 seconde na init
  window.lkRoom
    .on(RoomEvent.ParticipantConnected, () => window.Johka?.updateViewerList?.())
    .on(RoomEvent.ParticipantDisconnected, () => window.Johka?.updateViewerList?.())
    .on(RoomEvent.ParticipantMetadataChanged, () => window.Johka?.updateViewerList?.());

  // Extra failsafe: refresh 1 seconde na init
  setTimeout(() => window.Johka?.updateViewerList?.(), 1000);


  // ---------- BUTTONS ----------
  el("goLive")?.addEventListener("click", startAV);
  el("toggleCam")?.addEventListener("click", toggleCamera);
  el("toggleMic")?.addEventListener("click", toggleMic);
  el("reconnectBtn")?.addEventListener("click", manualReconnect);
  el("leave")?.addEventListener("click", leaveRoom);
  el("donateBtn")?.addEventListener("click", sendTip);
  // ✅ Start broadcasting button
  el("startBroadcast")?.addEventListener("click", async () => {
  console.log("🚀 Start Broadcasting clicked");
    await startAV();
  });


  // Chat form (indien aanwezig) — we blokkeren default submit
  const chatForm = document.querySelector("#chatForm");
  if (chatForm) {
    chatForm.addEventListener("submit", (e) => {
      e.preventDefault();
      sendChat();
    });
  }

  // Enter in input mag géén LiveKit shortcut triggeren
  const chatInput = el("chatInput");
  if (chatInput && !chatInput.dataset.bound) {
    chatInput.dataset.bound = "1";
    chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();            // géén form submit
        e.stopImmediatePropagation();  // blokkeer LiveKit/andere handlers
        sendChat();
      } else {
        e.stopPropagation();           // vermijdt globale keybinds tijdens typen
      }
    });
  }

  updateViewerList();
}

// ========== CAMERA PREVIEW ==========
async function startCameraPreview() {
  try {
    console.log("🎥 Camera preview starten...");
    const tracks = await createLocalTracks({ audio: false, video: true });
    const videoTrack = tracks.find((t) => t.kind === "video");

    // Maak een nieuw <video> element
    const preview = document.createElement("video");
    preview.id = "miniPreview";
    Object.assign(preview.style, {
      position: "absolute",
      top: "15px",
      right: "15px",
      width: "200px",
      height: "150px",
      borderRadius: "8px",
      border: "2px solid rgba(255,255,255,0.4)",
      background: "#000",
      zIndex: "15",
      display: "none",
    });

    preview.autoplay = true;
    preview.playsInline = true;

    const stage = el("stage");
    if (stage) {
      // Wis alleen de placeholdertekst, niet alles
      // stage.innerHTML = "";
      stage.style.position = "relative";
      stage.appendChild(preview);
    }

    if (videoTrack) {
      const stream = new MediaStream([videoTrack.mediaStreamTrack]);
      preview.srcObject = stream;
      preview.style.display = "block";
      await preview.play().catch(() => {});
      updateStatusBar("Camera actief", "#4caf50");
    } else {
      updateStatusBar("Geen camera gevonden", "#e53935");
    }
  } catch (err) {
    updateStatusBar("Camera fout", "#e53935");
    console.warn("❌ Camera preview fout:", err);
  }
}

// ========== MINI PREVIEW ==========
function createMiniPreview(stream) {
  if (miniPreview) miniPreview.remove();
  miniPreview = document.createElement("video");
  Object.assign(miniPreview.style, {
    position: "absolute",
    bottom: "10px",
    right: "10px",
    width: "160px",
    height: "120px",
    borderRadius: "8px",
    border: "2px solid rgba(255,255,255,0.4)",
    background: "#000",
    zIndex: "15",
  });
  miniPreview.srcObject = stream;
  miniPreview.autoplay = true;
  miniPreview.muted = true;
  miniPreview.playsInline = true;
  el("stage")?.appendChild(miniPreview);
}

// ========== SNAPSHOTS ==========
function pickSnapshotVideo() {
  // pak liveVideo als die bestaat, anders miniPreview
  return document.getElementById("liveVideo") || document.getElementById("miniPreview");
}

async function sendSnapshot() {
  const video = pickSnapshotVideo();
  if (!isLive || !video || video.readyState < 2 || !authToken) return;
  const canvas = document.createElement("canvas");
  canvas.width = 320;
  canvas.height = 240;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  const img = canvas.toDataURL("image/jpeg", 0.7);
  
  try {
    await fetch(`${API}/room/snapshot`, {
      method: "POST",
      headers: { Authorization: `Bearer ${authToken}`, "Content-Type": "application/json" },
      body: JSON.stringify({ image: img }),
    });
    console.log("📸 Snapshot verstuurd");
  } catch (err) {
    console.warn("Snapshot fout:", err);
  }
}

function startSnapshotLoop() {
  stopSnapshotLoop();
  if (!isLive) return;
  snapshotTimer = setInterval(() => {
    if (isLive) {
      sendSnapshot();
    }
  }, 60000);
}

function stopSnapshotLoop() {
  if (snapshotTimer) {
    clearInterval(snapshotTimer);
    snapshotTimer = null;
  }
}
// ========== CHAT ==========
async function sendChat() {
  const input = el("chatInput");
  if (!input) return;
  const txt = input.value.trim();
  if (!txt) return;
  input.value = "";
  const msg = { type: "chat", text: txt };
  try {
    await sendDataJSON(msg);
    addMsg(`ik: ${txt}`, "me");
  } catch (err) {
    addMsg(`❌ chat fout: ${err.message}`);
  }
}

function handleDataMessage(msg, from) {
  if (msg.type === "chat") {
    addMsg(`${from}: ${msg.text}`);
  } else if (msg.type === "tip") {
    tipTotal += Number(msg.amount) || 0;
    const tb = el("tipTotalBar");
    if (tb) tb.textContent = tipTotal;
    addMsg(`💸 ${from} tipte ${msg.amount} tokens!`, "tip");
  }
}

// ========== TIPS ==========
async function sendTip(e) {
  if (e) e.preventDefault();
  const input = el("donateInput");
  const amt = Number(input?.value || "0");
  if (!amt || amt <= 0) return addMsg("⚠️ Ongeldig bedrag.");
  if (input) input.value = "";
  try {
    await sendDataJSON({ type: "tip", amount: amt });
    tipTotal += amt;
    const tb = el("tipTotalBar");
    if (tb) tb.textContent = tipTotal;
    addMsg(`💸 Jij tipte ${amt} tokens`, "me");
  } catch (err) {
    addMsg(`❌ Tip fout: ${err.message}`);
  }
}

// ========== TOGGLE MIC/CAM (stubs die niets breken) ==========
async function toggleMic() {
  try {
    const pub = room?.localParticipant?.getTrackPublications()?.find(p => p.kind === Track.Kind.Audio);
    if (pub) {
      if (pub.isMuted) await pub.unmute();
      else await pub.mute();
    }
  } catch (e) {
    console.warn("toggleMic error", e);
  }
}

async function toggleCamera() {
  try {
    const pub = room?.localParticipant?.getTrackPublications()?.find(p => p.kind === Track.Kind.Video);
    if (pub) {
      if (pub.isMuted) await pub.unmute();
      else await pub.mute();
    }
  } catch (e) {
    console.warn("toggleCamera error", e);
  }
}

async function manualReconnect() {
  reconnectAttempts = 0;
  await refreshLiveKitTokenAndReconnect();
}
async function announceGoLive() {
  if (isLive) return true;
  if (!authToken) {
    addMsg("⚠️ Niet ingelogd — kan go-live niet registreren.");
    return false;
  }
  try {
    const res = await fetch(`${API}/go-live`, {
      method: "POST",
      headers: { Authorization: `Bearer ${authToken}` },
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = payload?.detail || res.statusText;
      addMsg(`❌ Go-live mislukt: ${detail}`);
      return false;
    }
    liveRoomSlug = payload?.room || liveRoomSlug;
    isLive = true;
    return true;
  } catch (err) {
    addMsg(`❌ Go-live fout: ${err.message}`);
    return false;
  }
}

async function endLive() {
  if (!isLive || !authToken) {
    isLive = false;
    return;
  }
  try {
    await fetch(`${API}/end-live`, {
      method: "POST",
      headers: { Authorization: `Bearer ${authToken}` },
    });
  } catch (err) {
    console.warn("end-live fout", err);
  } finally {
    isLive = false;
  }
}

// ===================== LIVE START =====================
async function startAV() {
  if (isStarting) {
    addMsg("⚠️ Uitzending wordt al gestart...");
    return;
  }
  isStarting = true;
  addMsg("🎥 Start camera en microfoon...");
  updateStatusBar("Uitzending starten...", "#ff9800");

  try {
    const tracks = await createLocalTracks({ audio: true, video: true });
    localTracks = tracks;

    // ⏹️ Verberg kleine preview
    const _mini = document.getElementById("miniPreview");
    if (_mini) _mini.remove();

    for (const t of localTracks) {
      await room.localParticipant.publishTrack(t);
      addMsg(`✅ ${t.kind} gestart`);
    }


    const stage = el("stage");
    if (stage) {
      stage.innerHTML = "";
      const liveVideo = document.createElement("video");
      liveVideo.id = "liveVideo";
      liveVideo.autoplay = true;
      liveVideo.muted = true;  // geen echo
      liveVideo.playsInline = true;
      Object.assign(liveVideo.style, {
        width: "100%",
        height: "100%",
        objectFit: "cover",
        borderRadius: "10px",
        background: "#000",
      });
      stage.appendChild(liveVideo);

      const videoTrack = localTracks.find((t) => t.kind === "video");
      if (videoTrack) {
        const stream = new MediaStream([videoTrack.mediaStreamTrack]);
        liveVideo.srcObject = stream;
        await liveVideo.play().catch(() => {});
      }
    }
    updateStatusBar("📡 Media klaar – live gaan...", "#ff9800");
    const registered = await announceGoLive();
    if (!registered) {
      updateStatusBar("❌ Go-live mislukt", "#e53935");
      showLiveIndicator(false);
      stopSnapshotLoop();
      try {
        localTracks.forEach((track) => {
          try {
            room?.localParticipant?.unpublishTrack?.(track);
          } catch (_) {}
          track.stop();
        });
      } finally {
        localTracks = [];
      }
      await startCameraPreview();
      return;
    }
    updateStatusBar("📡 Live actief", "#e53935");
    showLiveIndicator(true);

    // ✅ snapshot loop starten
    await sendSnapshot();
    startSnapshotLoop();

    // ✅ heartbeat starten
    if (!window.johkaHeartbeat && authToken) {
      window.johkaHeartbeat = setInterval(() => {
        fetch(`${API}/live/start`, {
          method: "POST",
          headers: { Authorization: `Bearer ${authToken}` },
        }).catch(() => {});
      }, 20000);
    }

  } catch (err) {
    updateStatusBar("❌ Fout bij uitzending", "#e53935");
    addMsg("❌ Fout bij start camera: " + err.message);
    console.error(err);
    if (isLive) {
      await endLive();
    }
    showLiveIndicator(false);
    stopSnapshotLoop();
    localTracks.forEach((track) => {
      try {
        room?.localParticipant?.unpublishTrack?.(track);
      } catch (_) {}
      track.stop();
    });
    localTracks = [];
    await startCameraPreview();
  } finally {
    isStarting = false;
  }
}


// ===================== LEAVE ROOM =====================
async function leaveRoom() {
  isLeaving = true; // ⬅️ intentional leave
  addMsg("👋 Je verlaat de room...");
  try {
    // stop heartbeat eerst
    if (window.johkaHeartbeat) {
      clearInterval(window.johkaHeartbeat);
      window.johkaHeartbeat = null;
    }

    // backend melden dat stream stopt
    if (authToken) {
      fetch(`${API}/live/stop`, {
        method: "POST",
        headers: { Authorization: `Bearer ${authToken}` }
      }).catch(() => {});
    }
    await endLive();
    // disconnect + tracks stoppen
    if (room) await room.disconnect();
    localTracks.forEach(t => t.stop());
    localTracks = [];
  } finally {
    const stage = el("stage");
    if (stage) stage.innerHTML = "<p style='color:#666;'>Camera niet actief...</p>";
    await startCameraPreview();
    updateStatusBar("Verbinding verbroken", "#888");
    showLiveIndicator(false);

    stopSnapshotLoop();
    isLive = false;
    isStarting = false;
    rosterByKey.clear();
    updateViewerList();
    isLeaving = false;
  }
}

// ========== CONNECT / RECONNECT ==========
async function connectLiveKit(lkToken) {
  try {
    updateStatusBar("Verbinden met LiveKit...", "#ff9800");
    await room.connect(livekitUrl, lkToken);

    // ❌ GEEN extra Disconnected handler met reload hier! (dat veroorzaakte reconnect bij typen)

    updateStatusBar("Verbonden ✅", "#4caf50");
    addMsg(`✅ Verbonden met LiveKit-server (${livekitRoomName || "room"})`);
    reconnectAttempts = 0;
    syncRosterFromRoom();
  } catch (err) {
    updateStatusBar("Verbinding mislukt", "#e53935");
    handleDisconnect();
  }
}

async function handleDisconnect() {
  if (isLeaving) return; // we hebben zelf de verbinding beëindigd
  showLiveIndicator(false);
  updateStatusBar("Verbinding verbroken – herverbinden...", "#ff9800");
  stopSnapshotLoop();
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectAttempts++;
  const delay = Math.min(15000, 3000 * reconnectAttempts);
  reconnectTimer = setTimeout(refreshLiveKitTokenAndReconnect, delay);
}

async function refreshLiveKitTokenAndReconnect() {
  if (isLeaving || !authToken) return;
  try {
    const res = await fetch(`${API}/livekit-token`, {
      method: "POST",
      headers: { Authorization: `Bearer ${authToken}`, "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    if (data && !data.room_slug) {
      const inferredSlug = typeof data.room === "string" ? data.room.replace(/-room$/, "").trim() : null;
      if (inferredSlug) data.room_slug = inferredSlug;
    }
    livekitRoomName = data.room || livekitRoomName;
    liveRoomSlug = data.room_slug || liveRoomSlug;
    livekitUrl = data.url || livekitUrl;
    applyRoomMetadata(data);
    await room.connect(livekitUrl, data.token);
    updateStatusBar("✅ Herverbonden", "#4caf50");
    addMsg("✅ Herverbonden met LiveKit");
    reconnectAttempts = 0;
    syncRosterFromRoom();
    if (isLive) {
      showLiveIndicator(true);
      await sendSnapshot();
      startSnapshotLoop();
    }
  } catch (err) {
    updateStatusBar("Herverbinden mislukt", "#e53935");
    handleDisconnect();
  }
}



// ========== STARTUP ==========
window.addEventListener("beforeunload", () => {
  if (isLive && authToken) {
    const headers = { Authorization: `Bearer ${authToken}` };
    fetch(`${API}/live/stop`, { method: "POST", headers, keepalive: true }).catch(() => {});
    fetch(`${API}/end-live`, { method: "POST", headers, keepalive: true }).catch(() => {});
  }
});
window.addEventListener("DOMContentLoaded", () => setTimeout(init, 300));

// ======================================================
// 🔽 JOHKA LIVE – Loader voor onderste tabs (Apps/Bio/…)
// ======================================================
document.addEventListener("DOMContentLoaded", () => {
  const bottomTabs = document.querySelectorAll("#bottomTabs button");
  const bottomContent = document.getElementById("bottomContent");
  if (!bottomTabs.length || !bottomContent) return;

  let cleanupBioIframe = null;

  bottomTabs.forEach(btn => {
    btn.addEventListener("click", async () => {
      const page = btn.dataset.page;
      bottomTabs.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");

      // 👤 Speciaal geval: Creator Bio laden in een iframe zodat scripts blijven werken
      if (page === "creatorpage.html") {
        if (typeof cleanupBioIframe === "function") {
          cleanupBioIframe();
          cleanupBioIframe = null;
        }
        const username = storedUsername || localStorage.getItem("username");
        if (!username) {
          bottomContent.innerHTML = `<p style="color:#e53935; text-align:center;">❌ Geen gebruiker gevonden voor bio.</p>`;
          return;
        }

        const separator = page.includes("?") ? "&" : "?";
        const iframeSrc = `${page}${separator}u=${encodeURIComponent(username)}&embed=1`;
        bottomContent.innerHTML = `
          <iframe
            src="${iframeSrc}"
            title="Creator bio"
            style="width:100%;min-height:640px;border:0;background:transparent;display:block;"
            loading="lazy"
            allowtransparency="true"
          ></iframe>
        `;
        const iframe = bottomContent.querySelector("iframe");
        if (iframe) {
          const adjustIframeHeight = () => {
            try {
              const doc = iframe.contentDocument || iframe.contentWindow?.document;
              if (!doc) return;
              const { body, documentElement } = doc;
              const candidateHeights = [
                body?.scrollHeight,
                body?.offsetHeight,
                documentElement?.scrollHeight,
                documentElement?.offsetHeight,
              ].filter(Boolean);
              const nextHeight = Math.max(...candidateHeights, 640);
              if (Number.isFinite(nextHeight)) {
                iframe.style.height = `${nextHeight}px`;
              }
            } catch (err) {
              console.warn("iframe resize mislukt", err);
            }
          };

          const cleanupObservers = () => {
            if (iframe.__bioResizeObserver) {
              iframe.__bioResizeObserver.disconnect();
              delete iframe.__bioResizeObserver;
            }
            if (iframe.__bioMutationObserver) {
              iframe.__bioMutationObserver.disconnect();
              delete iframe.__bioMutationObserver;
            }
            if (iframe.__bioWindowResizeHandler) {
              window.removeEventListener("resize", iframe.__bioWindowResizeHandler);
              delete iframe.__bioWindowResizeHandler;
            }
          };

          cleanupObservers();

          iframe.addEventListener("load", () => {
            adjustIframeHeight();
            try {
              const doc = iframe.contentDocument;
              if (!doc || !doc.body) return;

              if (typeof ResizeObserver !== "undefined") {
                const resizeObserver = new ResizeObserver(() => adjustIframeHeight());
                resizeObserver.observe(doc.documentElement);
                resizeObserver.observe(doc.body);
                iframe.__bioResizeObserver = resizeObserver;
              }

              if (typeof MutationObserver !== "undefined") {
                const mutationObserver = new MutationObserver(() => adjustIframeHeight());
                mutationObserver.observe(doc.documentElement, {
                  childList: true,
                  subtree: true,
                  attributes: true,
                  characterData: true,
                });
                iframe.__bioMutationObserver = mutationObserver;
              }
            } catch (err) {
              console.warn("Observer setup voor bio-iframe mislukt", err);
            }
          }, { once: true });

          const onWindowResize = () => adjustIframeHeight();
          iframe.__bioWindowResizeHandler = onWindowResize;
          window.addEventListener("resize", onWindowResize, { passive: true });
          cleanupBioIframe = cleanupObservers;
        }
        return;
      }

      if (typeof cleanupBioIframe === "function") {
        cleanupBioIframe();
        cleanupBioIframe = null;
      }


      try {
        // 🔧 Gebruik het pad letterlijk – geen automatische /pages/ meer
        bottomContent.innerHTML = `<p style="color:#aaa;text-align:center;margin-top:20px;">Laden…</p>`;
        const res = await fetch(page);
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const html = await res.text();
        const renderFetchedHtml = (target, markup) => {
          const parser = new DOMParser();
          const doc = parser.parseFromString(markup, "text/html");

          const scriptsInOrder = [];
          const appendNodes = nodes => {
            nodes.forEach(node => {
              if (node.nodeName === "SCRIPT") {
                scriptsInOrder.push(node);
              } else {
                target.appendChild(node.cloneNode(true));
              }
            });
          };

          target.innerHTML = "";
          appendNodes(Array.from(doc.body?.childNodes || []));

          if (!target.childNodes.length) {
            const main = doc.querySelector("main") || doc.documentElement;
            if (main) {
              appendNodes(Array.from(main.childNodes));
            }
          }

          const headScripts = Array.from(doc.head?.querySelectorAll("script") || []);
          scriptsInOrder.unshift(...headScripts);

          scriptsInOrder.forEach(original => {
            const script = document.createElement("script");
            Array.from(original.attributes || []).forEach(attr => {
              script.setAttribute(attr.name, attr.value);
            });
            if (original.textContent) {
              script.textContent = original.textContent;
            }
            target.appendChild(script);
          });
        };

        renderFetchedHtml(bottomContent, html);
      } catch (err) {
        console.error("❌ Fout bij laden:", err);
        bottomContent.innerHTML = `<p style="color:#e53935;">❌ Fout bij laden: ${err.message}</p>`;
      }
    });
  });
});



// === EMOJI PICKER ===
(function setupEmojiPicker () {
  const emojiBtn = document.getElementById("emojiBtn");
  const input = document.getElementById("chatInput");
  if (!emojiBtn || !input) return; // niets te doen als UI er niet is

  const picker = new EmojiButton({
    position: "top-start",
    theme: "light",
    zIndex: 9999
  });

  emojiBtn.addEventListener("click", () => {
    picker.togglePicker(emojiBtn);
  });

  picker.on("emoji", (selection) => {
    const emoji = selection.emoji || selection; // lib geeft selection.emoji
    const start = input.selectionStart ?? input.value.length;
    const end   = input.selectionEnd   ?? input.value.length;
    input.setRangeText(emoji, start, end, "end"); // invoegen op cursor
    input.focus();
  });
})();


// === PRIVÉROOM LOGICA ===
const isPrivateToggle = document.getElementById("isPrivateToggle");
const privateOptions = document.getElementById("privateOptions");
const privateModeSel = document.getElementById("privateMode");
const privateKeyField = document.getElementById("privateKeyField");
const privateTokenField = document.getElementById("privateTokenField");
const createPrivateBtn = document.getElementById("createPrivateBtn");

if (isPrivateToggle) {
  isPrivateToggle.addEventListener("change", () => {
    privateOptions.style.display = isPrivateToggle.checked ? "block" : "none";
  });
}

if (privateModeSel) {
  privateModeSel.addEventListener("change", () => {
    const mode = privateModeSel.value;
    privateKeyField.style.display = (mode === "invite" || mode === "password") ? "block" : "none";
    privateTokenField.style.display = (mode === "token") ? "block" : "none";
  });
}

if (createPrivateBtn) {
  createPrivateBtn.addEventListener("click", async () => {
    const mode = privateModeSel.value;
    const key = document.getElementById("privateKey")?.value || null;
    const tokens = Number(document.getElementById("privateTokens")?.value) || 0;
    const name = prompt("Geef een naam voor je privéroom:", "mijn-room");

    if (!name) return alert("Geen naam opgegeven.");

    try {
      const token = localStorage.getItem("token");
      if (!token) return alert("Je bent niet ingelogd.");

      const res = await fetch("https://api.johka.be/api/room/create-private", {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${token}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          name,
          access_mode: mode,
          access_key: key,
          token_price: tokens
        })
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);

      alert(`✅ Privéroom aangemaakt!\nSlug: ${data.slug}\nToegang: ${data.access_mode}`);
    } catch (err) {
      console.error(err);
      alert("❌ Privéroom aanmaken mislukt: " + err.message);
    }
  });
}
// === init Private Room UI for roomsettings.html ===
function initPrivateRoomUI() {
  const toggle = document.getElementById("isPrivateToggle");
  const options = document.getElementById("privateOptions");
  const modeSel = document.getElementById("privateMode");
  const keyField = document.getElementById("privateKeyField");
  const tokenField = document.getElementById("privateTokenField");
  const createBtn = document.getElementById("createPrivateBtn");

  if (!toggle) return; // pagina nog niet geladen of niet aanwezig

  toggle.addEventListener("change", () => {
    options.style.display = toggle.checked ? "block" : "none";
  });

  if (modeSel) {
    modeSel.addEventListener("change", () => {
      const mode = modeSel.value;
      keyField.style.display =
        mode === "invite" || mode === "password" ? "block" : "none";
      tokenField.style.display = mode === "token" ? "block" : "none";
    });
  }

  if (createBtn) {
    createBtn.addEventListener("click", async () => {
      const name = prompt("Naam van privéroom:", "mijn-room");
      if (!name) return;
      const mode = modeSel.value;
      const key = document.getElementById("privateKey")?.value || null;
      const tokens = Number(
        document.getElementById("privateTokens")?.value || 0
      );

      const token = localStorage.getItem("token");
      if (!token) return alert("Je bent niet ingelogd.");

      const res = await fetch("https://api.johka.be/api/room/create-private", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          name,
          access_mode: mode,
          access_key: key,
          token_price: tokens,
        }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);

      alert(`✅ Privéroom aangemaakt!\nSlug: ${data.slug}`);
    });
  }
}

// 👉 telkens wanneer de roomsettings-pagina geladen wordt
document.addEventListener("DOMContentLoaded", () => {
  // controleer of we op de roomsettings-pagina zijn
  if (window.location.pathname.endsWith("roomsettings.html")) {
    initPrivateRoomUI();
  }
});

// ================================
// 👥 JOHKA - ViewerList (safe scope)
// ================================
window.Johka = window.Johka || {};

window.Johka.updateViewerList = function () {
  try {
    syncRosterFromRoom();
  } catch (err) {
    console.error("ViewerList update failed:", err);
  }
};

