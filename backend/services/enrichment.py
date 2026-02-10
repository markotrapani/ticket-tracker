"""Enrichment service - populates ticket fields from parsed PDF data.

Extracted from app.py so it can be shared between Flask routes and the MCP server.
"""

import json
from datetime import datetime

from dateutil import parser as dateutil_parser

from backend.models.database import db
from backend.models.ticket import TicketNote
from backend.services import scoring, stats
from backend.services.pdf_parser import synthesize_investigation, build_summary


def enrich_ticket_from_parsed(ticket, parsed, skip_star=False):
    """Enrich a ticket with data from a parsed PDF.

    Populates metadata fields, stores conversation comments as TicketNotes,
    builds a comprehensive description, auto-suggests category, and scores.

    Args:
        ticket: Ticket model instance (must be within an active DB session)
        parsed: Dict returned by parse_zendesk_pdf()
        skip_star: If True, skip heuristic STAR generation (summary, root_cause,
                   steps_taken, resolution). Use when AI analysis will fill these.
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

    # Zendesk infrastructure metadata (only overwrite empty fields)
    if parsed.get('product_line') and not ticket.product_line:
        ticket.product_line = parsed['product_line']
    if parsed.get('additional_products') and not ticket.additional_products:
        ticket.additional_products = parsed['additional_products']
    if parsed.get('jira_ticket_ids') and not ticket.jira_ticket_ids:
        ticket.jira_ticket_ids = parsed['jira_ticket_ids']
    if parsed.get('subscription_id') and not ticket.subscription_id:
        ticket.subscription_id = parsed['subscription_id']
    if parsed.get('bdb_id') and not ticket.bdb_id:
        ticket.bdb_id = parsed['bdb_id']
    if parsed.get('endpoint') and not ticket.endpoint:
        ticket.endpoint = parsed['endpoint']
    if parsed.get('customer_impact') and not ticket.customer_impact:
        ticket.customer_impact = parsed['customer_impact']
    if parsed.get('issue_type') and not ticket.issue_type:
        ticket.issue_type = parsed['issue_type']
    if parsed.get('problem_summary_zd') and not ticket.problem_summary_zd:
        ticket.problem_summary_zd = parsed['problem_summary_zd']
    if parsed.get('resolution_summary_zd') and not ticket.resolution_summary_zd:
        ticket.resolution_summary_zd = parsed['resolution_summary_zd']

    # Related ticket IDs from conversation cross-references
    related = parsed.get('related_ticket_ids', [])
    if related:
        ticket.related_ticket_ids = json.dumps(related)

    # Build rich root_cause, steps_taken, resolution from conversation
    # (skipped when AI analysis will fill these instead)
    if not skip_star:
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

    # Build situation summary (skipped when AI analysis will fill this)
    if not skip_star:
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


def reset_ticket_enrichment(ticket):
    """Reset a ticket's enrichment data back to metadata_only state.

    Clears all enriched fields while preserving CSV-imported metadata
    (zendesk_id, status, group_name, assignee, created_date, solved_date, zendesk_url).
    Deletes conversation notes but preserves manual user notes.
    Re-scores the ticket (content_score will drop).

    Args:
        ticket: Ticket model instance (must be within an active DB session)

    Returns:
        int: Number of conversation notes deleted
    """
    # Clear enriched content fields
    ticket.summary = None
    ticket.description = None
    ticket.root_cause = None
    ticket.steps_taken = None
    ticket.resolution = None
    ticket.subject = None
    ticket.customer_name = None

    # Clear Zendesk infrastructure metadata from PDF
    ticket.product_line = None
    ticket.additional_products = None
    ticket.jira_ticket_ids = None
    ticket.subscription_id = None
    ticket.bdb_id = None
    ticket.endpoint = None
    ticket.customer_impact = None
    ticket.issue_type = None
    ticket.problem_summary_zd = None
    ticket.resolution_summary_zd = None
    ticket.related_ticket_ids = None

    # Reset enrichment level
    ticket.enrichment_level = 'metadata_only'

    # Delete conversation notes (preserve manual notes)
    deleted = TicketNote.query.filter_by(
        ticket_id=ticket.id, note_type='conversation'
    ).delete()

    # Re-score (will drop content score component)
    scoring.score_ticket(ticket)
    ticket.updated_at = datetime.utcnow()

    return deleted
