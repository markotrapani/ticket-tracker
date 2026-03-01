"""Tests for the enrichment service (enrichment.py).

Focuses on outage detection logic — the _detect_outage function.
"""

from datetime import date, datetime

import pytest

from backend.models.ticket import Ticket, TicketNote
from backend.models.database import db
from backend.services.enrichment import (
    _detect_outage,
    enrich_ticket_from_parsed,
    reset_ticket_enrichment,
)


class TestOutageDetection:
    """Tests for _detect_outage()."""

    def test_strong_phrase_production_outage(self):
        """'production outage' should trigger outage detection."""
        parsed = {'subject': 'Production outage on cluster', 'comments': []}
        assert _detect_outage(parsed) is True

    def test_strong_phrase_database_down(self):
        """'database down' should trigger outage detection."""
        parsed = {
            'subject': 'Help needed',
            'comments': [
                {'author': 'Customer', 'body': 'Our database is down completely!'}
            ],
        }
        assert _detect_outage(parsed) is True

    def test_strong_phrase_data_loss(self):
        """'data loss' should trigger outage detection."""
        parsed = {
            'subject': 'Data loss after failover',
            'comments': [],
        }
        assert _detect_outage(parsed) is True

    def test_weak_signals_single_no_match(self):
        """A single weak signal should NOT trigger outage."""
        parsed = {
            'subject': 'Brief outage yesterday',
            'comments': [],
        }
        # 'outage' alone is a weak signal — needs 2+
        assert _detect_outage(parsed) is False

    def test_weak_signals_two_match(self):
        """Two weak signals should trigger outage."""
        parsed = {
            'subject': 'Outage and downtime on cluster',
            'comments': [],
        }
        assert _detect_outage(parsed) is True

    def test_bot_messages_excluded(self):
        """Bot authors should be excluded from outage analysis."""
        parsed = {
            'subject': 'Question about config',
            'comments': [
                {
                    'author': 'Redis Support Bot Agent',
                    'body': 'Include the keywords production, urgent, or outage '
                            'in your message for priority handling.',
                },
            ],
        }
        assert _detect_outage(parsed) is False

    def test_boilerplate_excluded(self):
        """Boilerplate template text should be excluded."""
        parsed = {
            'subject': 'Need help',
            'comments': [
                {
                    'author': 'Support Agent',
                    'body': 'This is an automated response. Your cluster is being '
                            'checked for production outage.',
                },
            ],
        }
        assert _detect_outage(parsed) is False

    def test_empty_parsed_no_outage(self):
        """Empty parsed data should not trigger outage."""
        assert _detect_outage({}) is False
        assert _detect_outage({'subject': '', 'comments': []}) is False

    def test_extra_text_parameter(self):
        """The extra_text parameter should be included in analysis."""
        parsed = {'subject': 'Help', 'comments': []}
        assert _detect_outage(parsed, extra_text='database down') is True

    def test_first_10_comments_only(self):
        """Only first 10 non-bot comments should be checked."""
        comments = [
            {'author': f'Agent {i}', 'body': 'Normal troubleshooting text.'}
            for i in range(15)
        ]
        # Put outage keywords only in comment 12 (should be skipped)
        comments[12]['body'] = 'Production outage confirmed!'
        parsed = {'subject': 'Issue', 'comments': comments}
        assert _detect_outage(parsed) is False

    def test_outage_phrases_list(self):
        """Key outage phrases should all trigger detection."""
        critical_phrases = [
            'production outage', 'cluster down', 'data loss',
            'service disruption', 'node failure', 'connectivity loss',
        ]
        for phrase in critical_phrases:
            parsed = {
                'subject': f'Issue: {phrase}',
                'comments': [],
            }
            assert _detect_outage(parsed) is True, f"Missed phrase: {phrase!r}"


class TestEnrichTicketFromParsed:
    """Tests for enrich_ticket_from_parsed()."""

    def test_basic_enrichment(self, app, sample_ticket):
        """Should populate subject and customer from parsed data."""
        with app.app_context():
            ticket = Ticket.query.get(sample_ticket.id)
            parsed = {
                'subject': 'Test Subject',
                'customer_name': 'Test Customer',
                'comments': [],
            }
            enrich_ticket_from_parsed(ticket, parsed, skip_star=True)
            db.session.commit()

            assert ticket.subject == 'Test Subject'
            assert ticket.customer_name == 'Test Customer'

    def test_does_not_overwrite_existing_fields(self, app, enriched_ticket):
        """Should not overwrite fields that already have values."""
        with app.app_context():
            ticket = Ticket.query.get(enriched_ticket.id)
            original_subject = ticket.subject
            parsed = {
                'subject': 'New Subject Should Be Ignored',
                'customer_name': 'New Customer Should Be Ignored',
                'comments': [],
            }
            enrich_ticket_from_parsed(ticket, parsed, skip_star=True)
            db.session.commit()

            assert ticket.subject == original_subject

    def test_stores_conversation_notes(self, app, sample_ticket):
        """Should store comments as TicketNote records."""
        with app.app_context():
            ticket = Ticket.query.get(sample_ticket.id)
            parsed = {
                'comments': [
                    {
                        'author': 'Customer',
                        'body': 'Need help!',
                        'timestamp': '2024-06-15T10:00:00Z',
                        'is_internal': False,
                    },
                    {
                        'author': 'Agent',
                        'body': 'On it.',
                        'timestamp': '2024-06-15T12:00:00Z',
                        'is_internal': False,
                    },
                ],
            }
            enrich_ticket_from_parsed(ticket, parsed, skip_star=True)
            db.session.commit()

            notes = TicketNote.query.filter_by(
                ticket_id=ticket.id, note_type='conversation'
            ).all()
            assert len(notes) == 2

    def test_sets_enrichment_level(self, app, sample_ticket):
        """Should update enrichment_level based on content."""
        with app.app_context():
            ticket = Ticket.query.get(sample_ticket.id)
            parsed = {
                'comments': [
                    {
                        'author': 'Customer',
                        'body': 'Long description text here.',
                        'timestamp': '2024-06-15T10:00:00Z',
                    },
                ],
            }
            enrich_ticket_from_parsed(ticket, parsed, skip_star=False)
            db.session.commit()

            # Should be at least 'partial' since description is populated
            assert ticket.enrichment_level in ('partial', 'full')

    def test_outage_detection_during_enrichment(self, app, sample_ticket):
        """Should set is_production_outage if outage keywords found."""
        with app.app_context():
            ticket = Ticket.query.get(sample_ticket.id)
            parsed = {
                'subject': 'Database down on production cluster',
                'comments': [],
            }
            enrich_ticket_from_parsed(ticket, parsed, skip_star=True)
            db.session.commit()

            assert ticket.is_production_outage is True


class TestResetTicketEnrichment:
    """Tests for reset_ticket_enrichment()."""

    def test_clears_star_fields(self, app, enriched_ticket):
        """Should clear summary, root_cause, steps_taken, resolution."""
        with app.app_context():
            ticket = Ticket.query.get(enriched_ticket.id)
            reset_ticket_enrichment(ticket)
            db.session.commit()

            assert ticket.summary is None
            assert ticket.root_cause is None
            assert ticket.steps_taken is None
            assert ticket.resolution is None
            assert ticket.description is None

    def test_preserves_metadata(self, app, enriched_ticket):
        """Should preserve zendesk_id, status, dates."""
        with app.app_context():
            ticket = Ticket.query.get(enriched_ticket.id)
            original_zid = ticket.zendesk_id
            original_status = ticket.status
            original_date = ticket.created_date

            reset_ticket_enrichment(ticket)
            db.session.commit()

            assert ticket.zendesk_id == original_zid
            assert ticket.status == original_status
            assert ticket.created_date == original_date

    def test_sets_metadata_only_level(self, app, enriched_ticket):
        """Should reset enrichment_level to metadata_only."""
        with app.app_context():
            ticket = Ticket.query.get(enriched_ticket.id)
            assert ticket.enrichment_level == 'full'
            reset_ticket_enrichment(ticket)
            db.session.commit()

            assert ticket.enrichment_level == 'metadata_only'

    def test_deletes_conversation_notes_only(self, app, ticket_with_notes):
        """Should delete conversation notes but preserve manual notes."""
        with app.app_context():
            ticket = Ticket.query.get(ticket_with_notes.id)
            # Add a manual note
            db.session.add(TicketNote(
                ticket_id=ticket.id,
                content='This is my personal note.',
                note_type='general',
            ))
            db.session.commit()

            conv_before = TicketNote.query.filter_by(
                ticket_id=ticket.id, note_type='conversation'
            ).count()
            assert conv_before > 0

            deleted = reset_ticket_enrichment(ticket)
            db.session.commit()

            assert deleted == conv_before
            conv_after = TicketNote.query.filter_by(
                ticket_id=ticket.id, note_type='conversation'
            ).count()
            assert conv_after == 0

            # Manual note should still be there
            manual = TicketNote.query.filter_by(
                ticket_id=ticket.id, note_type='general'
            ).count()
            assert manual == 1
