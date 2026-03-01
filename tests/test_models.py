"""Tests for the data models (ticket.py).

Tests model creation, relationships, properties, and to_dict serialization.
"""

from datetime import date, datetime

import pytest

from backend.models.ticket import (
    Ticket, TicketTag, TicketNote, TicketJiraIssue, ScoreHistory,
)
from backend.models.database import db


class TestTicketModel:

    def test_create_ticket(self, app, db_session):
        """Should create a ticket with required fields."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='T00001',
                status='Open',
                created_date=date(2024, 1, 1),
            )
            db_session.session.add(ticket)
            db_session.session.commit()

            assert ticket.id is not None
            assert ticket.zendesk_id == 'T00001'
            assert ticket.status == 'Open'
            assert ticket.assignee == 'Marko Trapani'  # default
            assert ticket.group_name == 'Support - L3'  # default

    def test_resolution_days_property(self, sample_ticket):
        """resolution_days should calculate days between created and solved."""
        # created: June 15, solved: June 22 = 7 days
        assert sample_ticket.resolution_days == 7

    def test_resolution_days_none_when_unsolved(self, app, db_session):
        """resolution_days should be None when ticket is unsolved."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='T00002',
                status='Open',
                created_date=date(2024, 1, 1),
            )
            db_session.session.add(ticket)
            db_session.session.commit()

            assert ticket.resolution_days is None

    def test_to_dict(self, enriched_ticket, app):
        """to_dict should serialize all fields correctly."""
        with app.app_context():
            ticket = Ticket.query.get(enriched_ticket.id)
            d = ticket.to_dict()
            assert d['zendesk_id'] == '100002'
            assert d['status'] == 'Closed'
            assert d['subject'] == 'CRDB replication lag causing data inconsistency'
            assert d['customer_name'] == 'Acme Corp'
            assert d['category'] == 'CRDB/Active-Active'
            assert d['is_production'] is True
            assert 'tags' in d
            assert 'jira_issues_parsed' in d

    def test_zendesk_id_unique(self, app, db_session):
        """Duplicate zendesk_id should raise IntegrityError."""
        with app.app_context():
            db_session.session.add(Ticket(
                zendesk_id='UNIQUE01',
                status='Closed',
                created_date=date(2024, 1, 1),
            ))
            db_session.session.commit()

            db_session.session.add(Ticket(
                zendesk_id='UNIQUE01',
                status='Open',
                created_date=date(2024, 2, 1),
            ))
            with pytest.raises(Exception):  # IntegrityError
                db_session.session.commit()
            db_session.session.rollback()


class TestTicketTagModel:

    def test_add_tag(self, app, sample_ticket, db_session):
        """Should add tags to a ticket."""
        with app.app_context():
            ticket = Ticket.query.get(sample_ticket.id)
            db_session.session.add(TicketTag(
                ticket_id=ticket.id,
                tag='crdb',
                source='manual',
            ))
            db_session.session.commit()

            assert 'crdb' in ticket.tag_list

    def test_tag_uniqueness(self, app, sample_ticket, db_session):
        """Duplicate (ticket_id, tag) should raise error."""
        with app.app_context():
            ticket = Ticket.query.get(sample_ticket.id)
            db_session.session.add(TicketTag(ticket_id=ticket.id, tag='test'))
            db_session.session.commit()

            db_session.session.add(TicketTag(ticket_id=ticket.id, tag='test'))
            with pytest.raises(Exception):
                db_session.session.commit()
            db_session.session.rollback()

    def test_cascade_delete(self, app, sample_ticket, db_session):
        """Deleting ticket should cascade-delete tags."""
        with app.app_context():
            ticket = Ticket.query.get(sample_ticket.id)
            db_session.session.add(TicketTag(ticket_id=ticket.id, tag='cascade'))
            db_session.session.commit()

            db_session.session.delete(ticket)
            db_session.session.commit()

            assert TicketTag.query.filter_by(tag='cascade').count() == 0


class TestTicketNoteModel:

    def test_create_note(self, app, sample_ticket, db_session):
        """Should create conversation note."""
        with app.app_context():
            ticket = Ticket.query.get(sample_ticket.id)
            db_session.session.add(TicketNote(
                ticket_id=ticket.id,
                content='Test note content.',
                note_type='conversation',
                author='Test Agent',
            ))
            db_session.session.commit()

            notes = TicketNote.query.filter_by(ticket_id=ticket.id).all()
            assert len(notes) == 1
            assert notes[0].content == 'Test note content.'

    def test_note_to_dict(self, app, sample_ticket, db_session):
        """Note to_dict should include all fields."""
        with app.app_context():
            ticket = Ticket.query.get(sample_ticket.id)
            note = TicketNote(
                ticket_id=ticket.id,
                content='Serializable note.',
                note_type='general',
            )
            db_session.session.add(note)
            db_session.session.commit()

            d = note.to_dict()
            assert d['content'] == 'Serializable note.'
            assert d['note_type'] == 'general'
            assert 'created_at' in d


class TestTicketJiraIssueModel:

    def test_create_jira_issue(self, app, sample_ticket, db_session):
        """Should create Jira issue reference."""
        with app.app_context():
            ticket = Ticket.query.get(sample_ticket.id)
            db_session.session.add(TicketJiraIssue(
                ticket_id=ticket.id,
                jira_id='RED-12345',
                source_field='root_cause',
            ))
            db_session.session.commit()

            assert 'RED-12345' in ticket.jira_issue_list

    def test_jira_uniqueness(self, app, sample_ticket, db_session):
        """Duplicate (ticket_id, jira_id) should raise error."""
        with app.app_context():
            ticket = Ticket.query.get(sample_ticket.id)
            db_session.session.add(TicketJiraIssue(
                ticket_id=ticket.id, jira_id='RED-11111',
            ))
            db_session.session.commit()

            db_session.session.add(TicketJiraIssue(
                ticket_id=ticket.id, jira_id='RED-11111',
            ))
            with pytest.raises(Exception):
                db_session.session.commit()
            db_session.session.rollback()

    def test_cascade_delete(self, app, sample_ticket, db_session):
        """Deleting ticket should cascade-delete Jira issues."""
        with app.app_context():
            ticket = Ticket.query.get(sample_ticket.id)
            db_session.session.add(TicketJiraIssue(
                ticket_id=ticket.id, jira_id='RED-99999',
            ))
            db_session.session.commit()

            db_session.session.delete(ticket)
            db_session.session.commit()

            assert TicketJiraIssue.query.filter_by(jira_id='RED-99999').count() == 0
