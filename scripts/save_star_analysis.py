#!/usr/bin/env python3
"""Save AI-generated STAR analysis fields to a ticket.

Usage: python scripts/save_star_analysis.py <ticket_id> <summary> <root_cause> <steps_taken> <resolution>

All fields are passed as arguments. Use stdin for large content.
"""

import os
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import create_app
from backend.models.database import db
from backend.models.ticket import Ticket
from backend.services import scoring


def main():
    # Read JSON from stdin
    data = json.load(sys.stdin)
    ticket_id = data['ticket_id']
    summary = data['summary']
    root_cause = data['root_cause']
    steps_taken = data['steps_taken']
    resolution = data['resolution']

    app = create_app()
    with app.app_context():
        ticket = Ticket.query.filter_by(zendesk_id=str(ticket_id)).first()
        if not ticket:
            print(f"ERROR: Ticket #{ticket_id} not found")
            sys.exit(1)

        # Skip if already has STAR analysis
        if ticket.enrichment_level == 'full' and ticket.summary and ticket.root_cause:
            print(f"SKIP: #{ticket_id} already has STAR analysis")
            return

        ticket.summary = summary
        ticket.root_cause = root_cause
        ticket.steps_taken = steps_taken
        ticket.resolution = resolution
        ticket.enrichment_level = 'full'
        ticket.updated_at = datetime.utcnow()
        scoring.score_ticket(ticket)
        db.session.commit()
        print(f"OK: #{ticket_id} score={ticket.auto_score}")


if __name__ == '__main__':
    main()
