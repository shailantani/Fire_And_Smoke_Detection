
import os
from pathlib import Path

class Config:
    # Directories
    BASE_DIR = Path(os.getcwd())
    DETECTED_FIRES_DIR = BASE_DIR / "detected_events"
    
    # Create directory if it doesn't exist
    DETECTED_FIRES_DIR.mkdir(exist_ok=True)

    # API Keys (Loaded from environment variables for security)
    # You must set these in your .env file or environment
    IMGUR_CLIENT_ID = os.getenv("IMGUR_CLIENT_ID", "your_imgur_id") 
    
    # Video Source
    VIDEO_SOURCE = os.getenv("VIDEO_SOURCE", "0")
    
    # Alert Settings
    ALERT_COOLDOWN = 30  # Seconds
