const chat = document.getElementById("chat");
const form = document.getElementById("chat-form");
const userInput = document.getElementById("user-input");
const typingIndicator = document.getElementById("typing-indicator");
const clearBtn = document.getElementById("clear-btn");
const chatFrame = document.getElementById("chat-frame");

let userLocation = null;

// Auto-resize config for textarea
let baseInputHeight = null;           // single-line height in px
const MAX_INPUT_HEIGHT = 120;         // cap growth
let showFcpChips = false; // controls showing Fastest/Cheapest/Premium chips

/* ---------- Station card helpers ---------- */
function formatDistance(km) {
  if (km == null) return "—";
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(1)} km`;
}
function availabilityBadge(avail) {
  const v = (avail || "").toString().toLowerCase();
  if (v === "yes" || v === "available") return { text: "Available", cls: "available" };
  if (v === "no" || v === "busy") return { text: "Busy", cls: "busy" };
  return { text: "Unknown", cls: "unknown" };
}

/* Renders a list of station cards inside a bot message row */
function addStationCards(stations = [], opts = {}) {
  if (!Array.isArray(stations) || stations.length === 0) {
    addMessage("No stations found nearby.", "bot");
    return;
  }

  const row = document.createElement("div");
  row.className = "message-row bot";

  const avatar = document.createElement("div");
  avatar.className = "avatar bot";
  avatar.textContent = "⚡";

  const bubble = document.createElement("div");
  bubble.className = "bubble station-wrap";

  const list = document.createElement("div");
  list.className = "station-list";

  const availPill = (a) => {
    const v = (a || "").toString().toLowerCase();
    if (v === "yes" || v === "available") return { txt: "Available", cls: "available" };
    if (v === "no" || v === "busy") return { txt: "Busy", cls: "busy" };
    return { txt: "Unknown", cls: "unknown" };
  };
  const fmtDist = (km) => km == null ? "—" : (km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(1)} km`);

  stations.forEach(s => {
    const badge = availPill(s.availability);
    const card = document.createElement("div");
    card.className = "station-card";
    card.innerHTML = `
  <div class="station-icon">⚡</div>

  <div class="station-main">
    <div class="station-top">
      <div>
        <div class="station-name">${s.name || "Unnamed station"}</div>
        <div class="station-address">${s.address || ""}</div>
      </div>
      ${opts.show_availability
        ? `<span class="station-badge ${badge.cls}">${badge.txt}</span>`
        : ""}
    </div>

    <div class="station-meta">
      <div class="kv">
        <div class="label">Distance</div>
        <div class="value">${fmtDist(s.distance_km)}</div>
      </div>
      <div class="kv">
        <div class="label">Cost</div>
        <div class="value emphasize">${s.cost || "—"}</div>
      </div>
      <div class="kv power">
        <div class="label">Power</div>
        <div class="value">${s.power != null ? `${s.power} kW` : "—"}</div>
      </div>
    </div>

    <div class="station-actions">
      <button type="button"
              class="btn-primary station-directions"
              data-id="${s.station_id}">Get Directions</button>
    </div>
  </div>
`;
    list.appendChild(card);
  });

  bubble.appendChild(list);
  const ts = document.createElement("span");
  ts.className = "timestamp";
  ts.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  bubble.appendChild(ts);

  row.append(avatar, bubble);
  chat.appendChild(row);

  bubble.querySelectorAll(".station-directions").forEach(btn => {
    btn.addEventListener("click", () => {
      const sid = btn.getAttribute("data-id");
      addMessage("Get Directions", "user");
      sendMessage(`/get_directions{"station_id":"${sid}"}`);
    });
  });

  localStorage.setItem("chatHistory", chat.innerHTML);
}


/* ---------- Utilities ---------- */
const scrollToBottom = () => {
  requestAnimationFrame(() => {
    chatFrame.scrollTop = chatFrame.scrollHeight;
  });
};

const timestamp = () =>
  new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/* ---------- Basic sanitizer for bot text ---------- */
function sanitize(html) {
  // strip <script>...</script> and inline on* handlers
  return String(html)
    .replace(/<script[\s\S]*?>[\s\S]*?<\/script>/gi, "")
    .replace(/\son\w+="[^"]*"/gi, "");
}

/* ---------- Message rendering with avatars ---------- */
function addMessage(text, sender = "bot") {
  const row = document.createElement("div");
  row.className = `message-row ${sender}`;

  const avatar = document.createElement("div");
  avatar.className = `avatar ${sender}`;
  avatar.textContent = sender === "bot" ? "⚡" : "🙂";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  // Strip markdown-style bold (**text**) before rendering
  const cleanText = text.replace(/\*\*(.*?)\*\*/g, "$1");
  bubble.innerHTML = sanitize(cleanText);

  const ts = document.createElement("span");
  ts.className = "timestamp";
  ts.textContent = timestamp();
  bubble.appendChild(ts);

  if (sender === "bot") {
    row.append(avatar, bubble);
  } else {
    row.append(bubble, avatar);
  }

  chat.appendChild(row);
  persist();
  scrollToBottom();

  // Add inline chips when the bot asks for 1/2/3
  if (
    sender === "bot" &&
    /press|type/i.test(text) &&
    /1.*2.*3|1, 2, or 3/i.test(text)
  ) {
    addChips(["1", "2", "3"]);
  }
  // Arm the F/C/P chips only when the bot asks the actual question
  if (sender === "bot" && /what'?s\s+most\s+important\s+to\s+you/i.test(text)) {
    showFcpChips = true;
  }
  // Add inline chips for Fastest/Cheapest/Premium — only once per prompt
  if (
    sender === "bot" &&
    showFcpChips &&
    /fastest|cheapest|premium/i.test(text) &&
    !/1.*2.*3/.test(text)
  ) {
    addChips(["Cheapest", "Fastest", "Premium"]);
    showFcpChips = false;  // prevent re-showing on follow-up bot messages
  }
}

function addChips(options) {
  const row = document.createElement("div");
  row.className = "message-row bot";

  const spacer = document.createElement("div");
  spacer.className = "avatar bot";
  spacer.textContent = "⚡";

  const wrap = document.createElement("div");
  wrap.className = "bubble";

  const chips = document.createElement("div");
  chips.className = "chips";

  options.forEach((opt) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = opt;
    chip.addEventListener("click", () => {
      showFcpChips = false;        // ensure we don't show them again on the next bot line
      addMessage(opt, "user");
      sendMessage(opt);
      row.remove();                 // remove the chips row after click
    });
    chips.appendChild(chip);
  });

  wrap.appendChild(chips);
  row.append(spacer, wrap);
  chat.appendChild(row);
  persist();
  scrollToBottom();
}

/* Cards keep the same payloads but render inside bot bubble */
function addCard(innerHTML) {
  const row = document.createElement("div");
  row.className = "message-row bot";

  const avatar = document.createElement("div");
  avatar.className = "avatar bot";
  avatar.textContent = "⚡";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = innerHTML;
  bubble.appendChild(card);

  const ts = document.createElement("span");
  ts.className = "timestamp";
  ts.textContent = timestamp();
  bubble.appendChild(ts);

  row.append(avatar, bubble);
  chat.appendChild(row);
  persist();
  scrollToBottom();
}

function addDirectionsCard(payload) {
  const steps = Array.isArray(payload.instructions)
    ? payload.instructions
    : typeof payload.instructions === "string"
      ? [payload.instructions]
      : [];
  addCard(`
    <div><strong>🗺️ Directions</strong></div>
    <div>${payload.origin} → ${payload.destination}</div>
    <div>Distance: ${payload.distance_km != null ? payload.distance_km.toFixed(1) + " km" : "—"
    } | ETA: ${payload.eta_min != null ? Math.round(payload.eta_min) + " min" : "—"
    }${payload.delay_min ? ` (+${Math.round(payload.delay_min)} min traffic)` : ""}</div>
    ${payload.maps_url ? `
  <div class="map-card">
    <iframe
      src="${payload.maps_url}&output=embed"
      width="100%"
      height="200"
      style="border:0; border-radius:10px;"
      allowfullscreen=""
      loading="lazy"
      referrerpolicy="no-referrer-when-downgrade">
    </iframe>
    <div style="margin-top:6px; text-align:center">
      <a href="${payload.maps_url}" target="_blank" rel="noopener" class="btn-map">Open in Google Maps</a>
    </div>
  </div>` : ""}
    }
    ${steps.length
      ? `<ol style="margin:8px 0 0 18px">${steps
        .slice(0, 8)
        .map((s) => `<li>${s}</li>`)
        .join("")}</ol>`
      : ""
    }
  `);
}

function addTrafficCard(payload) {
  const speedText =
    payload.current_speed_kmh != null && payload.free_flow_speed_kmh != null
      ? ` | Speed: ${Math.round(payload.current_speed_kmh)} km/h (free-flow ${Math.round(
        payload.free_flow_speed_kmh
      )} km/h)`
      : "";
  const congestionText =
    payload.congestion_level != null ? ` | Level ${payload.congestion_level}` : "";
  addCard(`
    <div><strong>🚦 Traffic</strong></div>
    <div>${payload.origin} → ${payload.destination}</div>
    <div>Status: ${payload.status || "Available"}${congestionText}${speedText}${payload.delay_min != null ? ` | Delay: ${Math.round(payload.delay_min)} min` : ""
    }</div>
  `);
}

function handleRasaResponse(messages) {
  messages.forEach((msg) => {
    if (msg.text) addMessage(msg.text, "bot");

    const payload = msg.custom || msg.json_message || null;
    if (!payload || typeof payload !== "object") return;

    if (payload.type === "directions") {
      addDirectionsCard(payload);
    } else if (payload.type === "traffic") {
      addTrafficCard(payload);
    } else if (Array.isArray(payload.stations)) {
      addStationCards(payload.stations, { show_availability: !!payload.show_availability });
    }
  });
}

/* ---------- Geolocation + init ---------- */
async function getUserLocation() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) return reject(new Error("Geolocation not supported"));
    navigator.geolocation.getCurrentPosition(resolve, reject);
  });
}

async function sendLocationToRasa() {
  const payload = {
    sender: "user",
    message: "hello",
    metadata: userLocation,
  };
  const res = await fetch("http://localhost:5005/webhooks/rest/webhook", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (Array.isArray(data) && data.length) handleRasaResponse(data);
}

function persist() {
  localStorage.setItem("chatHistory", chat.innerHTML);
}

function attachStationDirectionsHandlers() {
  document.querySelectorAll(".station-directions").forEach((btn) => {
    if (btn.dataset.bound) return;       // avoid duplicate binding
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const sid = btn.getAttribute("data-id");
      addMessage("Get Directions", "user");
      sendMessage(`/get_directions{"station_id":"${sid}"}`);
    });
  });
}

window.onload = async () => {
  const stored = localStorage.getItem("chatHistory");
  if (stored) chat.innerHTML = stored;
  attachStationDirectionsHandlers();

  // measure single-line height once
  userInput.style.height = 'auto';
  baseInputHeight = userInput.scrollHeight;

  addMessage("Hello! Welcome to Melbourne EV Charging Assistant! ⚡", "bot");
  addMessage("📍 **Getting your location…**", "bot");
  addMessage("Please accept the location permission when prompted.", "bot");

  try {
    const loc = await getUserLocation();
    userLocation = { lat: loc.coords.latitude, lng: loc.coords.longitude };
    addMessage("✅ **Location detected!** Now I can help you find the best charging options.", "bot");
    await sendLocationToRasa();
  } catch (e) {
    addMessage(
      "⚠️ Location access denied. Please type your suburb name (e.g., 'Richmond') to continue.",
      "bot"
    );
  }
};

/* ---------- Messaging ---------- */
async function sendMessage(message) {
  typingIndicator.classList.remove("hidden");
  try {
    const payload = { sender: "user", message, metadata: userLocation || {} };
    const response = await fetch("http://localhost:5005/webhooks/rest/webhook", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!Array.isArray(data) || data.length === 0) {
      addMessage("Sorry, I didn’t understand that.", "bot");
    } else {
      handleRasaResponse(data);
    }
  } catch (err) {
    addMessage("Server error. Please try again.", "bot");
  } finally {
    typingIndicator.classList.add("hidden");
  }
}

/* Form + input behaviours */
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const message = userInput.value.trim();
  if (!message) return;

  addMessage(message, "user");
  userInput.value = "";
  userInput.style.height = baseInputHeight + 'px';   // reset height
  await sendMessage(message);
});


userInput.addEventListener("input", () => {
  if (baseInputHeight == null) {
    userInput.style.height = 'auto';
    baseInputHeight = userInput.scrollHeight;
  }

  // reset to single-line then grow only if content actually wraps
  userInput.style.height = baseInputHeight + 'px';
  const needed = Math.min(MAX_INPUT_HEIGHT, userInput.scrollHeight);
  if (needed > baseInputHeight + 2) {           // small threshold avoids first-char jump
    userInput.style.height = needed + 'px';
  }
});

userInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

/* Clear chat */
clearBtn.addEventListener("click", () => {
  if (!confirm("Clear all chat messages?")) return;
  chat.innerHTML = "";
  localStorage.removeItem("chatHistory");
  addMessage("Chat cleared. How can I help you now?", "bot");
});