from flask import Blueprint, request, jsonify
from ..services.ml_engine import predict, train_model, META_PATH
from ..services import airports
import hmac, json, os, re, traceback
from datetime import datetime

predict_bp = Blueprint("predict", __name__)

@predict_bp.route("/predict", methods=["POST"])
def predict_flight():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400
    required = ["flight_number", "dep_airport", "arr_airport", "airline", "date", "dep_time"]
    missing  = [k for k in required if not data.get(k)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
    data = {k: str(data[k]).strip().upper() for k in required}
    if not re.fullmatch(r"[A-Z0-9]{2}\s?\d{1,4}[A-Z]?", data["flight_number"]):
        return jsonify({"error": "Please enter a valid flight number, e.g. AA100."}), 400
    if not re.fullmatch(r"[A-Z0-9]{2}", data["airline"]):
        return jsonify({"error": "Airline must be a 2-character code, e.g. AA."}), 400
    if not (re.fullmatch(r"[A-Z]{3}", data["dep_airport"]) and re.fullmatch(r"[A-Z]{3}", data["arr_airport"])):
        return jsonify({"error": "Airports must be 3-letter codes, e.g. JFK."}), 400
    try:
        datetime.strptime(f"{data['date']} {data['dep_time']}", "%Y-%m-%d %H:%M")
    except ValueError:
        return jsonify({"error": "Date must be YYYY-MM-DD and time HH:MM."}), 400

    try:
        result = predict(
            flight_number=data["flight_number"],
            dep_airport  =data["dep_airport"],
            arr_airport  =data["arr_airport"],
            airline      =data["airline"],
            date_str     =data["date"],
            dep_time     =data["dep_time"],
        )
        result["dep_name"] = airports.lookup(data["dep_airport"])["name"]
        result["arr_name"] = airports.lookup(data["arr_airport"])["name"]
        return jsonify({"success": True, "data": result})
    except Exception:
        traceback.print_exc()
        return jsonify({"error": "Prediction engine error"}), 500

@predict_bp.route("/model/status", methods=["GET"])
def model_status():
    if META_PATH.exists():
        meta = json.loads(META_PATH.read_text())
        return jsonify({"trained": True, "meta": meta})
    return jsonify({"trained": False})

@predict_bp.route("/model/train", methods=["POST"])
def retrain():
    # Retraining is slow and memory-heavy, so only the site owner may trigger it.
    admin_token = os.environ.get("ADMIN_TOKEN")
    supplied = request.headers.get("X-Admin-Token", "")
    if not admin_token or not hmac.compare_digest(supplied, admin_token):
        return jsonify({"error": "Not authorized"}), 403
    # Training needs a year of flight records and runs offline:
    # python scripts/train_delay_model.py <folder of BTS zip files>
    return jsonify({"success": True, "meta": train_model(),
                    "note": "Retrain offline with backend/scripts/train_delay_model.py"})
