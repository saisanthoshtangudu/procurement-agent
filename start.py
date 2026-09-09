"""
start.py
--------
Launches both the Frontend Web Server (port 8000) and the
Backend Flask API Server (port 5000) concurrently.

Usage:
    python start.py
"""

import os
import signal
import subprocess
import sys
import time

from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

FRONTEND_PORT = 8000
BACKEND_PORT = 5000
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# Explicitly load .env in start.py
load_dotenv(os.path.join(PROJECT_DIR, ".env"))


def main():
    print("=" * 60)
    print("       Procurement Agent - Starting All Services")
    print("=" * 60)
    print(f"  📁 Directory   : {PROJECT_DIR}")
    print(f"  🌐 Web App     : http://localhost:{FRONTEND_PORT}")
    print(f"  ⚙️  Flask API   : http://localhost:{BACKEND_PORT}")
    brevo_loaded = "Configured" if os.getenv("BREVO_API_KEY") else "Missing"
    print(f"  📧 Brevo API   : {brevo_loaded}")
    print("-" * 60)
    print("  Starting servers...")

    env = os.environ.copy()

    # 1. Start Python built-in HTTP server for frontend files
    http_cmd = [sys.executable, "-m", "http.server", str(FRONTEND_PORT)]
    frontend_proc = subprocess.Popen(
        http_cmd,
        cwd=PROJECT_DIR,
        env=env,
    )

    # 2. Start Flask API server (server.py)
    flask_cmd = [sys.executable, "server.py", "--port", str(BACKEND_PORT)]
    backend_proc = subprocess.Popen(
        flask_cmd,
        cwd=PROJECT_DIR,
        env=env,
    )

    time.sleep(1)
    print("\n  [OK] Both servers are running successfully!")
    print(f"  👉 Open your browser at: http://localhost:{FRONTEND_PORT}")
    print("  Press Ctrl+C to stop both servers.\n" + "=" * 60 + "\n")

    processes = [frontend_proc, backend_proc]

    def cleanup():
        print("\nShutting down servers...")
        for p in processes:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    p.kill()
        print("All servers stopped.")

    try:
        # Wait for either process to exit or keyboard interrupt
        while True:
            for p in processes:
                if p.poll() is not None:
                    print(f"A server process exited unexpectedly (code {p.returncode}).")
                    cleanup()
                    return
            time.sleep(1)
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
