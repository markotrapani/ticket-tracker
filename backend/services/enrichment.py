"""Enrichment service - populates ticket fields from parsed PDF data.

Extracted from app.py so it can be shared between Flask routes and the MCP server.
"""

from datetime import datetime

from dateutil import parser as dateutil_parser

from backend.models.database import db
from backend.models.ticket import TicketNote
from backend.services import scoring, stats
from backend.services.pdf_parser import synthesize_investigation, build_summary


def enrich_ticket_from_parsed(ticket, parsed):
    """Enrich a ticket with data from a parsed PDF.

    Populates metadata fields, stores conversation comments as TicketNotes,
    builds a comprehensive description, auto-suggests category, and scores.

    Args:
        ticket: Ticket model instance (must be within an active DB session)
        parsed: Dict returned by parse_zendesk_pdf()
    """
    # Metadata fields (only overwrite empty fields)
    if parsed.get('subject') and not ticket.subject:
        ticket.subject = parsed['subject']
    if parsed.get('customer_name') and not ticket.customer_name:
        ticket.customer_name = parsed['customer_name']
    if parsed.get('priority') and not ticket.priority:
        ticket.priority = parsed['priority']
    if parsed.get('severity') and not ticket.severity:
        ticket.severity = parsed['severity']
    if parsed.get('product_line') and not ticket.product_area:
        ticket.product_area = parsed['product_line']
    if parsed.get('is_production'):
        ticket.is_production_outage = True

    # Build rich root_cause, steps_taken, resolution from conversation
    rich_root_cause, rich_steps, rich_resolution = synthesize_investigation(parsed)
    if rich_root_cause:
        ticket.root_cause = rich_root_cause
    elif parsed.get('root_cause') and not ticket.root_cause:
        ticket.root_cause = parsed['root_cause']
    if rich_steps:
        ticket.steps_taken = rich_steps
    if rich_resolution:
        ticket.resolution = rich_resolution
    elif parsed.get('resolution') and not ticket.resolution:
        ticket.resolution = parsed['resolution']

    # Store conversation comments as TicketNotes
    comments = parsed.get('comments', [])
    if comments:
        # Clear existing conversation notes to avoid duplicates on re-import
        TicketNote.query.filter_by(
            ticket_id=ticket.id, note_type='conversation'
        ).delete()

        for comment in comments:
            try:
                ts = dateutil_parser.parse(comment['timestamp'])
            except Exception:
                ts = datetime.utcnow()

            db.session.add(TicketNote(
                ticket_id=ticket.id,
                content=comment['body'],
                note_type='conversation',
                author=comment.get('author', 'Unknown'),
                is_internal=comment.get('is_internal', False),
                created_at=ts,
            ))

    # Build comprehensive description from all non-internal comments
    bot_authors = {'Redis Support Bot Agent', 'Analyzer Bot'}
    public_comments = [
        c for c in comments
        if not c.get('is_internal')
        and c.get('author') not in bot_authors
        and 'This is an automated response' not in c.get('body', '')
        and c.get('body', '').strip()
    ]
    if public_comments:
        desc_parts = []
        for c in public_comments:
            desc_parts.append(f"[{c['author']} - {c['timestamp']}]\n{c['body']}")
        ticket.description = '\n\n---\n\n'.join(desc_parts)

    # Build situation summary
    ticket.summary = build_summary(parsed)

    # Enrichment level
    has_desc = bool(ticket.description)
    has_res = bool(ticket.resolution or ticket.root_cause)
    if has_desc and has_res:
        ticket.enrichment_level = 'full'
    elif has_desc or has_res:
        ticket.enrichment_level = 'partial'

    # Auto-suggest category
    if not ticket.category:
        suggestion = stats.suggest_category(ticket)
        if suggestion.get('suggested') and suggestion.get('confidence', 0) >= 0.33:
            ticket.category = suggestion['suggested']

    scoring.score_ticket(ticket)
    ticket.updated_at = datetime.utcnow()
