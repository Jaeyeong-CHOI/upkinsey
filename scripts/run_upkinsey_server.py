#!/usr/bin/env python3
"""Compatibility launcher; the server implementation is an installable module."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from upstage_api_sim.server import main

if __name__ == "__main__":
    main()
