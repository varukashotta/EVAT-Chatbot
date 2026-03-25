# EVAT – Electric Vehicle Adoption Tools

A conversational AI chatbot designed to help electric vehicle users find charging stations, plan routes, and get relevant information using the Rasa framework.

## 🌐 Live Demo
The EVAT Chatbot is now deployed online and accessible via Netlify: https://t2-rasa-chatbpt-2025.netlify.app/

**About the live demo:**
- Users can access the chatbot directly from a browser without local setup.
- All main flows—Route Planning, Emergency Charging, and Charging Preferences—are fully interactive.
- The bot detects the user’s location (via browser geolocation) for a starting point.
- Station cards display filtered charging stations with Get Directions, Check Availability, and Compare Options buttons.
- Real-time traffic information is provided via TomTom APIs for accurate route planning.
- The live demo is ideal for testing and showcasing the chatbot functionality.

---

## 🚀 Features
- **Location-based charging station finder**
- **Emergency Charging mode** → asks for car model or connector type (CHAdeMO, Tesla Model 3, Type 2, CCS) and returns compatible nearby stations (within ~10 km).
- **Real-time, traffic-aware routing** (powered by TomTom API)
- **Charging preferences with station cards** → users can filter by:
  - Fastest (high-speed chargers)
  - Cheapest (budget-friendly)
  - Premium (well-equipped/high-rated)
- **Interactive station cards** with:
  - Station details (name, suburb, coordinates, charger type)
  - Buttons for Get Directions, Check Availability, and Compare Options
  - Google Maps integration for live traffic and routes
- **Web-based chat interface**

---

## 📁 Project Structure
EVAT_Chatbot/  
├── rasa/                 # Rasa chatbot configuration  
│   ├── domain.yml        # Intent, entity, and action definitions  
│   ├── config.yml        # NLU pipeline and policy configuration  
│   ├── endpoints.yml     # API endpoints  
│   ├── credentials.yml   # Authentication settings  
│   ├── actions/          # Custom action implementations  
│   └── data/             # Training data (intents, stories, rules)  
├── backend/              # Core business logic  
│   ├── real_time_apis.py # TomTom client used by actions  
│   └── utils/            # Utility functions  
├── frontend/             # Web interface  
│   ├── index.html        # Main chat interface  
│   ├── script.js         # Frontend logic  
│   └── style.css         # Styling  
├── data/                 # Datasets  
│   └── raw/              # CSV files (charging stations, coordinates)  
├── ml/                   # Machine learning models  
│   ├── classification.py # Station classification  
│   ├── regression.py     # ETA prediction  
│   └── README.md         # ML documentation  
├── config/               # Configuration files  
├── README.md             # Project overview  
├── requirements.txt      # Dependencies  
└── .gitignore            # Git ignore rules

---

## 🧩 How to Use the Chatbot (Local Setup)

1. Quick setup  
- Navigate to the project directory: `cd EVAT_Chatbot`  
- Create a virtual environment: `python -m venv rasa_env`  
- Activate the virtual environment:  
  - On Mac/Linux: `source rasa_env/bin/activate`  
  - On Windows (PowerShell): `.\rasa_env\Scripts\Activate`  
- Install requirements: `pip install -r requirements.txt`

2. Train the Rasa model  
- Navigate to the Rasa folder: `cd rasa`  
- Train the model: `rasa train`

3. Run Servers  
- Tab 1: Run Actions Server  
  - On Mac/Linux:  
    `source ../rasa_env/bin/activate`  
    `cd rasa`  
    `rasa run actions --port 5055`  
  - On Windows:  
    `.\rasa_env\Scripts\Activate`  
    `cd rasa`  
    `rasa run actions --port 5055`  
- Tab 2: Run Rasa Core Server  
  - On Mac/Linux:  
    `source ../rasa_env/bin/activate`  
    `cd rasa`  
    `rasa run --enable-api --cors "*"`  
  - On Windows:  
    `.\rasa_env\Scripts\Activate`  
    `cd rasa`  
    `rasa run --enable-api --cors "*"`

4. Frontend setup  
- Navigate to the frontend folder: `cd frontend`  
- Start a local server: `python -m http.server 8080` (or `python3 -m http.server 8080` on some systems)  
- Open the application in your browser: `http://localhost:8080`  
- The frontend (`index.html`) communicates with: `http://localhost:5005/webhooks/rest/webhook`

**Note:**
- Windows users should run commands in **PowerShell**.  
- Mac/Linux users should run commands in **Terminal**.  
- Ensure that **Python 3.8+** is installed and accessible in your system path.  
- Always activate the virtual environment before running servers.

---

## 🎮 Interact with the Bot
When you start chatting, the bot detects your location or asks for your starting suburb. You then choose a destination.

Main conversation flows:

1. **🗺️ Route Planning – plan charging stops for a journey**  
- Bot suggests chargers within ~10 km of current location.

2. **🚨 Emergency Charging – find nearest compatible station when battery is low**  
Flow:  
1. User selects Emergency Charging.  
2. Bot asks: “Tell me your car model or connector type (CHAdeMO, Type 2, CCS, Tesla Model 3, etc.)”  
3. Bot finds compatible stations within ~10 km of current location.  
4. Station cards displayed with Get Directions (Google Maps), Check Availability, Compare Options.

3. **⚡ Charging Preferences – Filter Chargers by Preference**  
The user selects one of the following preferences: Cheapest, Premium, Fastest

Flow:  
1. The chatbot asks the user to select a preference (Cheapest, Premium, or Fastest).  
2. The system determines the user’s location (from browser geolocation).  
3. Based on the selected preference, the chatbot filters available charging stations near the user’s location.  
4. The filtered results are displayed as station cards, each containing:  
   - Station name, suburb, and charger type  
   - Distance from user’s location  
   - Buttons for: Get Directions (opens Google Maps with real-time traffic), Check Availability, Compare Options  
5. The user can select any station card to proceed with navigation or availability checks.

---

## ⚙️ How It Works
- Station resolution: Names/suburbs → coordinates via CSV dataset.  
- Routing & traffic: TomTom API for distance, ETA, and traffic.  
- Station cards: Show details + CTA buttons (directions, availability, compare).  
- Frontend: Browser geolocation (lat/lon) sent as metadata → integrated into search.

---

## 🖥️ Frontend (Current State)
- Chat UI wired to Rasa REST webhook.  
- Station cards implemented (previously text-only).  
- Buttons link directly to Google Maps with live traffic.

**Current limitations:**
- Only works in Melbourne Metropolitan area.

---

## 📍 Data Sources
- `data/raw/Co-ordinates.csv` → suburb coordinates  
- `data/raw/charger_info_mel.csv` → charging station details
  
---
## Next Step
- Improve Searching Algorithm
- Optimize Rasa to pick up most entities
- Expand the dataset to cover all charging stations in Melbourne
- Enhance UX/UI
  
