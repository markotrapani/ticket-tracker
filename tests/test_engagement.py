"""Tests for the engagement_type feature.

Tests engagement type auto-detection (backfill logic), API filtering,
model serialization, retrieval query parsing, and UI/API integration.
"""

import re
from datetime import date, datetime

import pytest

from backend.models.ticket import Ticket, TicketTag, TicketNote
from backend.models.database import db
from backend.app import rebuild_fts
from backend.services.retrieval import parse_query, retrieve_tickets, format_for_llm, format_for_chat


# ── Engagement Type Detection Patterns (mirrors cli.py backfill) ──

def detect_engagement_type(ticket, notes_text=''):
    """Standalone engagement detection logic matching cli.py backfill.

    Extracted here so tests can validate the classification rules
    without running the full backfill CLI command.
    """
    subject_lower = (ticket.subject or '').lower()
    star_text = ' '.join(filter(None, [
        ticket.summary, ticket.root_cause,
        ticket.steps_taken, ticket.resolution,
    ])).lower()
    all_text = subject_lower + ' ' + star_text + ' ' + notes_text

    if any(kw in all_text for kw in [
        'competitive eval', 'competitive analysis', 'compared to',
        'vs elasticache', 'vs memcached', 'vs dragonfly',
        'vs keydb', 'alternative to',
    ]):
        return 'competitive'
    elif any(kw in all_text for kw in [
        'poc ', ' poc', 'proof of concept', 'proof of value',
        'pov ', ' pov',
    ]) or any(kw in subject_lower for kw in ['poc', 'pov']):
        return 'poc'
    elif any(kw in all_text for kw in [
        'evaluation', 'evaluating', 'benchmark', 'benchmarking',
        'load test', 'load-test', 'performance test',
        'memtier_benchmark', 'redis-benchmark',
    ]):
        return 'evaluation'
    elif any(kw in all_text for kw in [
        'trial license', 'trial period', 'trial expir',
        'free trial', 'trial account',
    ]):
        return 'trial'
    elif any(kw in all_text for kw in [
        'migration', 'migrating', 'cutover', 'cut-over',
        'migrate from', 'migrate to', 'data migration',
    ]):
        return 'migration'
    elif any(kw in all_text for kw in [
        'demo environment', 'demo cluster', 'demonstration',
        'demo setup', 'sandbox',
    ]) or 'demo' in subject_lower.split():
        return 'demo'
    elif any(kw in all_text for kw in [
        'pilot program', 'pilot project', 'pilot phase',
        'pilot deployment',
    ]):
        return 'pilot'
    elif ticket.is_production_outage or ticket.is_production:
        return 'production'
    return None


# ── Model Tests ──

class TestEngagementTypeModel:

    def test_engagement_type_field_exists(self, app, db_session):
        """Ticket model should have engagement_type column."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='ENG001',
                status='Closed',
                created_date=date(2024, 1, 1),
                engagement_type='poc',
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            assert ticket.engagement_type == 'poc'

    def test_engagement_type_nullable(self, app, db_session):
        """engagement_type should be nullable (most tickets won't have one)."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='ENG002',
                status='Closed',
                created_date=date(2024, 1, 1),
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            assert ticket.engagement_type is None

    def test_engagement_type_in_to_dict(self, app, db_session):
        """to_dict() should include engagement_type."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='ENG003',
                status='Closed',
                created_date=date(2024, 1, 1),
                engagement_type='evaluation',
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            d = ticket.to_dict()
            assert 'engagement_type' in d
            assert d['engagement_type'] == 'evaluation'

    def test_engagement_type_none_in_to_dict(self, app, db_session):
        """to_dict() should return None for unset engagement_type."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='ENG004',
                status='Closed',
                created_date=date(2024, 1, 1),
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            d = ticket.to_dict()
            assert d['engagement_type'] is None


# ── Detection Logic Tests ──

class TestEngagementDetection:

    def test_detect_poc_in_subject(self, app, db_session):
        """POC in subject should classify as poc."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='DET001', status='Closed',
                created_date=date(2024, 1, 1),
                subject='POC Required - Customer Demo',
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            assert detect_engagement_type(ticket) == 'poc'

    def test_detect_poc_in_star_fields(self, app, db_session):
        """'proof of concept' in summary should classify as poc."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='DET002', status='Closed',
                created_date=date(2024, 1, 1),
                subject='Redis cluster setup help',
                summary='Customer is running a proof of concept for Redis Cloud.',
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            assert detect_engagement_type(ticket) == 'poc'

    def test_detect_poc_in_notes(self, app, db_session):
        """POC keyword in conversation notes should classify as poc."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='DET003', status='Closed',
                created_date=date(2024, 1, 1),
                subject='General inquiry',
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            notes_text = 'this is a poc environment we set up for testing'
            assert detect_engagement_type(ticket, notes_text) == 'poc'

    def test_detect_evaluation(self, app, db_session):
        """Benchmark/evaluation keywords should classify as evaluation."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='DET004', status='Closed',
                created_date=date(2024, 1, 1),
                subject='memtier_benchmark throughput issues',
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            assert detect_engagement_type(ticket) == 'evaluation'

    def test_detect_migration(self, app, db_session):
        """Migration keywords should classify as migration."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='DET005', status='Closed',
                created_date=date(2024, 1, 1),
                steps_taken='Assisted customer with data migration from ElastiCache.',
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            assert detect_engagement_type(ticket) == 'migration'

    def test_detect_trial(self, app, db_session):
        """Trial keywords should classify as trial."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='DET006', status='Closed',
                created_date=date(2024, 1, 1),
                summary='Customer free trial account needs extension.',
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            assert detect_engagement_type(ticket) == 'trial'

    def test_detect_competitive(self, app, db_session):
        """Competitive evaluation keywords should classify as competitive."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='DET007', status='Closed',
                created_date=date(2024, 1, 1),
                summary='Customer comparing Redis Cloud vs ElastiCache performance.',
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            assert detect_engagement_type(ticket) == 'competitive'

    def test_detect_demo(self, app, db_session):
        """Demo keywords should classify as demo."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='DET008', status='Closed',
                created_date=date(2024, 1, 1),
                subject='demo environment setup',
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            assert detect_engagement_type(ticket) == 'demo'

    def test_detect_pilot(self, app, db_session):
        """Pilot keywords should classify as pilot."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='DET009', status='Closed',
                created_date=date(2024, 1, 1),
                summary='Customer is in pilot phase with Redis Enterprise.',
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            assert detect_engagement_type(ticket) == 'pilot'

    def test_detect_production_from_outage(self, app, db_session):
        """Production outage tickets should classify as production."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='DET010', status='Closed',
                created_date=date(2024, 1, 1),
                is_production_outage=True,
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            assert detect_engagement_type(ticket) == 'production'

    def test_detect_production_from_is_production(self, app, db_session):
        """Tickets with is_production=True should classify as production."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='DET011', status='Closed',
                created_date=date(2024, 1, 1),
                is_production=True,
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            assert detect_engagement_type(ticket) == 'production'

    def test_no_match_returns_none(self, app, db_session):
        """Ticket without engagement signals should return None."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='DET012', status='Closed',
                created_date=date(2024, 1, 1),
                subject='General Redis question',
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            assert detect_engagement_type(ticket) is None

    def test_competitive_takes_priority_over_evaluation(self, app, db_session):
        """Competitive should win over evaluation when both match."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='DET013', status='Closed',
                created_date=date(2024, 1, 1),
                summary='Customer doing a competitive eval benchmark vs ElastiCache.',
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            assert detect_engagement_type(ticket) == 'competitive'

    def test_poc_takes_priority_over_migration(self, app, db_session):
        """POC should win over migration when both match."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='DET014', status='Closed',
                created_date=date(2024, 1, 1),
                subject='POC migration from ElastiCache',
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            assert detect_engagement_type(ticket) == 'poc'

    def test_demo_word_boundary(self, app, db_session):
        """'demo' should only match as a word in subject, not as substring."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='DET015', status='Closed',
                created_date=date(2024, 1, 1),
                subject='democratic process',  # contains 'demo' but not as word
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            # 'demo' in subject_lower.split() checks word boundaries
            assert detect_engagement_type(ticket) is None


# ── API Filter Tests ──

class TestEngagementAPIFilter:

    def _create_tickets(self, db_session):
        """Helper to create tickets with various engagement types."""
        tickets = []
        for zid, eng_type in [
            ('API001', 'poc'),
            ('API002', 'poc'),
            ('API003', 'evaluation'),
            ('API004', 'migration'),
            ('API005', None),
        ]:
            t = Ticket(
                zendesk_id=zid, status='Closed',
                created_date=date(2024, 1, 1),
                engagement_type=eng_type,
            )
            db_session.session.add(t)
            tickets.append(t)
        db_session.session.commit()
        return tickets

    def test_filter_by_engagement_type(self, app, client, db_session):
        """GET /api/tickets?engagement_type=poc should return only poc tickets."""
        with app.app_context():
            self._create_tickets(db_session)
            resp = client.get('/api/tickets?engagement_type=poc')
            data = resp.get_json()
            assert data['total'] == 2
            for t in data['tickets']:
                assert t['engagement_type'] == 'poc'

    def test_filter_by_engagement_type_evaluation(self, app, client, db_session):
        """Should return only evaluation tickets."""
        with app.app_context():
            self._create_tickets(db_session)
            resp = client.get('/api/tickets?engagement_type=evaluation')
            data = resp.get_json()
            assert data['total'] == 1
            assert data['tickets'][0]['engagement_type'] == 'evaluation'

    def test_no_filter_returns_all(self, app, client, db_session):
        """Without engagement_type filter, all tickets should be returned."""
        with app.app_context():
            self._create_tickets(db_session)
            resp = client.get('/api/tickets')
            data = resp.get_json()
            assert data['total'] == 5

    def test_filter_engagement_all_returns_all(self, app, client, db_session):
        """engagement_type=all should not filter."""
        with app.app_context():
            self._create_tickets(db_session)
            resp = client.get('/api/tickets?engagement_type=all')
            data = resp.get_json()
            assert data['total'] == 5

    def test_update_engagement_type(self, app, client, db_session):
        """PUT should allow updating engagement_type."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='API010', status='Closed',
                created_date=date(2024, 1, 1),
            )
            db_session.session.add(ticket)
            db_session.session.commit()

            resp = client.put('/api/tickets/API010',
                              json={'engagement_type': 'poc'},
                              content_type='application/json')
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['engagement_type'] == 'poc'

            # Verify it persisted
            ticket_reloaded = Ticket.query.filter_by(zendesk_id='API010').first()
            assert ticket_reloaded.engagement_type == 'poc'

    def test_clear_engagement_type(self, app, client, db_session):
        """PUT with engagement_type=null should clear it."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='API011', status='Closed',
                created_date=date(2024, 1, 1),
                engagement_type='evaluation',
            )
            db_session.session.add(ticket)
            db_session.session.commit()

            resp = client.put('/api/tickets/API011',
                              json={'engagement_type': None},
                              content_type='application/json')
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['engagement_type'] is None


# ── Tag Filter API Tests ──

class TestTagAPIFilter:

    def test_filter_by_tag(self, app, client, db_session):
        """GET /api/tickets?tag=crdb should return only tagged tickets."""
        with app.app_context():
            t1 = Ticket(zendesk_id='TAG001', status='Closed', created_date=date(2024, 1, 1))
            t2 = Ticket(zendesk_id='TAG002', status='Closed', created_date=date(2024, 1, 1))
            db_session.session.add_all([t1, t2])
            db_session.session.flush()

            db_session.session.add(TicketTag(ticket_id=t1.id, tag='crdb'))
            db_session.session.commit()

            resp = client.get('/api/tickets?tag=crdb')
            data = resp.get_json()
            assert data['total'] == 1
            assert data['tickets'][0]['zendesk_id'] == 'TAG001'

    def test_tag_filter_case_insensitive(self, app, client, db_session):
        """Tag filter should be case-insensitive."""
        with app.app_context():
            t = Ticket(zendesk_id='TAG003', status='Closed', created_date=date(2024, 1, 1))
            db_session.session.add(t)
            db_session.session.flush()
            db_session.session.add(TicketTag(ticket_id=t.id, tag='performance'))
            db_session.session.commit()

            # Tags are stored lowercase; filter should trim and lowercase
            resp = client.get('/api/tickets?tag=Performance')
            data = resp.get_json()
            assert data['total'] == 1


# ── Retrieval / Query Parsing Tests ──

class TestEngagementQueryParsing:

    def test_parse_poc_query(self):
        """'poc tickets' should set engagement_type filter."""
        result = parse_query('show me poc tickets')
        assert result['filters'].get('engagement_type') == 'poc'

    def test_parse_proof_of_concept_query(self):
        """'proof of concept' should map to poc."""
        result = parse_query('proof of concept projects')
        assert result['filters'].get('engagement_type') == 'poc'

    def test_parse_evaluation_query(self):
        """'evaluation' should set engagement_type filter."""
        result = parse_query('evaluation tickets')
        assert result['filters'].get('engagement_type') == 'evaluation'

    def test_parse_benchmark_maps_to_evaluation(self):
        """'benchmark' should map to evaluation engagement type."""
        result = parse_query('benchmark results')
        assert result['filters'].get('engagement_type') == 'evaluation'

    def test_parse_migration_query(self):
        """'migration' should set engagement_type filter."""
        result = parse_query('migration tickets')
        assert result['filters'].get('engagement_type') == 'migration'

    def test_parse_competitive_query(self):
        """'competitive eval' should map to competitive."""
        result = parse_query('competitive eval tickets')
        assert result['filters'].get('engagement_type') == 'competitive'

    def test_parse_demo_query(self):
        """'demo' should set engagement_type filter."""
        result = parse_query('demo environment issues')
        assert result['filters'].get('engagement_type') == 'demo'

    def test_parse_pilot_query(self):
        """'pilot' should set engagement_type filter."""
        result = parse_query('pilot program tickets')
        assert result['filters'].get('engagement_type') == 'pilot'

    def test_parse_trial_query(self):
        """'trial' should set engagement_type filter."""
        result = parse_query('trial account issues')
        assert result['filters'].get('engagement_type') == 'trial'

    def test_longer_phrase_matched_first(self):
        """'proof of concept' should be matched before 'poc'."""
        result = parse_query('proof of concept evaluation')
        # 'proof of concept' is longer, matched first -> poc
        assert result['filters'].get('engagement_type') == 'poc'

    def test_no_engagement_in_unrelated_query(self):
        """Unrelated queries should not trigger engagement filter."""
        result = parse_query('cluster failover issues')
        assert 'engagement_type' not in result['filters']


# ── Retrieval with Engagement Filter ──

class TestEngagementRetrieval:

    def _setup_tickets(self, db_session):
        """Create tickets with various engagement types for retrieval tests."""
        for zid, eng, subj in [
            ('RET001', 'poc', 'POC cluster setup'),
            ('RET002', 'poc', 'POC performance issues'),
            ('RET003', 'evaluation', 'Benchmark evaluation results'),
            ('RET004', 'migration', 'Data migration from ElastiCache'),
            ('RET005', None, 'General cluster issue'),
        ]:
            t = Ticket(
                zendesk_id=zid, status='Closed',
                created_date=date(2024, 1, 1),
                engagement_type=eng,
                subject=subj,
                enrichment_level='full',
            )
            db_session.session.add(t)
        db_session.session.commit()

    def test_retrieve_filters_by_engagement(self, app, db_session):
        """retrieve_tickets should filter by engagement_type."""
        with app.app_context():
            self._setup_tickets(db_session)
            rebuild_fts()

            parsed = parse_query('show me poc tickets')
            tickets = retrieve_tickets(parsed)
            for t in tickets:
                assert t.engagement_type == 'poc'

    def test_retrieve_evaluation_tickets(self, app, db_session):
        """Should retrieve only evaluation tickets."""
        with app.app_context():
            self._setup_tickets(db_session)
            rebuild_fts()

            parsed = parse_query('benchmark evaluation')
            tickets = retrieve_tickets(parsed)
            eng_types = {t.engagement_type for t in tickets}
            assert 'evaluation' in eng_types


# ── Formatting Tests ──

class TestEngagementFormatting:

    def test_format_for_llm_includes_engagement(self, app, db_session):
        """LLM output format should include Engagement field."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='FMT001', status='Closed',
                created_date=date(2024, 1, 1),
                engagement_type='poc',
                subject='POC test',
            )
            db_session.session.add(ticket)
            db_session.session.commit()

            output = format_for_llm([ticket], 'test question')
            assert 'Engagement: poc' in output

    def test_format_for_llm_engagement_na(self, app, db_session):
        """LLM format should show N/A for missing engagement_type."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='FMT002', status='Closed',
                created_date=date(2024, 1, 1),
                subject='General issue',
            )
            db_session.session.add(ticket)
            db_session.session.commit()

            output = format_for_llm([ticket], 'test question')
            assert 'Engagement: N/A' in output

    def test_format_for_chat_includes_engagement(self, app, db_session):
        """Chat format should include engagement_type key."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='FMT003', status='Closed',
                created_date=date(2024, 1, 1),
                engagement_type='migration',
            )
            db_session.session.add(ticket)
            db_session.session.commit()

            results = format_for_chat([ticket])
            assert len(results) == 1
            assert results[0]['engagement_type'] == 'migration'

    def test_format_for_chat_engagement_none(self, app, db_session):
        """Chat format should return None for unset engagement_type."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='FMT004', status='Closed',
                created_date=date(2024, 1, 1),
            )
            db_session.session.add(ticket)
            db_session.session.commit()

            results = format_for_chat([ticket])
            assert results[0]['engagement_type'] is None


# ── FTS5 Index Tests ──

class TestEngagementFTS:

    def test_fts_indexes_engagement_type(self, app, db_session):
        """FTS5 rebuild should include engagement_type in the index."""
        with app.app_context():
            ticket = Ticket(
                zendesk_id='FTS001', status='Closed',
                created_date=date(2024, 1, 1),
                engagement_type='competitive',
                subject='Standard issue',
            )
            db_session.session.add(ticket)
            db_session.session.commit()
            rebuild_fts()

            # Search for 'competitive' which only exists in engagement_type
            parsed = parse_query('competitive')
            tickets = retrieve_tickets(parsed)
            # The parser will set engagement_type filter, but it should
            # also be findable via FTS
            zids = [t.zendesk_id for t in tickets]
            assert 'FTS001' in zids
