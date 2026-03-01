"""Shared pytest fixtures for ticket tracker tests.

Uses an in-memory SQLite database so tests are fast, isolated, and don't
touch the real data/tickets.db file.
"""

import os
import sys
from datetime import date, datetime

import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import create_app, rebuild_fts
from backend.models.database import db as _db
from backend.models.ticket import Ticket, TicketTag, TicketNote, TicketJiraIssue


class TestConfig:
    """Config that uses in-memory SQLite for tests."""
    SECRET_KEY = 'test-secret'
    SQLALCHEMY_DATABASE_URI = 'sqlite://'  # in-memory
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True


@pytest.fixture(scope='session')
def app():
    """Create the Flask application once per test session."""
    app = create_app(config=TestConfig)
    return app


@pytest.fixture(autouse=True)
def db_session(app):
    """Provide a clean database for each test.

    Creates all tables, yields the db instance, then drops everything.
    """
    with app.app_context():
        _db.create_all()
        yield _db
        _db.session.rollback()
        _db.drop_all()


@pytest.fixture
def client(app):
    """Flask test client for HTTP endpoint tests."""
    return app.test_client()


@pytest.fixture
def sample_ticket(db_session):
    """Create a single ticket with basic metadata."""
    ticket = Ticket(
        zendesk_id='100001',
        status='Closed',
        created_date=date(2024, 6, 15),
        solved_date=date(2024, 6, 22),
        assignee='Marko Trapani',
        group_name='Support - L3',
    )
    db_session.session.add(ticket)
    db_session.session.commit()
    return ticket


@pytest.fixture
def enriched_ticket(db_session):
    """Create a ticket with STAR analysis fields populated."""
    ticket = Ticket(
        zendesk_id='100002',
        status='Closed',
        created_date=date(2024, 3, 10),
        solved_date=date(2024, 4, 5),
        assignee='Marko Trapani',
        subject='CRDB replication lag causing data inconsistency',
        customer_name='Acme Corp',
        summary='Customer reported active-active replication lag on CRDB cluster.',
        root_cause='The root cause was a network partition between regions. '
                   'RED-123456 was filed to track the fix. Cluster failover was needed.',
        steps_taken='Investigated replication metrics. Ran redis-cli debug commands. '
                    'Wrote a custom script to compare key counts across regions.',
        resolution='Resolved by triggering manual failover and patching the CRDB sync. '
                   'Filed RED-789012 for long-term fix. Customer confirmed recovery.',
        category='CRDB/Active-Active',
        enrichment_level='full',
        is_production=True,
    )
    db_session.session.add(ticket)
    db_session.session.commit()
    return ticket


@pytest.fixture
def ticket_with_notes(db_session):
    """Create a ticket with conversation notes attached."""
    ticket = Ticket(
        zendesk_id='100003',
        status='Closed',
        created_date=date(2024, 5, 1),
        solved_date=date(2024, 5, 10),
        assignee='Marko Trapani',
        subject='TLS certificate renewal failure',
    )
    db_session.session.add(ticket)
    db_session.session.flush()

    # Customer first message
    db_session.session.add(TicketNote(
        ticket_id=ticket.id,
        content='We are having issues renewing our TLS certificate on the Redis cluster.',
        note_type='conversation',
        author='John Customer',
        is_internal=False,
        created_at=datetime(2024, 5, 1, 10, 0, 0),
    ))
    # Bot auto-response
    db_session.session.add(TicketNote(
        ticket_id=ticket.id,
        content='This is an automated response. Please provide your subscription ID.',
        note_type='conversation',
        author='Redis Support Bot Agent',
        is_internal=False,
        created_at=datetime(2024, 5, 1, 10, 1, 0),
    ))
    # Agent reply
    db_session.session.add(TicketNote(
        ticket_id=ticket.id,
        content='Hi John, I see the TLS cert expired. Let me check the cluster config.',
        note_type='conversation',
        author='Marko Trapani',
        is_internal=False,
        created_at=datetime(2024, 5, 2, 14, 30, 0),
    ))
    # Internal note with escalation
    db_session.session.add(TicketNote(
        ticket_id=ticket.id,
        content='@l3_to_review - need engineering input on cert rotation.',
        note_type='conversation',
        author='Marko Trapani',
        is_internal=True,
        created_at=datetime(2024, 5, 3, 9, 0, 0),
    ))
    db_session.session.commit()
    return ticket


@pytest.fixture
def multiple_tickets(db_session):
    """Create several tickets for aggregate/search tests."""
    tickets = []
    for i, (zid, subj, cat, rc) in enumerate([
        ('200001', 'Cluster failover issue', 'Cluster',
         'Node failure caused shard migration. Outage detected.'),
        ('200002', 'TLS handshake timeout', 'TLS/SSL',
         'Certificate chain was incomplete. Escalated to R&D.'),
        ('200003', 'ArgoCD deployment failing', None,
         'Helm chart misconfigured redis.conf persistence settings.'),
        ('200004', 'Memory eviction on production', 'Performance',
         'maxmemory-policy was set to noeviction. Production outage ensued.'),
        ('200005', 'LDAP integration SSO issue', 'ACL/Auth',
         'LDAP bind DN was incorrect. Wrote a python script to test binds.'),
    ]):
        t = Ticket(
            zendesk_id=zid,
            status='Closed',
            created_date=date(2024, 1 + i, 1),
            solved_date=date(2024, 1 + i, 10 + i),
            assignee='Marko Trapani',
            subject=subj,
            category=cat,
            root_cause=rc,
            enrichment_level='full',
        )
        db_session.session.add(t)
        tickets.append(t)
    db_session.session.commit()
    return tickets
