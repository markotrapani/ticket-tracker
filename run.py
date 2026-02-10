#!/usr/bin/env python3
"""Entry point for Ticket Tracker web server."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.app import create_app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5050)
