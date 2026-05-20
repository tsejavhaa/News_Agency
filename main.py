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
║   1. DataMgr    — Source scraper orchestration       ║
║   2. Webmaster  — FastAPI web server                 ║
╠══════════════════════════════════════════════════════╣
║  Scheduler: manual / every N hours / daily time      ║
║  Data:     database/master_articles.json             ║
╚══════════════════════════════════════════════════════╝
    """)

    uvicorn.run(
        "agents.webmaster:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
