import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from app import create_app
from app.services.ml_engine import train_model

app = create_app()

import os

if __name__ == "__main__":
    # Only train locally. Vercel/Production should use the pre-trained model.
    if not os.environ.get("VERCEL"):
        print("[AeroPredict] Local environment detected. Initializing ML model...")
        train_model()
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
