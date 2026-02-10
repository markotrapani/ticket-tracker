#!/usr/bin/env python3
"""List ticket IDs that need STAR analysis (enriched but no summary).

Usage: python scripts/list_needs_star.py [offset] [limit]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import create_app
from backend.models.ticket import Ticket


def main():
    offset = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 999

    app = create_app()
    with app.app_context():
        tickets = (
            Ticket.query
            .filter(Ticket.enrichment_level.in_(['partial', 'full']))
            .filter((Ticket.summary == None) | (Ticket.summary == ''))
            .order_by(Ticket.auto_score.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        total_needing = (
            Ticket.query
            .filter(Ticket.enrichment_level.in_(['partial', 'full']))
            .filter((Ticket.summary == None) | (Ticket.summary == ''))
            .count()
        )
        print(f"TOTAL_NEEDING_STAR: {total_needing}")
        for t in tickets:
            print(t.zendesk_id)


if __name__ == '__main__':
    main()
