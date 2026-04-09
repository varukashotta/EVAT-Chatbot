# EVAT – Electric Vehicle Adoption Tools

A conversational AI chatbot that helps electric vehicle users in Melbourne find charging stations, plan routes, and get charging information. Built with Rasa and deployed on Railway.

---

## Deploy on Railway

### Prerequisites

- A [Railway](https://railway.app) account (GitHub login works)
- This repo pushed to your GitHub account

### 1. Deploy the Actions Server

1. Go to Railway → **New Project** → **Deploy from GitHub Repo** → select this repo
2. Click the service → **Settings** → set **Dockerfile Path** to `Dockerfile.actions`
3. Go to **Settings** → **Networking** → **Generate Domain**
4. Set port to `5055`
5. Wait for the build to finish (~2-3 min)
6. Copy the generated URL (e.g. `https://your-actions-service.up.railway.app`)

### 2. Update the Rasa Endpoints

In `rasa/endpoints.docker.yml`, set the action server URL to the one from step 1:

```yaml
action_endpoint:
  url: "https://your-actions-service.up.railway.app/webhook"
```

Commit and push this change.

### 3. Deploy the Rasa Core Server

1. In the same Railway project, click **+ New** → **GitHub Repo** → select this repo again
2. Click the service → **Settings** → set **Dockerfile Path** to `Dockerfile.rasa`
3. Go to **Settings** → **Networking** → **Generate Domain**
4. Railway sets the port automatically via `$PORT` — no manual config needed
5. Wait for the build (~5-10 min — includes model training)
6. Copy the generated URL (e.g. `https://your-rasa-service.up.railway.app`)

### 4. Point the Frontend

Update the Rasa webhook URL in both frontend files:

**`frontend/script.js`** — replace all occurrences of the webhook URL:
```js
const response = await fetch("https://your-rasa-service.up.railway.app/webhooks/rest/webhook", {
```

**`frontend/chat.html`** — same replacement:
```js
const response = await fetch("https://your-rasa-service.up.railway.app/webhooks/rest/webhook", {
```

Commit and push, then deploy the frontend on Netlify (or any static host).

### 5. Environment Variables (Optional)

Add these in Railway → service → **Variables** if you want real-time features:

| Variable | Required | Purpose |
|----------|----------|---------|
| `TOMTOM_API_KEY` | No | Enables real-time traffic and routing. App works without it using static CSV data. |

---

## Docker Setup (Local)

Run both services locally with Docker Compose:

```bash
cp .env.example .env
docker-compose up --build
```

This starts:
- **Rasa Core** on `http://localhost:5005`
- **Action Server** on `http://localhost:5055`

If you have a pre-trained model in `rasa/models/`, it will be used automatically. Otherwise, the Rasa image trains one during build.

Then serve the frontend:

```bash
cd frontend
python3 -m http.server 8080
```

Open `http://localhost:8080`.

---

## Local Setup (Without Docker)

```bash
python -m venv rasa_env
source rasa_env/bin/activate   # Windows: .\rasa_env\Scripts\Activate
pip install -r requirements.txt
cd rasa && rasa train
```

Run in separate terminals:

```bash
# Terminal 1 — Actions Server
cd rasa && rasa run actions --port 5055

# Terminal 2 — Rasa Core
cd rasa && rasa run --enable-api --cors "*"

# Terminal 3 — Frontend
cd frontend && python3 -m http.server 8080
```

Open `http://localhost:8080`.

---

## Project Structure

```
EVAT-Chatbot/
├── rasa/                        # Rasa chatbot
│   ├── domain.yml               # Intents, entities, actions, slots
│   ├── config.yml               # NLU pipeline and policies
│   ├── endpoints.yml            # Local action endpoint
│   ├── endpoints.docker.yml     # Docker/Railway action endpoint
│   ├── credentials.yml          # Channel config
│   ├── actions/                 # Custom action server code
│   └── data/                    # NLU training data, stories, rules
├── backend/                     # TomTom API client
├── frontend/                    # Chat web interface
├── data/raw/                    # CSV datasets (stations, coordinates)
├── Dockerfile.rasa              # Rasa Core container
├── Dockerfile.actions           # Action server container
├── docker-compose.yml           # Local multi-service setup
├── requirements.txt             # Full Python dependencies
└── requirements.actions.txt     # Action server dependencies only
```

---

## Data Sources

- `data/raw/charger_info_mel.csv` — 256 charging stations in Melbourne
- `data/raw/Co-ordinates.csv` — 198 suburb coordinates for location lookup
