import {
  Room,
  RoomEvent,
  Track,
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

let normalizedSlug = null;
let activeSlug = null;
let lkRoom = null;
let isLeaving = false;

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

function attachVideoTrack(videoTrack) {
  const stream = new MediaStream([videoTrack.mediaStreamTrack]);
  remoteVideo.srcObject = stream;
}

async function notifyView(endpoint) {
  if (!activeSlug) return;
  try {
    await fetch(`${API}/room/view-${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ room: activeSlug }),
    });
  } catch (err) {
    console.warn(`Viewer ${endpoint} notify failed`, err);
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
    creatorNameEl.textContent = info.owner;
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

async function obtainToken() {
  const token = localStorage.getItem("token");
  if (!token) {
    showOverlay("🔒 Inloggen vereist om streams te bekijken");
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
    throw new Error(detail);
  }
  return data;
}

function setupRoomEvents(room) {
  room
    .on(RoomEvent.TrackSubscribed, (_track, publication, participant) => {
      if (publication.kind === Track.Kind.Video && publication.videoTrack) {
        attachVideoTrack(publication.videoTrack);
        hideOverlay();
      } else if (publication.kind === Track.Kind.Audio && publication.audioTrack) {
        publication.audioTrack.attach(new Audio());
      }
    })
    .on(RoomEvent.TrackUnsubscribed, (_track, publication) => {
      if (publication.kind === Track.Kind.Video) {
        remoteVideo.srcObject = null;
      }
    })
    .on(RoomEvent.ParticipantConnected, updateViewerCount)
    .on(RoomEvent.ParticipantDisconnected, updateViewerCount)
    .on(RoomEvent.Disconnected, () => {
      setStatus({ live: false, text: "Verbinding verbroken" });
      showOverlay("Verbinding verbroken");
      remoteVideo.srcObject = null;
      if (!isLeaving) {
        notifyView("end");
      }
    });
}

function updateViewerCount() {
  if (!lkRoom) return;
  const total = lkRoom.participants.size + 1; // inclusief jezelf
  viewerCountEl.textContent = `👁 ${Math.max(total, 0)} kijkers`;
}

async function start() {
  try {
    const info = await loadRoomInfo();
    if (requestedUsername) {
      creatorNameEl.textContent = requestedUsername;
    }
    if (!info.is_live) {
      showOverlay("🔴 Deze creator is momenteel offline");
      return;
    }
    const tokenData = await obtainToken();
    lkRoom = new Room({ adaptiveStream: true, dynacast: true });
    setupRoomEvents(lkRoom);
    await lkRoom.connect(tokenData.url, tokenData.token, { autoSubscribe: true });
    updateViewerCount();
    await notifyView("start");
  } catch (err) {
    console.error(err);
  }
}

leaveBtn.addEventListener("click", async () => {
  isLeaving = true;
  await notifyView("end");
  if (lkRoom) {
    await lkRoom.disconnect();
  }
  window.location.href = "/index.html";
});

window.addEventListener("beforeunload", () => {
  if (!isLeaving) {
    const blob = new Blob(
      [JSON.stringify({ room: activeSlug })],
      { type: "application/json" }
    );
    navigator.sendBeacon?.(`${API}/room/view-end`, blob);
  }
});

start();