// ======= JOHKA LIVE v9.6 – Snapshots + Chat + Tips + Live UI =======
import {
  Room,
  RoomEvent,
  Track,
  DataPacket_Kind,
  createLocalTracks,
} from "https://cdn.jsdelivr.net/npm/livekit-client@2.0.10/+esm";

const API = "https://api.johka.be/api";
const WS_URL = "wss://live.johka.be";

const el = (id) => document.getElementById(id);
let room;
let localTracks = [];
let viewers = new Set();
let reconnectAttempts = 0;
let reconnectTimer = null;
let micEnabled = true;
let liveIndicator = null;
let miniPreview = null;
let snapshotTimer = null;
let tipTotal = 0;

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
  el("chat").appendChild(div);
  el("chat").scrollTop = el("chat").scrollHeight;
}

function sendDataJSON(obj, kind = DataPacket_Kind.RELIABLE) {
  if (!room || !room.localParticipant) return;
  const payload = new TextEncoder().encode(JSON.stringify(obj));
  return room.localParticipant.publishData(payload, kind);
}

// ========== INIT ==========
async function init() {
  const token = localStorage.getItem("token");
  const username = localStorage.getItem("username");
  if (!token || !username) {
    alert("Geen token gevonden — log opnieuw in.");
    location.href = "/login.html";
    return;
  }

  updateStatusBar("Camera starten...");
  await startCameraPreview();

  // ✅ LiveKit token
  let lkToken, slug;
  try {
    const res = await fetch(`${API}/livekit-token`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    lkToken = data.token;
    slug = data.room;
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
    .on(RoomEvent.ParticipantConnected, (p) => {
      viewers.add(p.identity);
      updateViewerList();
      addMsg(`👋 ${p.identity} is joined`);
    })
    .on(RoomEvent.ParticipantDisconnected, (p) => {
      viewers.delete(p.identity);
      updateViewerList();
      addMsg(`🚪 ${p.identity} heeft verlaten`);
    })
    .on(RoomEvent.TrackSubscribed, (_t, pub, part) => {
      if (pub.kind === Track.Kind.Video)
        attachVideo(pub.videoTrack, part.identity);
      else if (pub.kind === Track.Kind.Audio)
        pub.audioTrack.attach(new Audio());
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
    .on(RoomEvent.Disconnected, handleDisconnect);

  await connectLiveKit(lkToken, slug);

  // ---------- BUTTONS ----------
  el("goLive").onclick = startAV;
  el("toggleCam").onclick = toggleCamera;
  el("toggleMic").onclick = toggleMic;
  el("reconnectBtn").onclick = manualReconnect;
  el("leave").onclick = leaveRoom;
  el("donateBtn").onclick = sendTip;
  document.querySelector("#chatForm")?.addEventListener("submit", sendChat);

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
      stage.innerHTML = "";
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
async function sendSnapshot() {
  const video = el("preview");
  if (!video || video.readyState < 2) return;
  const canvas = document.createElement("canvas");
  canvas.width = 320;
  canvas.height = 240;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  const img = canvas.toDataURL("image/jpeg", 0.7);
  const token = localStorage.getItem("token");
  if (!token) return;
  try {
    await fetch(`${API}/room/snapshot`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ image: img }),
    });
    console.log("📸 Snapshot verstuurd");
  } catch (err) {
    console.warn("Snapshot fout:", err);
  }
}
function startSnapshotLoop() {
  if (snapshotTimer) clearInterval(snapshotTimer);
  snapshotTimer = setInterval(sendSnapshot, 60000);
}

// ========== CHAT ==========
async function sendChat(e) {
  e.preventDefault();
  const input = el("chatInput");
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
    el("tipTotalBar").textContent = tipTotal;
    addMsg(`💸 ${from} tipte ${msg.amount} tokens!`, "tip");
  }
}

// ========== TIPS ==========
async function sendTip(e) {
  e.preventDefault();
  const input = el("donateInput");
  const amt = Number(input.value || "0");
  if (!amt || amt <= 0) return addMsg("⚠️ Ongeldig bedrag.");
  input.value = "";
  try {
    await sendDataJSON({ type: "tip", amount: amt });
    tipTotal += amt;
    el("tipTotalBar").textContent = tipTotal;
    addMsg(`💸 Jij tipte ${amt} tokens`, "me");
  } catch (err) {
    addMsg(`❌ Tip fout: ${err.message}`);
  }
}

// ===================== LIVE START =====================
async function startAV() {
  addMsg("🎥 Start camera en microfoon...");
  updateStatusBar("Uitzending starten...", "#ff9800");

  try {
    const tracks = await createLocalTracks({ audio: true, video: true });
    localTracks = tracks;

    // ⏹️ Verberg de kleine preview zodra we live gaan
    const miniPreview = document.getElementById("miniPreview");
    if (miniPreview) miniPreview.remove();

    // Publiceer tracks naar LiveKit
    for (const t of localTracks) {
      await room.localParticipant.publishTrack(t);
      addMsg(`✅ ${t.kind} gestart`);
    }

    // 🎬 Toon groot live-beeld in #stage
    const stage = el("stage");
    if (stage) {
      stage.innerHTML = "";
      const liveVideo = document.createElement("video");
      liveVideo.id = "liveVideo";
      liveVideo.autoplay = true;
      liveVideo.muted = true; // geen echo
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

    updateStatusBar("📡 Live actief", "#e53935");
    showLiveIndicator(true);
    await sendSnapshot();
    startSnapshotLoop();
  } catch (err) {
    updateStatusBar("❌ Fout bij uitzending", "#e53935");
    addMsg("❌ Fout bij start camera: " + err.message);
    console.error(err);
  }
}

// ===================== LEAVE ROOM =====================
async function leaveRoom() {
  addMsg("👋 Je verlaat de room...");
  if (room) await room.disconnect();

  // ⛔ Stop tracks
  localTracks.forEach(t => t.stop());
  localTracks = [];

  // 🧹 Maak stage leeg
  const stage = el("stage");
  if (stage) stage.innerHTML = "<p style='color:#666;'>Camera niet actief...</p>";

  // 🎞️ Start opnieuw mini-preview
  await startCameraPreview();

  updateStatusBar("Verbinding verbroken", "#888");
  showLiveIndicator(false);
}


// ========== CONNECT / RECONNECT ==========
async function connectLiveKit(lkToken, slug) {
  try {
    updateStatusBar("Verbinden met LiveKit...", "#ff9800");
    await room.connect(WS_URL, lkToken);
    updateStatusBar("Verbonden ✅", "#4caf50");
    addMsg(`✅ Verbonden met LiveKit-server (${slug})`);
    reconnectAttempts = 0;
  } catch (err) {
    updateStatusBar("Verbinding mislukt", "#e53935");
    handleDisconnect();
  }
}

async function handleDisconnect() {
  showLiveIndicator(false);
  updateStatusBar("Verbinding verbroken – herverbinden...", "#ff9800");
  if (snapshotTimer) clearInterval(snapshotTimer);
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectAttempts++;
  const delay = 3000 * reconnectAttempts;
  reconnectTimer = setTimeout(refreshLiveKitTokenAndReconnect, delay);
}

async function refreshLiveKitTokenAndReconnect() {
  try {
    const res = await fetch(`${API}/livekit-token`, {
      method: "POST",
      headers: { Authorization: `Bearer ${localStorage.getItem("token")}`, "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    await room.connect(WS_URL, data.token);
    updateStatusBar("✅ Herverbonden", "#4caf50");
    addMsg("✅ Herverbonden met LiveKit");
    reconnectAttempts = 0;
    showLiveIndicator(true);
    startSnapshotLoop();
  } catch (err) {
    updateStatusBar("Herverbinden mislukt", "#e53935");
    handleDisconnect();
  }
}

// ========== VIEWERS ==========
function updateViewerList() {
  const vc = el("viewCount");
  if (vc) vc.textContent = viewers.size;
}

// ========== STARTUP ==========
window.addEventListener("DOMContentLoaded", () => setTimeout(init, 300));
