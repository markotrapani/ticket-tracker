"""CSV importer for ZenDesk ticket exports.

Handles the semicolon-delimited, double-quoted CSV format from ZenDesk:
"Ticket ID";"Ticket status";"Ticket group";"Assignee name";"Ticket created - Date";"Ticket solved - Date";"Tickets"
"""

import csv
from datetime import datetime

from backend.models.database import db
from backend.models.ticket import Ticket, ScoreHistory
from backend.services.scoring import score_ticket

import json


COLUMN_MAP = {
    'Ticket ID': 'zendesk_id',
    'Ticket status': 'status',
    'Ticket group': 'group_name',
    'Assignee name': 'assignee',
    'Ticket created - Date': 'created_date',
    'Ticket solved - Date': 'solved_date',
}


def parse_date(date_str):
    """Parse date from CSV. Returns None for empty/whitespace values."""
    if not date_str:
        return None
    date_str = date_str.strip().strip('"')
    if not date_str or date_str.isspace():
        return None
    return datetime.strptime(date_str, '%Y-%m-%d').date()


def import_csv(csv_path, update_existing=True):
    """Import tickets from ZenDesk CSV export.

    Returns dict: {new: int, updated: int, skipped: int, errors: list}
    """
    results = {'new': 0, 'updated': 0, 'skipped': 0, 'errors': []}

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';', quotechar='"')

        for row_num, row in enumerate(reader, start=2):
            try:
                zendesk_id = row['Ticket ID'].strip()
                created = parse_date(row['Ticket created - Date'])
                solved = parse_date(row.get('Ticket solved - Date', ''))
                status = row['Ticket status'].strip()
                group_name = row['Ticket group'].strip()
                assignee = row['Assignee name'].strip()

                existing = Ticket.query.filter_by(zendesk_id=zendesk_id).first()

                if existing:
                    if update_existing:
                        existing.status = status
                        existing.group_name = group_name
                        existing.assignee = assignee
                        existing.created_date = created
                        existing.solved_date = solved
                        results['updated'] += 1
                    else:
                        results['skipped'] += 1
                else:
                    ticket = Ticket(
                        zendesk_id=zendesk_id,
                        status=status,
                        group_name=group_name,
                        assignee=assignee,
                        created_date=created,
                        solved_date=solved,
                        zendesk_url=f'https://redislabs.zendesk.com/agent/tickets/{zendesk_id}',
                        enrichment_level='metadata_only',
                    )
                    db.session.add(ticket)
                    results['new'] += 1

            except Exception as e:
                results['errors'].append(f"Row {row_num}: {str(e)}")

    db.session.commit()

    # Auto-score all tickets that don't have scores yet
    tickets_to_score = Ticket.query.filter(Ticket.auto_score.is_(None)).all()
    for ticket in tickets_to_score:
        score_result = score_ticket(ticket)
        history = ScoreHistory(
            ticket_id=ticket.id,
            score_type='auto',
            score_value=score_result['auto_score'],
            components_json=json.dumps(score_result['auto_components']),
        )
        db.session.add(history)

    db.session.commit()

    return results
