# AeroPredict AI: Flight Delay Prediction Platform

AeroPredict is a full-stack intelligent aviation platform designed to predict flight statuses (On-Time, Delayed, or Cancelled) using advanced Machine Learning. This project was architected for global aviation management use, prioritizing accuracy, premium UI/UX, and real-world scalability.

---

## 🏛️ System Architecture

The platform follows a modern decoupled architecture:

*   **Core Logic (Backend)**: Built with **Python** and **Flask**. It handles the ML inference pipeline, feature engineering, and provides RESTful APIs for the dashboard.
*   **Intelligence (AI/ML)**: Powered by an **XGBoost** ensemble classifier with **Platt Scaling** for probability calibration.
*   **Presentation (Frontend)**: A vanilla **JavaScript** and **CSS3** dashboard utilizing **Chart.js** for real-time data visualization and a premium aviation-themed aesthetic.
*   **Infrastructure**: Designed for containerization (Docker) and deployment on cloud providers like Render, AWS, or Azure.

---

## 🧠 Machine Learning Engine

### 1. The Dataset
The model is trained on a statistically significant corpus (120,000+ flights) derived from **BTS (Bureau of Transportation Statistics)** and **FAA (Federal Aviation Administration)** data. It incorporates:
*   Historical carrier on-time performance (OTP).
*   Airport congestion indices.
*   National Aviation System (NAS) delay profiles.

### 2. Feature Engineering
We transform raw flight data into high-dimensional feature vectors:
*   **Cyclical Temporal Encoding**: Month, day-of-week, and hour are encoded using sine/cosine transforms to preserve their periodic nature.
*   **Congestion Indexing**: Departure and arrival airports are assigned dynamic congestion scores based on FAA hub pressure data.
*   **Environmental Risk**: A seasonal and diurnal weather severity index is mapped based on historical METAR patterns.
*   **Interaction Features**: Cross-features like `Congestion * Weather` are calculated to capture non-linear relationships.

### 3. Model & Training
*   **Algorithm**: Gradient Boosted Trees (XGBoost).
*   **Multi-Class Classification**: Softmax objective for 3 classes (On-time, Delayed, Cancelled).
*   **Calibration**: We apply **Platt Scaling** (via `CalibratedClassifierCV`) to ensure that the output percentages are statistically meaningful probabilities, not just raw scores.
*   **Evaluation Metrics**: The model is validated using **Log-Loss** and **Stratified F1-Score** to ensure robustness against class imbalances (especially cancellations).

---

## ✈️ AI Concierge — Journey Planner

`POST /api/concierge/journey-plan` builds a door-to-gate timeline for a passenger.

It combines:
*   **Live flight data** (AeroDataBox via RapidAPI): scheduled time, terminal, gate, check-in desk, status
*   **Live airport weather** (aviationweather.gov METAR, free)
*   **The AeroPredict delay model**
*   **Claude AI**, which personalizes the plan for the passenger's cabin, bags, status, preferences and language

Every source is optional. Without `RAPIDAPI_KEY` the passenger's typed flight details are used, and without
`ANTHROPIC_API_KEY` a rule-based plan is returned. Each response lists its `data_sources` and marks estimated
times with `is_estimate`.

Example request:

```json
{
  "flight_number": "TK1", "date": "2026-11-02",
  "dep_airport": "IST", "arr_airport": "JFK", "dep_time": "13:30",
  "starting_point": "Taksim, Istanbul", "transport_mode": "taxi",
  "cabin": "economy", "checked_bags": 1, "loyalty_tier": "none",
  "online_check_in": false, "fast_track": false,
  "preferences": ["dining", "prayer_room"], "language": "en"
}
```

`GET /api/concierge/status` shows which data sources are switched on.

### Wayfinding — amenities near your gate

`GET /api/concierge/wayfinding?airport=IST&gate=B4&categories=coffee,dining,toilets`

Returns the nearest coffee shops, food, toilets, ATMs, prayer rooms, lounges, pharmacies, drinking water,
charging points and shops, sorted by estimated walking distance from the gate. Data comes free from
OpenStreetMap through the Overpass API (no key needed) and is cached for 24 hours per airport.
Coverage depends on how well each airport is mapped in OpenStreetMap.

Airport maps are saved in `backend/data/osm/` so answers are instant. The **Refresh airport maps**
GitHub Action updates them on the 1st of every month; run it by hand from the Actions tab
(optionally with specific airport codes). Airports without a saved map are downloaded live.

### Baggage risk

`POST /api/concierge/baggage-risk` with `{"route": ["IST","FRA","JFK"], "connections": [{"duration": "under_1h", "airline_change": true}], "checked_bags": 1, "date": "2026-07-15"}`

Returns the estimated chance a checked bag is delayed or lost, the factors behind it, what to do before
and during the trip, the steps to take if the bag doesn't arrive, and Montreal Convention claim deadlines.
Estimates use industry-wide rates from SITA's Baggage IT Insights reports; no external API is called.

### Delay & cancellation rights

`POST /api/concierge/rights` with `{"origin": "IST", "destination": "LHR", "airline": "TK", "disruption": "cancellation", "cause": "airline", "notice_days": 1}`

Works out which rules apply (EU261, UK261, Türkiye SHY-YOLCU, US DOT refund rules, Canada APPR, or the
Montreal Convention), the cash compensation that may be owed, care and refund rights, what to say at the
desk, rebooking options and what evidence to keep. General guidance, not legal advice.

### Delay model and automatic retraining

The delay model (`models/delay_model.ubj` plus history tables) is trained on two years of US Bureau of
Transportation Statistics flight records and hourly airport weather from Iowa State University's IEM archive.
Two held-out months are used to test it; results are saved in `models/model_meta.json` and shown on the
Analytics page.

The **Retrain delay model** GitHub Action runs on the 12th of January, April, July and October (or by hand
from the Actions tab). It downloads the newest data, retrains, and publishes the model only if
`backend/scripts/check_model.py` passes and all tests pass with it; otherwise the live model is kept and the
run fails, so GitHub emails you.

To retrain on your own computer:

```bash
cd backend
python scripts/download_training_data.py ../data --months 24
python scripts/train_delay_model.py --flights ../data/flights --weather ../data/weather --out ../new_model
python scripts/check_model.py ../new_model --current ../models/model_meta.json
```

### Coverage outside the US

For flights without US history, predictions start from the most specific official data available, then apply
the model's time-of-day, season, holiday and weather effects:

| Data | Coverage | Refreshed |
|---|---|---|
| UK CAA punctuality statistics | every airline on every route to/from 25 UK airports | monthly (`route-stats.yml`) |
| ANAC Brazil VRA | every flight to/from/within Brazil, flight by flight | monthly (`route-stats.yml`) |
| Airportal (South Korea) | every flight at Korea's 15 airports, flight by flight | monthly (`route-stats.yml`) |
| Airport Authority Hong Kong | every flight at Hong Kong, last ~90 days, flight by flight | monthly (`route-stats.yml`) |
| Avinor flight data (Norway) | every flight at Norway's airports, saved daily (`collect-flights.yml`) | monthly (`route-stats.yml`) |
| MLIT Japan | national on-time record of the 10 main Japanese airlines (domestic) | monthly check, published yearly |
| BITRE Australia on-time performance | every major airline on every Australian domestic route, plus each airline's national record | monthly (`route-stats.yml`) |
| EUROCONTROL airport punctuality | ~110 European and Turkish airports, plus country averages | weekly (`reference-data.yml`) |
| Airport weather forecasts (TAF) | any airport worldwide, flights in the next ~30 hours | live |

Where there is no data for the route, the airline's overall record across all of the above (156 airlines,
plus US airlines' BTS records) is used, and it also adjusts the airport-punctuality estimates. Routes with
none of these are labelled "Estimate only".

### Configuration

Set these environment variables (see `.env.example`). On Render: **Dashboard → your service → Environment**.

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude AI personalization |
| `RAPIDAPI_KEY` | Live flight data (AeroDataBox) |
| `ADMIN_TOKEN` | Required to call `POST /api/model/train` (header `X-Admin-Token`) |
| `JOURNEY_PLAN_LIMIT_PER_HOUR` | Journey plans per visitor per hour (default 10) |

### Tests

```bash
cd backend
pip install -r requirements.txt pytest
python -m pytest tests
```

---

## 🚀 Deployment Strategy

As a senior engineer, you can deploy this in several ways:

### Option A: Cloud PaaS (Recommended for Demo)
1.  **Render/Railway**: 
    *   Connect your GitHub repository.
    *   Set Build Command: `pip install -r backend/requirements.txt`
    *   Set Start Command: `gunicorn --bind 0.0.0.0:$PORT backend.run:app`
2.  **Frontend**: If you want to host the frontend separately (e.g., Vercel), simply point the API URL in `app.js` to your deployed backend.

### Option B: Dockerized Microservices
I have designed the structure to be easily Dockerized. You can create a `Dockerfile` for the backend:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "backend.run:app"]
```

---

## 🛠️ Technology Stack
*   **Backend**: Python, Flask, Gunicorn
*   **AI/ML**: XGBoost, Scikit-learn, Pandas, Joblib
*   **Frontend**: HTML5, CSS3 (Vanilla), JavaScript, Chart.js
*   **Standards**: IATA Airport/Airline coding, FAA OPSNET alignment

---

## 📁 Folder Structure
```text
AeroPredict/
├── backend/            # Flask API & ML Logic
├── frontend/           # Dashboard UI
├── models/             # Serialized ML Models (Joblib)
├── notebooks/          # Research & EDA
└── data/               # Datasets
```

---

## 🎓 Academic Contribution
This project demonstrates:
1.  Application of **Ensemble Learning** in time-sensitive logistics.
2.  **Full-Stack Integration** of ML models into reactive web interfaces.
3.  **Domain Knowledge** application of IATA and FAA aviation standards.


