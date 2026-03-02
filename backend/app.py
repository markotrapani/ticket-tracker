"""Flask application for Ticket Tracker."""

import json
import os
from datetime import datetime

from flask import Flask, render_template, request, jsonify, redirect, url_for
from werkzeug.utils import secure_filename

from backend.config import Config, ensure_dirs, ATTACHMENTS_DIR
from backend.models.database import db
from backend.models.ticket import Ticket, TicketTag, TicketNote, TicketJiraIssue, ScoreHistory
from backend.services import scoring, stats
from backend.services.csv_importer import import_csv
from backend.services.pdf_parser import parse_zendesk_pdf, synthesize_investigation, build_summary


def create_app(config=None):
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend', 'templates'),
        static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend', 'static'),
    )
    app.config.from_object(config or Config)
    ensure_dirs()
    db.init_app(app)

    with app.app_context():
        db.create_all()
        _init_fts(app)

    register_routes(app)
    return app


def _init_fts(app):
    """Initialize FTS5 virtual tables for full-text search.

    Always drops and recreates to handle schema changes (virtual tables
    can't be altered in SQLite).  Two tables:
      - tickets_fts: indexes Ticket table columns (STAR fields, metadata)
      - notes_fts:   indexes TicketNote content (conversation threads)
    """
    with db.engine.connect() as conn:
        conn.execute(db.text("DROP TABLE IF EXISTS tickets_fts"))
        conn.execute(db.text("""
            CREATE VIRTUAL TABLE tickets_fts USING fts5(
                zendesk_id, subject, customer_name, description,
                root_cause, steps_taken, resolution, interview_notes,
                category, engagement_type,
                content='tickets',
                content_rowid='id'
            )
        """))
        # notes_fts: only create if missing (rebuild_fts handles repopulation)
        exists = conn.execute(db.text(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='notes_fts'"
        )).scalar()
        if not exists:
            conn.execute(db.text("""
                CREATE VIRTUAL TABLE notes_fts USING fts5(
                    ticket_id UNINDEXED, note_content
                )
            """))
        conn.commit()


def rebuild_fts():
    """Rebuild FTS indexes from current ticket and note data.

    Drops and recreates FTS tables to avoid corruption issues, then
    repopulates from the source tables.
    """
    with db.engine.connect() as conn:
        # Drop and recreate to avoid stale/corrupt FTS state
        conn.execute(db.text("DROP TABLE IF EXISTS tickets_fts"))
        conn.execute(db.text("""
            CREATE VIRTUAL TABLE tickets_fts USING fts5(
                zendesk_id, subject, customer_name, description,
                root_cause, steps_taken, resolution, interview_notes,
                category, engagement_type,
                content='tickets',
                content_rowid='id'
            )
        """))
        conn.execute(db.text("""
            INSERT INTO tickets_fts(rowid, zendesk_id, subject, customer_name,
                description, root_cause, steps_taken, resolution, interview_notes,
                category, engagement_type)
            SELECT id, zendesk_id, COALESCE(subject,''), COALESCE(customer_name,''),
                COALESCE(description,''), COALESCE(root_cause,''),
                COALESCE(steps_taken,''), COALESCE(resolution,''),
                COALESCE(interview_notes,''), COALESCE(category,''),
                COALESCE(engagement_type,'')
            FROM tickets
        """))
        # notes_fts: standalone FTS5 table (stores its own content)
        # ticket_id is UNINDEXED (stored for joins but not searched)
        conn.execute(db.text("DROP TABLE IF EXISTS notes_fts"))
        conn.execute(db.text("""
            CREATE VIRTUAL TABLE notes_fts USING fts5(
                ticket_id UNINDEXED, note_content
            )
        """))
        conn.execute(db.text("""
            INSERT INTO notes_fts(ticket_id, note_content)
            SELECT ticket_id, COALESCE(content, '')
            FROM ticket_notes
        """))
        conn.commit()


def register_routes(app):
    # ── Page Routes ──────────────────────────────────────────────

    @app.route('/')
    def index():
        overview = stats.get_overview()
        top_tickets = stats.get_top_tickets(limit=10)
        recent = Ticket.query.order_by(Ticket.updated_at.desc()).limit(10).all()
        return render_template('index.html', overview=overview,
                               top_tickets=top_tickets, recent=recent)

    @app.route('/tickets')
    def tickets_list():
        return render_template('tickets.html')

    @app.route('/tickets/<zendesk_id>')
    def ticket_detail(zendesk_id):
        ticket = Ticket.query.filter_by(zendesk_id=zendesk_id).first_or_404()
        all_notes = ticket.notes.order_by(TicketNote.created_at.asc()).all()
        conversation = [n for n in all_notes if n.note_type == 'conversation']
        notes = [n for n in all_notes if n.note_type != 'conversation']
        notes.reverse()  # User notes: newest first
        tags = ticket.tag_list
        score_breakdown = None
        if ticket.auto_score is not None:
            meta_scorer = scoring.MetadataScorer()
            _, auto_comp = meta_scorer.score(ticket)
            content_comp = {}
            if ticket.content_score is not None:
                content_scorer = scoring.ContentScorer()
                _, content_comp = content_scorer.score(ticket)
            score_breakdown = {'auto': auto_comp, 'content': content_comp}

        # Parse related ticket IDs and check which exist in DB
        related_tickets = []
        if ticket.related_ticket_ids:
            import json
            try:
                refs = json.loads(ticket.related_ticket_ids)
                for ref in refs:
                    ref_ticket = Ticket.query.filter_by(zendesk_id=ref['id']).first()
                    related_tickets.append({
                        'id': ref['id'],
                        'context': ref.get('context', ''),
                        'exists': ref_ticket is not None,
                        'subject': ref_ticket.subject if ref_ticket else None,
                    })
            except (json.JSONDecodeError, TypeError):
                pass

        return render_template('ticket_detail.html', ticket=ticket,
                               notes=notes, conversation=conversation,
                               tags=tags, score_breakdown=score_breakdown,
                               related_tickets=related_tickets)

    @app.route('/import')
    def import_page():
        return render_template('import.html')

    @app.route('/stats')
    def stats_page():
        return render_template('stats.html')

    @app.route('/interview-prep')
    def interview_prep():
        starred = Ticket.query.filter_by(is_starred=True).order_by(Ticket.final_score.desc()).all()
        top = stats.get_top_tickets(limit=30, min_score=20)
        return render_template('interview_prep.html', starred=starred, top=top)

    @app.route('/manage')
    def manage_page():
        overview = stats.get_overview()
        return render_template('manage.html', overview=overview)

    @app.route('/chat')
    def chat_page():
        return render_template('chat.html')

    # ── API: Tickets ─────────────────────────────────────────────

    @app.route('/api/tickets')
    def api_tickets():
        query = Ticket.query
        q = request.args.get('q', '').strip()
        if q:
            like = f'%{q}%'
            query = query.filter(
                db.or_(
                    Ticket.zendesk_id.like(like),
                    Ticket.subject.like(like),
                    Ticket.customer_name.like(like),
                    Ticket.description.like(like),
                    Ticket.category.like(like),
                    Ticket.product_area.like(like),
                )
            )

        status_filter = request.args.get('status')
        if status_filter and status_filter != 'all':
            query = query.filter_by(status=status_filter)

        category = request.args.get('category')
        if category:
            query = query.filter_by(category=category)

        engagement_type = request.args.get('engagement_type')
        if engagement_type and engagement_type != 'all':
            query = query.filter_by(engagement_type=engagement_type)

        tag_filter = request.args.get('tag')
        if tag_filter:
            query = query.filter(Ticket.tags.any(TicketTag.tag == tag_filter.strip().lower()))

        enrichment = request.args.get('enrichment')
        if enrichment and enrichment != 'all':
            query = query.filter_by(enrichment_level=enrichment)

        starred = request.args.get('starred')
        if starred == 'true':
            query = query.filter_by(is_starred=True)

        min_score = request.args.get('min_score', type=float)
        if min_score is not None:
            query = query.filter(Ticket.final_score >= min_score)

        sort = request.args.get('sort', 'final_score')
        order = request.args.get('order', 'desc')
        sort_col = getattr(Ticket, sort, Ticket.final_score)
        if order == 'desc':
            query = query.order_by(sort_col.desc().nullslast())
        else:
            query = query.order_by(sort_col.asc().nullsfirst())

        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        per_page = min(per_page, 200)

        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        return jsonify({
            'tickets': [t.to_dict() for t in pagination.items],
            'total': pagination.total,
            'page': pagination.page,
            'pages': pagination.pages,
            'per_page': per_page,
        })

    @app.route('/api/tickets/<zendesk_id>')
    def api_ticket_get(zendesk_id):
        ticket = Ticket.query.filter_by(zendesk_id=zendesk_id).first_or_404()
        data = ticket.to_dict()
        data['notes'] = [n.to_dict() for n in ticket.notes.all()]
        return jsonify(data)

    @app.route('/api/tickets/<zendesk_id>', methods=['PUT'])
    def api_ticket_update(zendesk_id):
        ticket = Ticket.query.filter_by(zendesk_id=zendesk_id).first_or_404()
        data = request.get_json()

        updatable = ['subject', 'customer_name', 'summary', 'description', 'root_cause',
                     'steps_taken', 'resolution', 'category', 'engagement_type',
                     'product_area', 'priority', 'severity',
                     'is_production_outage', 'is_escalation', 'involved_custom_scripts',
                     'interview_notes', 'skills_demonstrated',
                     'product_line', 'additional_products', 'jira_ticket_ids',
                     'subscription_id', 'bdb_id', 'endpoint',
                     'customer_impact', 'issue_type',
                     'problem_summary_zd', 'resolution_summary_zd']

        for field in updatable:
            if field in data:
                setattr(ticket, field, data[field])

        # Update enrichment level based on what fields are filled
        if ticket.description or ticket.root_cause or ticket.resolution:
            if ticket.description and ticket.resolution:
                ticket.enrichment_level = 'full'
            else:
                ticket.enrichment_level = 'partial'

        # Manual score
        if 'manual_score' in data:
            ticket.manual_score = data['manual_score']

        # Recalculate scores
        scoring.score_ticket(ticket)

        ticket.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify(ticket.to_dict())

    @app.route('/api/tickets', methods=['POST'])
    def api_ticket_create():
        data = request.get_json()
        zendesk_id = data.get('zendesk_id')
        if not zendesk_id:
            return jsonify({'error': 'zendesk_id required'}), 400

        existing = Ticket.query.filter_by(zendesk_id=zendesk_id).first()
        if existing:
            return jsonify({'error': f'Ticket {zendesk_id} already exists'}), 409

        ticket = Ticket(
            zendesk_id=zendesk_id,
            status=data.get('status', 'Open'),
            group_name=data.get('group_name', 'Support - L3'),
            assignee=data.get('assignee', 'Marko Trapani'),
            created_date=datetime.strptime(data['created_date'], '%Y-%m-%d').date() if data.get('created_date') else datetime.utcnow().date(),
            solved_date=datetime.strptime(data['solved_date'], '%Y-%m-%d').date() if data.get('solved_date') else None,
            subject=data.get('subject'),
            customer_name=data.get('customer_name'),
            description=data.get('description'),
            root_cause=data.get('root_cause'),
            resolution=data.get('resolution'),
            zendesk_url=data.get('zendesk_url', f'https://redislabs.zendesk.com/agent/tickets/{zendesk_id}'),
            category=data.get('category'),
            product_area=data.get('product_area'),
            severity=data.get('severity'),
            enrichment_level='full' if data.get('description') else 'metadata_only',
        )
        db.session.add(ticket)
        db.session.flush()
        scoring.score_ticket(ticket)
        db.session.commit()
        return jsonify(ticket.to_dict()), 201

    @app.route('/api/tickets/<zendesk_id>', methods=['DELETE'])
    def api_ticket_delete(zendesk_id):
        ticket = Ticket.query.filter_by(zendesk_id=zendesk_id).first_or_404()
        db.session.delete(ticket)
        db.session.commit()
        return jsonify({'deleted': zendesk_id})

    # ── API: Notes ───────────────────────────────────────────────

    @app.route('/api/tickets/<zendesk_id>/notes', methods=['POST'])
    def api_add_note(zendesk_id):
        ticket = Ticket.query.filter_by(zendesk_id=zendesk_id).first_or_404()
        data = request.get_json()
        note = TicketNote(
            ticket_id=ticket.id,
            content=data['content'],
            note_type=data.get('note_type', 'general'),
        )
        db.session.add(note)
        db.session.commit()
        return jsonify(note.to_dict()), 201

    @app.route('/api/tickets/<zendesk_id>/notes/<int:note_id>', methods=['DELETE'])
    def api_delete_note(zendesk_id, note_id):
        note = TicketNote.query.get_or_404(note_id)
        db.session.delete(note)
        db.session.commit()
        return jsonify({'deleted': note_id})

    # ── API: Tags ────────────────────────────────────────────────

    @app.route('/api/tickets/<zendesk_id>/tags', methods=['POST'])
    def api_add_tags(zendesk_id):
        ticket = Ticket.query.filter_by(zendesk_id=zendesk_id).first_or_404()
        data = request.get_json()
        tags = data.get('tags', [])
        added = []
        for tag_name in tags:
            tag_name = tag_name.strip().lower()
            existing = TicketTag.query.filter_by(ticket_id=ticket.id, tag=tag_name).first()
            if not existing:
                tag = TicketTag(ticket_id=ticket.id, tag=tag_name)
                db.session.add(tag)
                added.append(tag_name)
        db.session.commit()
        return jsonify({'added': added})

    @app.route('/api/tickets/<zendesk_id>/tags/<tag>', methods=['DELETE'])
    def api_delete_tag(zendesk_id, tag):
        ticket = Ticket.query.filter_by(zendesk_id=zendesk_id).first_or_404()
        tag_obj = TicketTag.query.filter_by(ticket_id=ticket.id, tag=tag).first_or_404()
        db.session.delete(tag_obj)
        db.session.commit()
        return jsonify({'deleted': tag})

    # ── API: Star ────────────────────────────────────────────────

    @app.route('/api/tickets/<zendesk_id>/star', methods=['POST'])
    def api_toggle_star(zendesk_id):
        ticket = Ticket.query.filter_by(zendesk_id=zendesk_id).first_or_404()
        ticket.is_starred = not ticket.is_starred
        db.session.commit()
        return jsonify({'is_starred': ticket.is_starred})

    # ── API: Import ──────────────────────────────────────────────

    @app.route('/api/import/csv', methods=['POST'])
    def api_import_csv():
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        file = request.files['file']
        if not file.filename.endswith('.csv'):
            return jsonify({'error': 'File must be a CSV'}), 400

        filepath = os.path.join(ATTACHMENTS_DIR, secure_filename(file.filename))
        file.save(filepath)

        update = request.form.get('update_existing', 'true') == 'true'
        results = import_csv(filepath, update_existing=update)
        return jsonify(results)

    # Enrichment logic extracted to backend/services/enrichment.py for reuse by MCP server
    from backend.services.enrichment import enrich_ticket_from_parsed as _enrich_ticket_from_parsed

    @app.route('/api/import/pdf', methods=['POST'])
    def api_import_pdf():
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        file = request.files['file']
        if not file.filename.endswith('.pdf'):
            return jsonify({'error': 'File must be a PDF'}), 400

        filepath = os.path.join(ATTACHMENTS_DIR, secure_filename(file.filename))
        file.save(filepath)

        parsed = parse_zendesk_pdf(filepath)
        ticket_id = request.form.get('ticket_id') or parsed.get('ticket_id')
        if not ticket_id:
            return jsonify({'error': 'Could not determine ticket ID from PDF. Please provide it.'}), 400

        ticket = Ticket.query.filter_by(zendesk_id=ticket_id).first()
        if not ticket:
            return jsonify({'error': f'Ticket {ticket_id} not found in database. Import CSV first or create manually.'}), 404

        _enrich_ticket_from_parsed(ticket, parsed)
        db.session.commit()

        return jsonify({
            'ticket_id': ticket_id,
            'parsed': {k: v for k, v in parsed.items() if k not in ('full_text', 'comments')},
            'ticket': ticket.to_dict(),
            'comments_stored': len(parsed.get('comments', [])),
        })

    @app.route('/api/import/pdf/batch', methods=['POST'])
    def api_import_pdf_batch():
        """Upload multiple PDFs at once for batch enrichment."""
        if 'files' not in request.files:
            return jsonify({'error': 'No files uploaded'}), 400

        files = request.files.getlist('files')
        results = {'enriched': 0, 'not_found': [], 'no_id': [], 'errors': []}

        for file in files:
            if not file.filename.endswith('.pdf'):
                results['errors'].append(f'{file.filename}: not a PDF')
                continue

            filepath = os.path.join(ATTACHMENTS_DIR, secure_filename(file.filename))
            file.save(filepath)

            try:
                parsed = parse_zendesk_pdf(filepath)
                ticket_id = parsed.get('ticket_id')
                if not ticket_id:
                    results['no_id'].append(file.filename)
                    continue

                ticket = Ticket.query.filter_by(zendesk_id=ticket_id).first()
                if not ticket:
                    results['not_found'].append(ticket_id)
                    continue

                _enrich_ticket_from_parsed(ticket, parsed)
                results['enriched'] += 1

            except Exception as e:
                results['errors'].append(f'{file.filename}: {str(e)}')

        db.session.commit()
        return jsonify(results)

    @app.route('/api/utils/parse-zendesk-url', methods=['POST'])
    def api_parse_zendesk_url():
        """Extract ticket ID from a ZenDesk URL."""
        import re
        data = request.get_json()
        url = data.get('url', '')
        match = re.search(r'/tickets/(\d+)', url)
        if match:
            ticket_id = match.group(1)
            ticket = Ticket.query.filter_by(zendesk_id=ticket_id).first()
            return jsonify({
                'ticket_id': ticket_id,
                'exists': ticket is not None,
                'url': f'https://redislabs.zendesk.com/agent/tickets/{ticket_id}',
            })
        return jsonify({'error': 'No ticket ID found in URL'}), 400

    @app.route('/api/import/paste', methods=['POST'])
    def api_import_paste():
        data = request.get_json()
        zendesk_id = data.get('zendesk_id')
        content = data.get('content', '')
        if not zendesk_id:
            return jsonify({'error': 'zendesk_id required'}), 400

        ticket = Ticket.query.filter_by(zendesk_id=zendesk_id).first()
        if not ticket:
            return jsonify({'error': f'Ticket {zendesk_id} not found'}), 404

        if content:
            ticket.description = content
        if data.get('subject'):
            ticket.subject = data['subject']
        if data.get('customer_name'):
            ticket.customer_name = data['customer_name']
        if data.get('root_cause'):
            ticket.root_cause = data['root_cause']
        if data.get('steps_taken'):
            ticket.steps_taken = data['steps_taken']
        if data.get('resolution'):
            ticket.resolution = data['resolution']
        if data.get('summary'):
            ticket.summary = data['summary']

        has_desc = bool(ticket.description)
        has_res = bool(ticket.resolution or ticket.root_cause)
        if has_desc and has_res:
            ticket.enrichment_level = 'full'
        elif has_desc or has_res:
            ticket.enrichment_level = 'partial'
        # Don't set enrichment_level if no real content was added

        if not ticket.category:
            suggestion = stats.suggest_category(ticket)
            if suggestion.get('suggested') and suggestion.get('confidence', 0) >= 0.33:
                ticket.category = suggestion['suggested']

        scoring.score_ticket(ticket)
        ticket.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify(ticket.to_dict())

    # ── API: Bulk Operations ─────────────────────────────────────

    @app.route('/api/bulk/score', methods=['POST'])
    def api_bulk_score():
        tickets = Ticket.query.all()
        count = 0
        for ticket in tickets:
            scoring.score_ticket(ticket)
            count += 1
        db.session.commit()
        rebuild_fts()
        return jsonify({'scored': count})

    @app.route('/api/bulk/rebuild-fts', methods=['POST'])
    def api_rebuild_fts():
        """Rebuild the full-text search index."""
        rebuild_fts()
        return jsonify({'status': 'ok'})

    @app.route('/api/tickets/<zendesk_id>/reset-enrichment', methods=['POST'])
    def api_reset_enrichment(zendesk_id):
        """Reset a single ticket's enrichment back to metadata_only."""
        from backend.services.enrichment import reset_ticket_enrichment
        ticket = Ticket.query.filter_by(zendesk_id=zendesk_id).first_or_404()
        deleted = reset_ticket_enrichment(ticket)
        db.session.commit()
        return jsonify({
            'zendesk_id': zendesk_id,
            'deleted_notes': deleted,
            'new_score': ticket.final_score,
        })

    @app.route('/api/bulk/reset-enrichment', methods=['POST'])
    def api_bulk_reset_enrichment():
        """Reset ALL enriched tickets to metadata_only. Requires confirmation token."""
        from backend.services.enrichment import reset_ticket_enrichment
        data = request.get_json() or {}
        if data.get('confirm') != 'RESET_ALL_ENRICHMENT':
            return jsonify({'error': 'Confirmation required'}), 400
        tickets = Ticket.query.filter(Ticket.enrichment_level != 'metadata_only').all()
        count = 0
        total_notes = 0
        for ticket in tickets:
            deleted = reset_ticket_enrichment(ticket)
            total_notes += deleted
            count += 1
        db.session.commit()
        rebuild_fts()
        return jsonify({'reset': count, 'deleted_notes': total_notes})

    @app.route('/api/db/clear-all', methods=['POST'])
    def api_clear_all():
        """Delete ALL tickets, tags, notes, and scores. Auto-backup first."""
        data = request.get_json() or {}
        if data.get('confirm') != 'DELETE_ALL_DATA':
            return jsonify({'error': 'Confirmation required'}), 400
        import shutil
        from backend.config import DATA_DIR
        db_path = os.path.join(DATA_DIR, 'tickets.db')
        backup_name = f"tickets_pre_clear_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.db"
        backup_path = os.path.join(DATA_DIR, backup_name)
        if os.path.exists(db_path):
            shutil.copy2(db_path, backup_path)
        ScoreHistory.query.delete()
        TicketNote.query.delete()
        TicketTag.query.delete()
        Ticket.query.delete()
        db.session.commit()
        rebuild_fts()
        return jsonify({'cleared': True, 'backup': backup_name})

    # ── API: Enrichment (for MCP / automation) ─────────────────

    @app.route('/api/tickets/unenriched')
    def api_unenriched():
        """Return tickets that still need content enrichment.
        Designed for automation: Claude native app fetches this list,
        scrapes ZenDesk via MCP browser, then POSTs back to /api/enrich/bulk.
        """
        limit = request.args.get('limit', 50, type=int)
        limit = min(limit, 500)
        tickets = Ticket.query.filter_by(enrichment_level='metadata_only').order_by(
            Ticket.final_score.desc().nullslast()
        ).limit(limit).all()
        return jsonify({
            'tickets': [{'zendesk_id': t.zendesk_id, 'zendesk_url': t.zendesk_url,
                         'status': t.status, 'created_date': t.created_date.isoformat(),
                         'auto_score': t.auto_score} for t in tickets],
            'total_unenriched': Ticket.query.filter_by(enrichment_level='metadata_only').count(),
        })

    @app.route('/api/enrich/bulk', methods=['POST'])
    def api_enrich_bulk():
        """Bulk enrich tickets with scraped content.
        Accepts JSON array of objects, each with at minimum:
          { "zendesk_id": "12345", "subject": "...", "description": "..." }
        Optional fields: customer_name, root_cause, resolution, category,
                         product_area, severity, tags (array of strings)
        """
        data = request.get_json()
        if not isinstance(data, list):
            data = [data]

        results = {'enriched': 0, 'not_found': [], 'errors': []}

        for item in data:
            zendesk_id = item.get('zendesk_id')
            if not zendesk_id:
                results['errors'].append('Missing zendesk_id in item')
                continue

            ticket = Ticket.query.filter_by(zendesk_id=str(zendesk_id)).first()
            if not ticket:
                results['not_found'].append(zendesk_id)
                continue

            try:
                enrichable = ['subject', 'customer_name', 'summary', 'description', 'root_cause',
                              'steps_taken', 'resolution', 'category', 'product_area',
                              'priority', 'severity']
                for field in enrichable:
                    if item.get(field):
                        setattr(ticket, field, item[field])

                # Boolean flags
                if item.get('is_production_outage') is not None:
                    ticket.is_production_outage = bool(item['is_production_outage'])
                if item.get('is_escalation') is not None:
                    ticket.is_escalation = bool(item['is_escalation'])

                # Tags
                if item.get('tags'):
                    for tag_name in item['tags']:
                        tag_name = tag_name.strip().lower()
                        existing = TicketTag.query.filter_by(ticket_id=ticket.id, tag=tag_name).first()
                        if not existing:
                            db.session.add(TicketTag(ticket_id=ticket.id, tag=tag_name, source='auto'))

                # Update enrichment level based on actual content
                has_desc = bool(ticket.description)
                has_res = bool(ticket.resolution or ticket.root_cause)
                if has_desc and has_res:
                    ticket.enrichment_level = 'full'
                elif has_desc or has_res:
                    ticket.enrichment_level = 'partial'

                if not ticket.category:
                    suggestion = stats.suggest_category(ticket)
                    if suggestion.get('suggested') and suggestion.get('confidence', 0) >= 0.33:
                        ticket.category = suggestion['suggested']

                scoring.score_ticket(ticket)
                ticket.updated_at = datetime.utcnow()
                results['enriched'] += 1

            except Exception as e:
                results['errors'].append(f'{zendesk_id}: {str(e)}')

        db.session.commit()
        return jsonify(results)

    # ── API: Statistics ──────────────────────────────────────────

    @app.route('/api/stats/overview')
    def api_stats_overview():
        return jsonify(stats.get_overview())

    @app.route('/api/stats/timeline')
    def api_stats_timeline():
        return jsonify(stats.get_timeline())

    @app.route('/api/stats/score-distribution')
    def api_stats_score_dist():
        return jsonify(stats.get_score_distribution())

    @app.route('/api/stats/categories')
    def api_stats_categories():
        return jsonify(stats.get_category_breakdown())

    @app.route('/api/stats/resolution-times')
    def api_stats_resolution():
        return jsonify(stats.get_resolution_time_distribution())

    # ── API: Intelligence ──────────────────────────────────────────

    @app.route('/api/tickets/<zendesk_id>/suggest-category', methods=['POST'])
    def api_suggest_category(zendesk_id):
        """Auto-suggest a category based on ticket content keywords."""
        ticket = Ticket.query.filter_by(zendesk_id=zendesk_id).first_or_404()
        suggestion = stats.suggest_category(ticket)
        return jsonify(suggestion)

    @app.route('/api/tickets/<zendesk_id>/star-format')
    def api_star_format(zendesk_id):
        """Generate STAR format (Situation, Task, Action, Result) for interview prep."""
        ticket = Ticket.query.filter_by(zendesk_id=zendesk_id).first_or_404()
        star = stats.generate_star_format(ticket)
        return jsonify(star)

    @app.route('/api/stats/skill-gaps')
    def api_skill_gaps():
        """Analyze skill coverage gaps across enriched/starred tickets."""
        return jsonify(stats.get_skill_gaps())

    @app.route('/api/stats/best-tickets')
    def api_best_tickets():
        """Auto-generate a diverse list of best tickets to mention in interviews."""
        limit = request.args.get('limit', 15, type=int)
        return jsonify(stats.get_best_tickets(limit))

    @app.route('/api/stats/year-over-year')
    def api_year_over_year():
        return jsonify(stats.get_year_over_year())

    @app.route('/api/stats/resolution-trends')
    def api_resolution_trends():
        return jsonify(stats.get_resolution_trends())

    # ── API: Database Management ─────────────────────────────────

    @app.route('/api/db/backup', methods=['POST'])
    def api_db_backup():
        """Create a timestamped backup of the SQLite database."""
        import shutil
        from backend.config import DATA_DIR
        db_path = os.path.join(DATA_DIR, 'tickets.db')
        backup_name = f"tickets_backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.db"
        backup_path = os.path.join(DATA_DIR, backup_name)
        shutil.copy2(db_path, backup_path)
        return jsonify({'backup': backup_name, 'path': backup_path})

    @app.route('/api/db/backups')
    def api_db_list_backups():
        """List available database backups."""
        from backend.config import DATA_DIR
        backups = []
        for f in sorted(os.listdir(DATA_DIR)):
            if f.startswith('tickets_backup_') and f.endswith('.db'):
                path = os.path.join(DATA_DIR, f)
                backups.append({'name': f, 'size_mb': round(os.path.getsize(path) / 1024 / 1024, 2)})
        return jsonify(backups)

    # ── API: Search ──────────────────────────────────────────────

    @app.route('/api/search')
    def api_search():
        q = request.args.get('q', '').strip()
        if not q:
            return jsonify({'tickets': [], 'total': 0})

        # Try FTS5 first (tickets + notes), fall back to LIKE
        try:
            # Search ticket fields
            fts_query = db.text("""
                SELECT t.id FROM tickets t
                JOIN tickets_fts fts ON t.id = fts.rowid
                WHERE tickets_fts MATCH :query
            """)
            ticket_rows = db.session.execute(fts_query, {'query': q}).fetchall()
            ticket_ids = {r.id for r in ticket_rows}

            # Search conversation notes
            notes_query = db.text("""
                SELECT DISTINCT ticket_id FROM notes_fts
                WHERE notes_fts MATCH :query
            """)
            note_rows = db.session.execute(notes_query, {'query': q}).fetchall()
            ticket_ids.update(r.ticket_id for r in note_rows)

            if ticket_ids:
                results = Ticket.query.filter(Ticket.id.in_(ticket_ids)).order_by(
                    Ticket.final_score.desc().nullslast()
                ).limit(50).all()
                return jsonify({'tickets': [t.to_dict() for t in results], 'total': len(results)})
        except Exception:
            pass  # FTS not populated yet or query syntax issue

        # LIKE fallback (includes notes)
        like = f'%{q}%'
        note_ticket_ids = db.session.query(TicketNote.ticket_id).filter(
            TicketNote.content.ilike(like)
        ).distinct().subquery()
        results = Ticket.query.filter(
            db.or_(
                Ticket.zendesk_id.like(like),
                Ticket.subject.like(like),
                Ticket.customer_name.like(like),
                Ticket.description.like(like),
                Ticket.root_cause.like(like),
                Ticket.resolution.like(like),
                Ticket.category.like(like),
                Ticket.interview_notes.like(like),
                Ticket.id.in_(note_ticket_ids),
            )
        ).order_by(Ticket.final_score.desc().nullslast()).limit(50).all()

        return jsonify({
            'tickets': [t.to_dict() for t in results],
            'total': len(results),
        })

    # ── API: Chat ───────────────────────────────────────────────

    @app.route('/api/chat/retrieve', methods=['POST'])
    def api_chat_retrieve():
        """Smart retrieval: parse question, search DB, return formatted context."""
        data = request.get_json()
        question = (data or {}).get('question', '').strip()
        if not question:
            return jsonify({'error': 'Question required'}), 400

        from backend.services.retrieval import query_tickets
        result = query_tickets(question)
        return jsonify(result)

    @app.route('/api/chat/ollama', methods=['POST'])
    def api_chat_ollama():
        """Send question + retrieved context to Ollama, stream response via SSE."""
        import requests as http_requests
        data = request.get_json()
        question = (data or {}).get('question', '').strip()
        model = (data or {}).get('model', 'mistral-nemo')
        history = (data or {}).get('history', [])
        provided_context = (data or {}).get('context', '')
        if not question:
            return jsonify({'error': 'Question required'}), 400

        # Use provided context (for follow-ups) or retrieve fresh
        if provided_context:
            context = provided_context
        else:
            from backend.services.retrieval import query_tickets
            result = query_tickets(question)
            context = result['formatted_context']

        system_prompt = (
            "You are a support ticket analysis assistant. You help a Redis Cloud L3 support engineer "
            "review and discuss their past support tickets.\n\n"
            "IMPORTANT RULES:\n"
            "- You will receive retrieved ticket data from the engineer's ticket database.\n"
            "- ANSWER THE USER'S QUESTION DIRECTLY. If they ask about customers, list the customers. "
            "If they ask about patterns, describe patterns. If they ask for a summary, summarize.\n"
            "- Do NOT re-summarize tickets unless the user specifically asks for a summary.\n"
            "- Always reference specific tickets by number (e.g. #153987).\n"
            "- When multiple tickets match, identify patterns and themes if relevant.\n"
            "- Be concise and technical. Use bullet points for clarity.\n"
            "- Do NOT make up information not present in the ticket data.\n"
            "- Do NOT answer general Redis questions — only discuss the retrieved tickets."
        )

        # Build message list for multi-turn conversation
        ollama_messages = [{'role': 'system', 'content': system_prompt}]

        # Add conversation history (previous Q&A pairs)
        for msg in history[-6:]:  # Keep last 3 exchanges to fit context
            ollama_messages.append({
                'role': msg.get('role', 'user'),
                'content': msg.get('content', '')
            })

        # Current turn: context + question
        if provided_context:
            # Follow-up: strip the old TASK instruction from reused context
            # so it doesn't override the actual follow-up question
            import re
            clean_context = re.split(r'\n---\nTASK:', context)[0]
            user_content = (
                f"{clean_context}\n\n---\n"
                f"Based on the ticket data above, answer this question directly: {question}"
            )
        else:
            user_content = context
        ollama_messages.append({
            'role': 'user',
            'content': user_content
        })

        def generate():
            try:
                resp = http_requests.post('http://localhost:11434/api/chat', json={
                    'model': model,
                    'messages': ollama_messages,
                    'stream': True,
                    'options': {'num_ctx': 16384},
                }, stream=True, timeout=120)
                for line in resp.iter_lines():
                    if line:
                        yield f"data: {line.decode()}\n\n"
            except Exception as e:
                import json
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield "data: [DONE]\n\n"

        from flask import Response
        return Response(generate(), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.route('/api/chat/ollama/health')
    def api_ollama_health():
        """Check if Ollama is running and list available models."""
        try:
            import requests as http_requests
            resp = http_requests.get('http://localhost:11434/api/tags', timeout=2)
            if resp.ok:
                models = resp.json().get('models', [])
                return jsonify({
                    'available': True,
                    'models': [m['name'] for m in models],
                })
        except Exception:
            pass
        return jsonify({'available': False, 'models': []})

    # ── API: Export ──────────────────────────────────────────────

    @app.route('/api/export')
    def api_export():
        fmt = request.args.get('format', 'json')
        starred = request.args.get('starred') == 'true'
        min_score = request.args.get('min_score', 0, type=float)

        query = Ticket.query.filter(Ticket.final_score >= min_score)
        if starred:
            query = query.filter_by(is_starred=True)
        tickets = query.order_by(Ticket.final_score.desc()).all()

        if fmt == 'json':
            return jsonify([t.to_dict() for t in tickets])
        elif fmt == 'csv':
            import csv
            import io
            from flask import Response
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['Ticket ID', 'Subject', 'Customer', 'Status', 'Category',
                             'Product Area', 'Created', 'Solved', 'Resolution Days',
                             'Score', 'Enrichment', 'Starred', 'Tags', 'Interview Notes'])
            for t in tickets:
                writer.writerow([t.zendesk_id, t.subject or '', t.customer_name or '',
                                 t.status, t.category or '', t.product_area or '',
                                 t.created_date, t.solved_date or '', t.resolution_days or '',
                                 t.final_score or '', t.enrichment_level, t.is_starred,
                                 ', '.join(t.tag_list), t.interview_notes or ''])
            return Response(output.getvalue(), mimetype='text/csv',
                            headers={'Content-Disposition': 'attachment; filename=tickets_export.csv'})
        elif fmt == 'markdown':
            lines = ['# Ticket Tracker Export\n']
            for t in tickets:
                lines.append(f'## [{t.zendesk_id}]({t.zendesk_url}) - {t.subject or "No subject"}')
                lines.append(f'**Score:** {t.final_score} | **Status:** {t.status} | '
                             f'**Created:** {t.created_date} | **Resolved:** {t.solved_date or "Open"}')
                if t.customer_name:
                    lines.append(f'**Customer:** {t.customer_name}')
                if t.category:
                    lines.append(f'**Category:** {t.category}')
                if t.description:
                    lines.append(f'\n{t.description[:500]}')
                if t.resolution:
                    lines.append(f'\n**Resolution:** {t.resolution[:300]}')
                if t.interview_notes:
                    lines.append(f'\n**Interview Notes:** {t.interview_notes}')
                lines.append('\n---\n')
            from flask import Response
            return Response('\n'.join(lines), mimetype='text/markdown',
                            headers={'Content-Disposition': 'attachment; filename=tickets_export.md'})

        return jsonify({'error': 'Unsupported format'}), 400


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, port=5000)
