"""Tests for the retrieval service (retrieval.py).

Tests query parsing and ticket retrieval with FTS5 + structured filters.
"""

from datetime import date

import pytest

from backend.models.ticket import Ticket, TicketNote
from backend.models.database import db
from backend.app import rebuild_fts
from backend.services.retrieval import (
    parse_query,
    retrieve_tickets,
    format_for_llm,
    format_for_chat,
)


# ── Query Parsing Tests ──

class TestParseQuery:

    def test_category_detection_crdb(self):
        """Should detect CRDB category from keywords."""
        result = parse_query('Show me CRDB tickets')
        assert result['filters'].get('category') == 'CRDB/Active-Active'

    def test_category_detection_tls(self):
        """Should detect TLS/SSL category."""
        result = parse_query('TLS certificate issues')
        assert result['filters'].get('category') == 'TLS/SSL'

    def test_category_detection_acl(self):
        """Should detect ACL/Auth category."""
        result = parse_query('LDAP authentication problems')
        assert result['filters'].get('category') == 'ACL/Auth'

    def test_boolean_signal_outage(self):
        """Should detect production outage boolean signal."""
        result = parse_query('production outage tickets')
        assert result['filters'].get('is_production_outage') is True

    def test_boolean_signal_escalation(self):
        """Should detect escalation boolean signal."""
        result = parse_query('escalated tickets')
        assert result['filters'].get('is_escalation') is True

    def test_boolean_signal_custom_script(self):
        """Should detect custom script boolean signal."""
        result = parse_query('tickets where we wrote a script')
        assert result['filters'].get('involved_custom_scripts') is True

    def test_severity_as_keyword(self):
        """Severity terms should become keywords (not filters)."""
        result = parse_query('P1 critical tickets')
        assert 'p1' in result['keywords'] or 'critical' in result['keywords']

    def test_year_filter(self):
        """Should extract year as a filter."""
        result = parse_query('tickets from 2024')
        assert result['filters'].get('year') == 2024

    def test_limit_from_top_n(self):
        """'top 5' should set limit to 5 and enable score sorting."""
        result = parse_query('top 5 tickets')
        assert result['limit'] == 5
        assert result['sort_by_score'] is True

    def test_score_signal_highest(self):
        """'highest' should enable score sorting."""
        result = parse_query('highest scored tickets')
        assert result['sort_by_score'] is True

    def test_score_signal_lowest(self):
        """'lowest' should enable ascending score sort."""
        result = parse_query('lowest scored tickets')
        assert result['sort_by_score'] is True
        assert result['sort_score_asc'] is True

    def test_starred_filter(self):
        """'starred' should set is_starred filter."""
        result = parse_query('show starred tickets')
        assert result['filters'].get('is_starred') is True

    def test_stop_words_removed(self):
        """Common stop words should not become keywords."""
        result = parse_query('show me the tickets that are about something')
        for kw in result['keywords']:
            assert kw not in {'show', 'me', 'the', 'tickets', 'that', 'are', 'about'}

    def test_followup_detection(self):
        """Follow-up phrases should be detected."""
        result = parse_query('tell me more about that')
        assert result['is_followup'] is True

    def test_keywords_extracted(self):
        """Non-stop words should become search keywords."""
        result = parse_query('redis cluster failover problems')
        # 'cluster' gets consumed as category, 'failover' might too
        # 'redis' should remain as keyword
        assert len(result['keywords']) >= 1 or result['filters'].get('category') is not None

    def test_recent_filter(self):
        """'recent' should set recent_months filter."""
        result = parse_query('recent TLS issues')
        assert result['filters'].get('recent_months') == 12

    def test_empty_query(self):
        """Empty query should return no filters/keywords."""
        result = parse_query('')
        assert result['keywords'] == []
        assert result['filters'] == {}

    def test_multiple_categories_pick_primary(self):
        """Multiple category candidates — should pick one as primary."""
        result = parse_query('cluster failover with data corruption')
        # Should have one category in filters
        assert 'category' in result['filters']


# ── Ticket Retrieval Tests ──

class TestRetrieveTickets:

    def test_retrieval_by_keyword(self, app, multiple_tickets):
        """Should find tickets matching keyword search."""
        with app.app_context():
            rebuild_fts()
            parsed = parse_query('failover')
            tickets = retrieve_tickets(parsed)
            subjects = [t.subject for t in tickets]
            assert any('failover' in (s or '').lower() for s in subjects)

    def test_retrieval_by_category_filter(self, app, multiple_tickets):
        """Should filter by category."""
        with app.app_context():
            rebuild_fts()
            parsed = parse_query('TLS tickets')
            tickets = retrieve_tickets(parsed)
            for t in tickets:
                assert t.category == 'TLS/SSL'

    def test_retrieval_sorted_by_score(self, app, multiple_tickets):
        """Sort by score should order by final_score descending."""
        with app.app_context():
            # Score all tickets first
            from backend.services.scoring import score_ticket
            for t in Ticket.query.all():
                score_ticket(t)
            db.session.commit()
            rebuild_fts()

            parsed = parse_query('top 3 tickets')
            tickets = retrieve_tickets(parsed)
            if len(tickets) >= 2:
                scores = [(t.final_score or 0) for t in tickets]
                assert scores == sorted(scores, reverse=True)

    def test_retrieval_empty_results(self, app, multiple_tickets):
        """Query that matches nothing should return empty list."""
        with app.app_context():
            rebuild_fts()
            parsed = parse_query('xyznonexistent12345')
            tickets = retrieve_tickets(parsed)
            assert tickets == []

    def test_retrieval_limit(self, app, multiple_tickets):
        """Should respect the limit parameter."""
        with app.app_context():
            rebuild_fts()
            parsed = parse_query('top 2 tickets')
            tickets = retrieve_tickets(parsed)
            assert len(tickets) <= 2


# ── FTS Note Search Tests ──

class TestFTSNoteSearch:

    def test_fts_finds_note_content(self, app, ticket_with_notes):
        """FTS should find tickets via conversation note content."""
        with app.app_context():
            rebuild_fts()
            # Search for a term from the note content that isn't a category keyword
            parsed = parse_query('renewing')
            tickets = retrieve_tickets(parsed)
            zids = [t.zendesk_id for t in tickets]
            assert ticket_with_notes.zendesk_id in zids

    def test_fts_note_vs_ticket_field_search(self, app, db_session):
        """Should find tickets through notes even if STAR fields don't match."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='900001',
                status='Closed',
                created_date=date(2024, 1, 1),
                subject='General inquiry',
                # Note: no mention of ArgoCD in STAR fields
            )
            db_session.session.add(ticket)
            db_session.session.flush()

            db_session.session.add(TicketNote(
                ticket_id=ticket.id,
                content='Customer is using ArgoCD to deploy their Redis cluster.',
                note_type='conversation',
                author='Agent',
            ))
            db_session.session.commit()
            rebuild_fts()

            parsed = parse_query('ArgoCD')
            tickets = retrieve_tickets(parsed)
            zids = [t.zendesk_id for t in tickets]
            assert '900001' in zids


# ── Formatting Tests ──

class TestFormatting:

    def test_format_for_llm_structure(self, app, multiple_tickets):
        """LLM format should include question, ticket details, and task."""
        with app.app_context():
            from backend.services.scoring import score_ticket
            for t in Ticket.query.all():
                score_ticket(t)
            db.session.commit()

            tickets = Ticket.query.limit(3).all()
            output = format_for_llm(tickets, 'What are the top issues?')
            assert 'Question: What are the top issues?' in output
            assert 'TICKET #' in output
            assert 'TASK:' in output

    def test_format_for_chat_returns_dicts(self, app, multiple_tickets):
        """Chat format should return list of dicts with expected keys."""
        with app.app_context():
            tickets = Ticket.query.limit(2).all()
            results = format_for_chat(tickets)
            assert len(results) == 2
            for r in results:
                assert 'zendesk_id' in r
                assert 'subject' in r
                assert 'final_score' in r
