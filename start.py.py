"""
Start script for running the API server and the bot together.
Usage: python start.py
"""
import threading
import time
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 50)
print("🚀 Starting Trading Bot + API Server")
print("=" * 50)

# Start the bot in a background thread
def start_bot():
    print("[Bot] Starting bot engine...")
    from recovered_bot import app as bot_app
    bot_app.main()

# Start API server (blocking)
def start_api():
    print("[API] Starting FastAPI server on http://localhost:8000")
    print("[API] WebSocket available at ws://localhost:8000/ws")
    print("[API] Press Ctrl+C to stop")
    from api_server import run_server
    run_server()

if __name__ == "__main__":
    # Start bot in background thread
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()
    
    # Give bot some time to initialize
    time.sleep(2)
    
    # Start API server (this blocks)
    start_api()