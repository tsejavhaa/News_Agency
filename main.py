"""
News Agency — Main Entry Point
Run the full application: python main.py
"""

import logging
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

import uvicorn

if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════╗
║          THE AGENCY — AI News Platform               ║
╠══════════════════════════════════════════════════════╣
║  Web:    http://localhost:8000                       ║
║  Admin:  http://localhost:8000/admin                 ║
║  API:    http://localhost:8000/api/stats             ║
╠══════════════════════════════════════════════════════╣
║  Agents:                                             ║
║   1. Collector  — RSS scraper + image resize         ║
║   2. DataMgr    — SQLite + duplicate detection       ║
║   3. Editor     — Ollama LLM categorize + rank       ║
║   4. Webmaster  — FastAPI web server                 ║
╠══════════════════════════════════════════════════════╣
║  Ollama: ollama serve  (in separate terminal)        ║
║  Model:  ollama pull llama3.2                        ║
╚══════════════════════════════════════════════════════╝
    """)

    uvicorn.run(
        "agents.webmaster:app",
        host="0.0.0.0",
        port=8008,
        reload=False,
        log_level="info",
    )
