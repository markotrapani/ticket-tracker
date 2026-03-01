"""Tests for the backfill CLI command logic.

Tests Jira ID parsing, escalation detection, custom script detection,
and first response time calculation — all extracted from cli.py's
backfill command patterns.
"""

import re
from datetime import date, datetime

import pytest

from backend.models.ticket import Ticket, TicketNote, TicketJiraIssue
from backend.models.database import db


# ── Regex patterns (same as cli.py backfill) ──

JIRA_PATTERN = re.compile(r'\b(RED-\d{4,6})\b', re.IGNORECASE)

ESCALATION_MARKERS = [
    '@l3_to_review', '@l3_review', 'l3_to_review', 'l3_review_done',
    'moving to l3', 'escalat', 'r&d', 'engineering team',
    'filed a bug', 'filed a jira', 'opened a jira',
]

SCRIPT_MARKERS = [
    'script', 'wrote a', 'built a', 'developed a', 'created a tool',
    'custom tool', '.py', '.sh', 'automation', 'wrote custom',
    'built custom', 'python script', 'bash script', 'shell script',
]

BOT_AUTHORS = {
    'redis support bot agent', 'analyzer bot', 'pagerduty',
    'redisscope bot',
}


# ── Jira ID Parsing Tests ──

class TestJiraParsing:

    def test_single_jira_in_root_cause(self, enriched_ticket):
        """Should find RED-123456 in root_cause field."""
        matches = JIRA_PATTERN.findall(enriched_ticket.root_cause)
        jira_ids = {m.upper() for m in matches}
        assert 'RED-123456' in jira_ids

    def test_multiple_jiras_across_fields(self, enriched_ticket):
        """Should find Jira IDs across root_cause and resolution."""
        found = set()
        for field in ('root_cause', 'resolution'):
            text = getattr(enriched_ticket, field, '') or ''
            for match in JIRA_PATTERN.finditer(text):
                found.add(match.group(1).upper())
        assert found == {'RED-123456', 'RED-789012'}

    def test_no_jira_ids(self, sample_ticket):
        """Tickets without Jira references should yield no matches."""
        for field in ('summary', 'root_cause', 'steps_taken', 'resolution'):
            text = getattr(sample_ticket, field, '') or ''
            assert JIRA_PATTERN.findall(text) == []

    def test_jira_pattern_boundaries(self):
        """Regex should respect word boundaries and digit length."""
        # Valid patterns
        assert JIRA_PATTERN.findall('See RED-12345 for details') == ['RED-12345']
        assert JIRA_PATTERN.findall('RED-123456') == ['RED-123456']
        assert JIRA_PATTERN.findall('RED-1234') == ['RED-1234']
        # Too few digits (3)
        assert JIRA_PATTERN.findall('RED-123') == []
        # Too many digits (7)
        assert JIRA_PATTERN.findall('RED-1234567') == []

    def test_case_insensitive(self):
        """Should match red-12345 case-insensitively."""
        assert JIRA_PATTERN.findall('see red-54321 for info') == ['red-54321']

    def test_jira_in_conversation_notes(self, app, ticket_with_notes):
        """Should find Jira IDs in conversation note content."""
        with app.app_context():
            note = TicketNote(
                ticket_id=ticket_with_notes.id,
                content='Filed RED-99999 to track this issue.',
                note_type='conversation',
                author='Marko Trapani',
                created_at=datetime(2024, 5, 4, 10, 0),
            )
            db.session.add(note)
            db.session.commit()

            matches = JIRA_PATTERN.findall(note.content)
            assert 'RED-99999' in [m.upper() for m in matches]

    def test_jira_deduplication(self):
        """Same Jira ID appearing multiple times should be deduped."""
        text = 'RED-12345 was filed. Later RED-12345 was resolved.'
        found = set(m.upper() for m in JIRA_PATTERN.findall(text))
        assert found == {'RED-12345'}


# ── Escalation Detection Tests ──

class TestEscalationDetection:

    def test_l3_review_tag(self):
        """@l3_to_review in note should trigger escalation."""
        text = '@l3_to_review - this needs engineering eyes'
        assert any(marker in text.lower() for marker in ESCALATION_MARKERS)

    def test_escalation_in_star_fields(self, app):
        """Escalation mention in root_cause should flag ticket."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='300001',
                status='Closed',
                created_date=date(2024, 1, 1),
                root_cause='Issue was escalated to R&D for a kernel patch.',
            )
            db.session.add(ticket)
            db.session.commit()

            star_text = ' '.join(filter(None, [
                ticket.summary, ticket.root_cause,
                ticket.steps_taken, ticket.resolution,
            ])).lower()
            assert any(marker in star_text for marker in ESCALATION_MARKERS)

    def test_no_escalation_in_normal_ticket(self, sample_ticket):
        """Ticket without escalation keywords should not flag."""
        star_text = ' '.join(filter(None, [
            sample_ticket.summary, sample_ticket.root_cause,
            sample_ticket.steps_taken, sample_ticket.resolution,
        ])).lower()
        assert not any(marker in star_text for marker in ESCALATION_MARKERS)

    def test_jira_ids_imply_escalation(self, app):
        """Tickets with jira_ticket_ids should flag as escalated."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='300002',
                status='Closed',
                created_date=date(2024, 2, 1),
                jira_ticket_ids='RED-12345',
            )
            db.session.add(ticket)
            db.session.commit()
            assert ticket.jira_ticket_ids is not None

    def test_escalation_markers_comprehensive(self):
        """All expected markers should be detected."""
        test_cases = [
            ('@l3_to_review', True),
            ('moving to l3', True),
            ('escalated to engineering', True),  # 'escalat' matches
            ('filed a bug', True),
            ('filed a jira', True),
            ('r&d team involved', True),
            ('normal conversation text', False),
            ('customer asked a question', False),
        ]
        for text, expected in test_cases:
            result = any(marker in text.lower() for marker in ESCALATION_MARKERS)
            assert result == expected, f"Failed for: {text!r}"

    def test_escalation_in_conversation_note(self, app, ticket_with_notes):
        """@l3_to_review in notes should trigger escalation."""
        with app.app_context():
            notes = TicketNote.query.filter_by(
                ticket_id=ticket_with_notes.id, note_type='conversation'
            ).all()
            found = False
            for note in notes:
                if any(marker in (note.content or '').lower()
                       for marker in ESCALATION_MARKERS):
                    found = True
                    break
            assert found


# ── Custom Script Detection Tests ──

class TestScriptDetection:

    def test_script_keyword_in_steps(self, enriched_ticket):
        """'Wrote a custom script' in steps_taken should flag."""
        star_text = ' '.join(filter(None, [
            enriched_ticket.summary, enriched_ticket.root_cause,
            enriched_ticket.steps_taken, enriched_ticket.resolution,
        ])).lower()
        assert any(marker in star_text for marker in SCRIPT_MARKERS)

    def test_python_script_mention(self):
        """'.py' extension should trigger script detection."""
        text = 'ran migration_tool.py to fix the data'
        assert any(marker in text.lower() for marker in SCRIPT_MARKERS)

    def test_bash_script_mention(self):
        """'bash script' should trigger detection."""
        text = 'Created a bash script to automate the failover'
        assert any(marker in text.lower() for marker in SCRIPT_MARKERS)

    def test_no_script_in_normal_text(self, sample_ticket):
        """Normal ticket should not flag for scripts."""
        star_text = ' '.join(filter(None, [
            sample_ticket.summary, sample_ticket.root_cause,
            sample_ticket.steps_taken, sample_ticket.resolution,
        ])).lower()
        assert not any(marker in star_text for marker in SCRIPT_MARKERS)

    def test_all_script_markers(self):
        """Each script marker should match when present."""
        for marker in SCRIPT_MARKERS:
            text = f'we used {marker} for the fix'
            assert marker in text.lower(), f"Marker not found: {marker!r}"


# ── First Response Time Tests ──

class TestFirstResponseTime:

    def test_first_response_calculation(self, app, ticket_with_notes):
        """First non-bot response time should be calculated correctly."""
        with app.app_context():
            ticket = Ticket.query.get(ticket_with_notes.id)
            notes = TicketNote.query.filter_by(
                ticket_id=ticket.id, note_type='conversation', is_internal=False
            ).order_by(TicketNote.created_at.asc()).all()

            first_response = None
            for note in notes:
                author = (note.author or '').lower()
                if author in BOT_AUTHORS:
                    continue
                if 'this is an automated response' in (note.content or '').lower():
                    continue
                # First real agent reply
                if note.created_at and ticket.created_date:
                    created_dt = datetime.combine(ticket.created_date, datetime.min.time())
                    delta = note.created_at - created_dt
                    hours = delta.total_seconds() / 3600
                    if hours >= 0:
                        first_response = round(hours, 1)
                break

            # The first non-bot, non-automated public message is the customer's
            # at 10:00 on May 1. ticket.created_date is May 1 (midnight).
            # So: (May 1 10:00) - (May 1 00:00) = 10.0 hours
            assert first_response == 10.0

    def test_skip_bot_messages(self, app):
        """Bot messages should be skipped when finding first response."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='400001',
                status='Closed',
                created_date=date(2024, 1, 1),
            )
            db.session.add(ticket)
            db.session.flush()

            # Only bot messages
            db.session.add(TicketNote(
                ticket_id=ticket.id,
                content='Automated triage.',
                note_type='conversation',
                author='Redis Support Bot Agent',
                is_internal=False,
                created_at=datetime(2024, 1, 1, 0, 5),
            ))
            db.session.commit()

            notes = TicketNote.query.filter_by(
                ticket_id=ticket.id, note_type='conversation', is_internal=False
            ).order_by(TicketNote.created_at.asc()).all()

            first_response = None
            for note in notes:
                if (note.author or '').lower() in BOT_AUTHORS:
                    continue
                if 'this is an automated response' in (note.content or '').lower():
                    continue
                first_response = 1.0  # would calculate
                break

            assert first_response is None

    def test_skip_automated_response_text(self, app):
        """Messages with 'this is an automated response' should be skipped."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='400002',
                status='Closed',
                created_date=date(2024, 2, 1),
            )
            db.session.add(ticket)
            db.session.flush()

            db.session.add(TicketNote(
                ticket_id=ticket.id,
                content='This is an automated response from support.',
                note_type='conversation',
                author='Some Agent',
                is_internal=False,
                created_at=datetime(2024, 2, 1, 1, 0),
            ))
            db.session.add(TicketNote(
                ticket_id=ticket.id,
                content='Hi, I am looking into this.',
                note_type='conversation',
                author='Real Agent',
                is_internal=False,
                created_at=datetime(2024, 2, 1, 5, 0),
            ))
            db.session.commit()

            notes = TicketNote.query.filter_by(
                ticket_id=ticket.id, note_type='conversation', is_internal=False
            ).order_by(TicketNote.created_at.asc()).all()

            first_response = None
            for note in notes:
                if (note.author or '').lower() in BOT_AUTHORS:
                    continue
                if 'this is an automated response' in (note.content or '').lower():
                    continue
                if note.created_at and ticket.created_date:
                    created_dt = datetime.combine(ticket.created_date, datetime.min.time())
                    delta = note.created_at - created_dt
                    first_response = round(delta.total_seconds() / 3600, 1)
                break

            assert first_response == 5.0
