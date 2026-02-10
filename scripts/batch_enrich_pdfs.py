#!/usr/bin/env python3
"""Batch-parse all PDFs and store enrichment data (conversation notes, related tickets, metadata).

This does NOT generate STAR analysis - that's done by AI agents separately.
Skips tickets that are already enriched (partial or full).
"""

import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import create_app
from backend.models.database import db
from backend.models.ticket import Ticket
from backend.services.enrichment import enrich_ticket_from_parsed
from backend.services.pdf_parser import parse_zendesk_pdf

PDFS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'pdfs')


def main():
    app = create_app()
    with app.app_context():
        # Get all metadata_only tickets
        tickets = Ticket.query.filter_by(enrichment_level='metadata_only').order_by(Ticket.auto_score.desc()).all()
        total = len(tickets)
        print(f"Found {total} unenriched tickets to process")

        success = 0
        skipped = 0
        errors = 0
        start = time.time()

        for i, ticket in enumerate(tickets, 1):
            pdf_path = os.path.join(PDFS_DIR, f"#{ticket.zendesk_id}.pdf")
            if not os.path.exists(pdf_path):
                skipped += 1
                if i % 100 == 0:
                    print(f"  [{i}/{total}] Skipped (no PDF): #{ticket.zendesk_id}")
                continue

            try:
                parsed = parse_zendesk_pdf(pdf_path)
                enrich_ticket_from_parsed(ticket, parsed, skip_star=True)
                db.session.commit()
                success += 1
            except Exception as e:
                db.session.rollback()
                errors += 1
                print(f"  [{i}/{total}] ERROR #{ticket.zendesk_id}: {e}")

            if i % 50 == 0:
                elapsed = time.time() - start
                rate = success / elapsed if elapsed > 0 else 0
                print(f"  [{i}/{total}] {success} enriched, {skipped} skipped, {errors} errors ({rate:.1f}/sec)")

        elapsed = time.time() - start
        print(f"\nDone in {elapsed:.1f}s: {success} enriched, {skipped} skipped, {errors} errors")


if __name__ == '__main__':
    main()
