import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from app import create_app
from app.services.ml_engine import train_model

app = create_app()

import os

if __name__ == "__main__":
    # SKIP training in cloud (Render/Vercel) to save memory. 
    # Only train locally on your laptop.
    is_cloud = os.environ.get("RENDER") or os.environ.get("VERCEL")
    
    if not is_cloud:
        print("[AeroPredict] Local environment detected. Initializing ML model...")
        train_model()
    else:
        print("[AeroPredict] Cloud environment detected. Skipping training, loading existing model.")
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
