from flask import Blueprint, request, jsonify
from ..services.ml_engine import predict, train_model, META_PATH
import json, traceback

predict_bp = Blueprint("predict", __name__)

@predict_bp.route("/predict", methods=["POST"])
def predict_flight():
    data = request.get_json(force=True)
    required = ["flight_number", "dep_airport", "arr_airport", "airline", "date", "dep_time"]
    missing  = [k for k in required if not data.get(k)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    try:
        result = predict(
            flight_number=data["flight_number"],
            dep_airport  =data["dep_airport"],
            arr_airport  =data["arr_airport"],
            airline      =data["airline"],
            date_str     =data["date"],
            dep_time     =data["dep_time"],
        )
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
    try:
        meta = train_model(force=True)
        return jsonify({"success": True, "meta": meta})
    except Exception:
        traceback.print_exc()
        return jsonify({"error": "Training failed"}), 500
