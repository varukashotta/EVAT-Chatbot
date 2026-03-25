from flask import Flask, request, jsonify, render_template_string, session
import os, math, time, urllib.parse
import evat_core as ev

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-me")

STATIONS = None
PROFILE  = None

def warm():
    global STATIONS, PROFILE
    ev.ensure_dataset()
    STATIONS = ev.load_stations(prefer_enriched=True)
    PROFILE  = ev.load_profile(ev.USER_ID)

if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    warm()

ENV_FAST_DEFAULT   = os.getenv("FAST_TRIP", "1") == "1"
ENV_SIGHT_DEFAULT  = os.getenv("ENABLE_SIGHTSEEING", "1") == "1"
TRIP_TIMEOUT_SEC   = int(os.getenv("TRIP_TIMEOUT_SEC", "25"))

def get_flags():
    fast = session.get("fast_trip", ENV_FAST_DEFAULT)
    sight = session.get("sightseeing", ENV_SIGHT_DEFAULT)
    return fast, sight

def set_flags(fast: bool = None, sight: bool = None):
    if fast is not None:
        session["fast_trip"] = bool(fast)
    if sight is not None:
        session["sightseeing"] = bool(sight)

def get_history():
    return session.setdefault("history", [])

def add_to_history(role: str, text: str, html: str = None):
    hist = get_history()
    hist.append({"role": role, "text": text, "html": html, "ts": int(time.time())})
    if len(hist) > 200:
        del hist[:len(hist) - 200]
    session["history"] = hist

def clear_history():
    session["history"] = []

def gmaps_search(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

def gmaps_dir(origin=None, destination=None, waypoints=None) -> str:
    base = "https://www.google.com/maps/dir/?api=1"
    q = {}
    if origin:
        q["origin"] = f"{origin[0]},{origin[1]}"
    if destination:
        q["destination"] = f"{destination[0]},{destination[1]}"
    if waypoints:
        wps = "|".join(f"{w[0]},{w[1]}" for w in waypoints[:9])
        q["waypoints"] = wps
    q["travelmode"] = "driving"
    return base + "&" + urllib.parse.urlencode(q, safe="|,")

HTML = """
<!doctype html>
<title>EVAT Chatbot</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  :root{--b:#e5e7eb;--bg:#fafafa;--fg:#111827}
  body{font-family:system-ui,Segoe UI,Helvetica,Arial,sans-serif;max-width:960px;margin:32px auto;padding:0 16px;color:var(--fg)}
  h1{margin:0 0 8px}
  .bar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:16px 0}
  .bar button, .bar label{padding:8px 12px;border:1px solid var(--b);background:white;border-radius:8px;cursor:pointer}
  .bar input[type="checkbox"]{margin-right:6px;vertical-align:middle}
  #thread{border:1px solid var(--b);border-radius:10px;background:var(--bg);padding:12px;min-height:320px;max-height:420px;overflow:auto}
  .msg{margin:0 0 12px}
  .who{font-weight:600}
  .ts{color:#6b7280;font-size:12px;margin-left:6px}
  #msg{width:100%;padding:12px;border-radius:10px;border:1px solid var(--b);margin-top:12px}
  .hint{color:#6b7280;font-size:14px}
  .hint code{background:#f3f4f6;padding:2px 6px;border-radius:6px}
  .assistant a{color:#1d4ed8;text-decoration:none}
  .assistant a:hover{text-decoration:underline}
  #scrollBtn{position:fixed;right:20px;bottom:24px;padding:10px 12px;border-radius:999px;border:1px solid var(--b);background:white;cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,.08);display:none}
  #status{display:none;gap:8px;align-items:center;margin:10px 0;color:#374151}
  .spinner{width:16px;height:16px;border:2px solid #d1d5db;border-top-color:#4b5563;border-radius:50%;animation:spin 1s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
</style>
<h1>EVAT Chatbot</h1>
<p class="hint">Try: <code>Where can I charge near Melbourne Airport?</code> or <code>from Geelong to Sydney</code></p>
<div class="bar">
  <label><input type="checkbox" id="fast"> Fast trip</label>
  <label><input type="checkbox" id="sight"> Sightseeing</label>
  <button onclick="clearHist()">Clear history</button>
  <span class="hint">Fast is quick (chargers only). Sightseeing is richer (adds POIs & route link).</span>
</div>

<div id="status"><span class="spinner"></span><span id="statusText">Searching…</span></div>

<div id="thread"></div>
<input id="msg" placeholder="Type your message and press Enter" autofocus />
<button id="scrollBtn" title="Scroll to latest">↓</button>

<script>
const input  = document.getElementById('msg');
const thread = document.getElementById('thread');
const fastCB = document.getElementById('fast');
const sightCB= document.getElementById('sight');
const scrollBtn = document.getElementById('scrollBtn');
const statusBar = document.getElementById('status');
const statusText= document.getElementById('statusText');
let waitTimer = null;

function setStatus(text){
  if(text){
    statusText.textContent = text;
    statusBar.style.display = 'flex';
  }else{
    statusBar.style.display = 'none';
    statusText.textContent = '';
  }
}

function renderThread(items){
  thread.innerHTML = '';
  for(const m of items){
    const d = document.createElement('div'); d.className='msg ' + (m.role==='assistant'?'assistant':'');
    const who = document.createElement('span'); who.className='who';
    who.textContent = (m.role==='user'?'You':'Bot') + ': ';
    const content = document.createElement('span');
    if(m.html && m.role==='assistant'){ content.innerHTML = m.html; } else { content.textContent = m.text || ''; }
    const ts = document.createElement('span'); ts.className='ts';
    ts.textContent = new Date(m.ts*1000).toLocaleString();
    d.appendChild(who); d.appendChild(content); d.appendChild(ts);
    thread.appendChild(d);
  }
  maybeShowScrollBtn();
}
function maybeShowScrollBtn(){
  const nearBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 6;
  scrollBtn.style.display = nearBottom ? 'none' : 'block';
}
thread.addEventListener('scroll', maybeShowScrollBtn);
scrollBtn.addEventListener('click', () => {
  thread.scrollTo({top: thread.scrollHeight, behavior: 'smooth'});
  scrollBtn.style.display = 'none';
});

async function refresh(){
  const s = await (await fetch('/api/settings')).json();
  fastCB.checked = !!s.fast_trip;
  sightCB.checked = !!s.sightseeing;
  const r = await fetch('/api/history');
  const j = await r.json();
  renderThread(j.history || []);
  thread.scrollTop = thread.scrollHeight;
}

async function clearHist(){
  await fetch('/api/clear', {method:'POST'});
  await refresh();
}

fastCB.addEventListener('change', async () => {
  await fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'},
           body: JSON.stringify({fast_trip: fastCB.checked})});
});
sightCB.addEventListener('change', async () => {
  await fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'},
           body: JSON.stringify({sightseeing: sightCB.checked})});
});

async function send(m){
  if(!m.trim()) return;
  setStatus('Searching…');
  clearTimeout(waitTimer);
  waitTimer = setTimeout(()=> setStatus('Still working…'), 8000);
  input.disabled = true;

  try{
    const res = await fetch('/api/chat', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message:m})
    });
    await refresh();
    if(!res.ok){ setStatus('Something went wrong.'); setTimeout(()=> setStatus(''), 2500); }
    else{ setStatus(''); }
  }catch(e){
    setStatus('Network error. Check the server.'); setTimeout(()=> setStatus(''), 3000);
  }finally{
    clearTimeout(waitTimer);
    input.disabled = false; input.value = ''; input.focus();
  }
}
input.addEventListener('keydown', e => { if(e.key==='Enter'){ send(input.value); } });
refresh();
</script>
"""

@app.get("/")
def index():
    return render_template_string(HTML)

@app.get("/api/history")
def api_history():
    return jsonify(history=get_history())

@app.post("/api/clear")
def api_clear():
    clear_history()
    return jsonify(ok=True)

@app.get("/api/settings")
def api_get_settings():
    fast, sight = get_flags()
    return jsonify(fast_trip=fast, sightseeing=sight)

@app.post("/api/settings")
def api_set_settings():
    data = request.get_json(silent=True) or {}
    if "fast_trip" in data:
        set_flags(fast=bool(data["fast_trip"]))
    if "sightseeing" in data:
        set_flags(sight=bool(data["sightseeing"]))
    fast, sight = get_flags()
    return jsonify(ok=True, fast_trip=fast, sightseeing=sight)

@app.post("/api/chat")
def api_chat():
    data = request.get_json(silent=True) or {}
    msg = (data.get("message") or "").strip()
    if not msg:
        return jsonify(ok=False, error="Empty message"), 400

    add_to_history("user", msg)

    fast, sight = get_flags()
    use_sight = bool(sight)
    use_fast  = bool(fast and not use_sight)

    start_t = time.time()
    try:
        low = msg.lower()
        o_txt, d_txt = ev.parse_from_to(msg)
        is_trip = bool(o_txt and d_txt) or ("trip" in low and "to" in low)

        if is_trip:
            if not (o_txt and d_txt):
                reply = "Please say it like: from <origin> to <destination> (both in Australia)."
                add_to_history("assistant", reply, html=reply); return jsonify(ok=True, reply=reply)

            oc = ev.geocode_australia(o_txt); dc = ev.geocode_australia(d_txt)
            if not oc or not dc:
                reply = "Sorry, I couldn't geocode one of those places in Australia."
                add_to_history("assistant", reply, html=reply); return jsonify(ok=True, reply=reply)

            if use_sight:
                plans = ev.plan_dual_routes(oc, dc, PROFILE, STATIONS)
                if not plans:
                    reply = "Routing failed—try different places."
                    add_to_history("assistant", reply, html=reply); return jsonify(ok=True, reply=reply)
                shortest = plans["shortest"]; enhanced = plans["enhanced"]
                stops = enhanced.get("stops") or []
                wps = [(s["latitude"], s["longitude"]) for s in stops]
                route_url = gmaps_dir(origin=oc, destination=dc, waypoints=wps if wps else None)

                lines = []
                lines.append(f"<h3>Route A (shortest)</h3><div>≈ {round(shortest['distance_km'],1)} km.</div>")
                lines.append(f"<h3>Route B (chargers + sightseeing)</h3>"
                             f"<div>≈ {round(enhanced['distance_km'],1)} km, {len(stops)} stop(s). "
                             f"<a href=\"{route_url}\" target=\"_blank\" rel=\"noopener\">Open full route in Google Maps</a></div>")
                if stops:
                    lines.append("<ul>")
                    for i, s in enumerate(stops, 1):
                        kw = s.get("kw")
                        kwtx = f", ~{int(kw)} kW" if isinstance(kw, (int, float)) and math.isfinite(kw) else ""
                        lat, lon = s["latitude"], s["longitude"]
                        link = gmaps_search(lat, lon)
                        lines.append(
                            f"<li><b>{i}. {s['name']}</b> — at ≈{s.get('at_km','?')} km (detour {s['distance_km']} km{kwtx}) "
                            f"<a href=\"{link}\" target=\"_blank\" rel=\"noopener\">Open</a></li>"
                        )
                    lines.append("</ul>")
                sights = enhanced.get("sightseeing") or []
                if sights:
                    lines.append("<h4>Sightseeing near the route</h4><ul>")
                    for a in sights[:10]:
                        lat, lon = a["latitude"], a["longitude"]
                        link = gmaps_search(lat, lon)
                        near = f" (near {a.get('near_stop')})" if a.get('near_stop') else ""
                        lines.append(
                            f"<li>{a['name']} [{a['type']}] — {a['distance_km']} km off route{near} "
                            f"<a href=\"{link}\" target=\"_blank\" rel=\"noopener\">Open</a></li>"
                        )
                    lines.append("</ul>")
                html = "\n".join(lines)
                txt = "Trip with sightseeing planned."
                if time.time() - start_t > TRIP_TIMEOUT_SEC:
                    html += "<p><em>Note: this took a while. Turn on <b>Fast trip</b> for quicker results.</em></p>"
                add_to_history("assistant", txt, html=html)
                return jsonify(ok=True, reply=txt)

            # Fast mode
            plan = ev.plan_trip_with_chargers(oc, dc, PROFILE, STATIONS)
            if not plan:
                reply = "Routing failed—try different places."
                add_to_history("assistant", reply, html=reply); return jsonify(ok=True, reply=reply)

            stops = plan.get("stops") or []
            wps = [(s["latitude"], s["longitude"]) for s in stops]
            route_url = gmaps_dir(origin=oc, destination=dc, waypoints=wps if wps else None)

            lines = []
            lines.append(f"<div><b>Trip distance:</b> ≈ {round(plan['distance_km'],1)} km. "
                         f"<a href=\"{route_url}\" target=\"_blank\" rel=\"noopener\">Open full route in Google Maps</a></div>")
            if stops:
                lines.append("<ul>")
                for i, s in enumerate(stops, 1):
                    kw = s.get("kw")
                    kwtx = f", ~{int(kw)} kW" if isinstance(kw, (int, float)) and math.isfinite(kw) else ""
                    lat, lon = s["latitude"], s["longitude"]
                    lines.append(
                        f"<li><b>{i}. {s['name']}</b> — at ≈{s.get('at_km','?')} km (detour {s['distance_km']} km{kwtx}) "
                        f"<a href=\"{gmaps_search(lat,lon)}\" target=\"_blank\" rel=\"noopener\">Open</a></li>"
                    )
                lines.append("</ul>")
            else:
                lines.append("<div>No charging stops required (within range).</div>")

            html = "\n".join(lines)
            txt = "Trip planned (fast mode)."
            add_to_history("assistant", txt, html=html)
            return jsonify(ok=True, reply=txt)

        # Nearby
        poi = ev.extract_poi(msg) or msg
        coords = ev.geocode_australia(poi)
        if not coords:
            reply = f"I couldn't find '{poi}' in Australia. Try a suburb, landmark, or 'place, State'."
            add_to_history("assistant", reply, html=reply); return jsonify(ok=True, reply=reply)

        top3 = ev.personalize_rank(coords, PROFILE, STATIONS, want_k=3)
        lines = [f"<div><b>Nearest chargers to {poi}</b> ({coords[0]:.4f}, {coords[1]:.4f})</div>"]
        if not top3:
            lines.append("<div>None found.</div>")
        else:
            lines.append("<ul>")
            for s in top3:
                extras=[]
                if s.get("kw"): extras.append(f"~{int(s['kw'])} kW")
                if s.get("plug_types"):
                    plugs = s["plug_types"] if isinstance(s["plug_types"], list) else [s["plug_types"]]
                    extras.append(", ".join(plugs))
                etxt = f" ({'; '.join(extras)})" if extras else ""
                lat, lon = s["latitude"], s["longitude"]
                lines.append(
                    f"<li>{s['name']} — {s['distance_km']} km{etxt} "
                    f"<a href=\"{gmaps_search(lat,lon)}\" target=\"_blank\" rel=\"noopener\">Open</a></li>"
                )
            lines.append("</ul>")
        html = "\n".join(lines)
        txt = "Nearby results shown."
        add_to_history("assistant", txt, html=html)
        return jsonify(ok=True, reply=txt)

    except Exception as e:
        reply = f"Unexpected error: {e}"
        add_to_history("assistant", reply, html=reply)
        return jsonify(ok=False, error=reply), 500

if __name__ == "__main__":
    app.run(debug=True)  # or: app.run(debug=True, use_reloader=False)
