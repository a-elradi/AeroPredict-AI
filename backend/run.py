import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from app import create_app
from app.services.ml_engine import train_model

app = create_app()

if __name__ == "__main__":
    print("[AeroPredict] Initializing ML model...")
    train_model()          # trains on first run, loads from disk thereafter
    app.run(host="0.0.0.0", port=5000, debug=False)
