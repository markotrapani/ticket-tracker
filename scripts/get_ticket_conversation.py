#!/usr/bin/env python3
"""Get a ticket's formatted conversation for AI STAR analysis.

Usage: python scripts/get_ticket_conversation.py <ticket_id>
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import create_app
from backend.models.ticket import Ticket, TicketNote


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/get_ticket_conversation.py <ticket_id>")
        sys.exit(1)

    ticket_id = sys.argv[1]
    app = create_app()
    with app.app_context():
        ticket = Ticket.query.filter_by(zendesk_id=str(ticket_id)).first()
        if not ticket:
            print(f"Ticket #{ticket_id} not found")
            sys.exit(1)

        # Header
        print(f"=== TICKET #{ticket.zendesk_id} ===")
        print(f"Subject: {ticket.subject or 'N/A'}")
        print(f"Customer: {ticket.customer_name or 'N/A'}")
        print(f"Product: {ticket.product_line or 'N/A'}")
        print(f"Priority: {ticket.priority or 'N/A'} | Severity: {ticket.severity or 'N/A'}")
        print(f"Status: {ticket.status} | Created: {ticket.created_date} | Solved: {ticket.solved_date or 'N/A'}")
        if ticket.solved_date and ticket.created_date:
            days = (ticket.solved_date - ticket.created_date).days
            print(f"Resolution Days: {days}")
        print(f"Production: {'Yes' if ticket.is_production_outage else 'No'}")
        print(f"Category: {ticket.category or 'N/A'}")
        if ticket.problem_summary_zd:
            print(f"\nZendesk Problem Summary: {ticket.problem_summary_zd}")
        if ticket.resolution_summary_zd:
            print(f"Zendesk Resolution Summary: {ticket.resolution_summary_zd}")

        # Conversation
        notes = (
            TicketNote.query
            .filter_by(ticket_id=ticket.id, note_type='conversation')
            .order_by(TicketNote.created_at.asc())
            .all()
        )

        if notes:
            print(f"\n=== CONVERSATION ({len(notes)} comments) ===\n")
            for note in notes:
                visibility = "INTERNAL" if note.is_internal else "PUBLIC"
                ts = note.created_at.strftime("%b %d, %Y %I:%M %p") if note.created_at else "Unknown"
                print(f"[{visibility}] {note.author} - {ts}")
                print(note.content)
                print("\n---\n")


if __name__ == '__main__':
    main()
