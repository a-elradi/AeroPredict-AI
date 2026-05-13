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
