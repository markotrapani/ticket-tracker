#!/usr/bin/env python3
"""Re-parse PDFs to fix truncated subjects using the updated parser.

Only updates the subject field - doesn't touch conversation notes or other enrichment data.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import create_app
from backend.models.database import db
from backend.models.ticket import Ticket
from backend.services.pdf_parser import parse_zendesk_pdf

PDFS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'pdfs')


def main():
    app = create_app()
    with app.app_context():
        tickets = Ticket.query.filter(Ticket.enrichment_level != 'metadata_only').all()
        total = len(tickets)
        print(f"Re-parsing subjects for {total} enriched tickets")

        updated = 0
        errors = 0
        start = time.time()

        for i, ticket in enumerate(tickets, 1):
            pdf_path = os.path.join(PDFS_DIR, f"#{ticket.zendesk_id}.pdf")
            if not os.path.exists(pdf_path):
                continue

            try:
                parsed = parse_zendesk_pdf(pdf_path)
                new_subject = parsed.get('subject')
                if new_subject and new_subject != ticket.subject:
                    old = ticket.subject or ''
                    ticket.subject = new_subject
                    db.session.commit()
                    updated += 1
                    if len(new_subject) > len(old):
                        print(f"  #{ticket.zendesk_id}: '{old}' -> '{new_subject}'")
            except Exception as e:
                db.session.rollback()
                errors += 1
                if errors <= 5:
                    print(f"  ERROR #{ticket.zendesk_id}: {e}")

            if i % 100 == 0:
                print(f"  [{i}/{total}] {updated} subjects updated, {errors} errors")

        elapsed = time.time() - start
        print(f"\nDone in {elapsed:.1f}s: {updated} subjects updated, {errors} errors")


if __name__ == '__main__':
    main()
