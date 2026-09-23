#!/usr/bin/env python3
"""Thin shim — the real scanner lives in agent/backend_checks.py so that
pip installs (`pip install jev-backend-qa`) carry it. Direct invocation
`python3 bin/backend_checks.py --repo <path>` keeps working from a clone."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.backend_checks import main

if __name__ == "__main__":
    main()
