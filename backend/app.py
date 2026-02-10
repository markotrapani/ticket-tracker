"""Flask application for Ticket Tracker."""

import json
import os
from datetime import datetime

from flask import Flask, render_template, request, jsonify, redirect, url_for
from werkzeug.utils import secure_filename

from backend.config import Config, ensure_dirs, ATTACHMENTS_DIR
from backend.models.database import db
from backend.models.ticket import Ticket, TicketTag, TicketNote, ScoreHistory
from backend.services import scoring, stats
from backend.services.csv_importer import import_csv
from backend.services.pdf_parser import parse_zendesk_pdf


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

    register_routes(app)
    return app


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
        notes = ticket.notes.order_by(TicketNote.created_at.desc()).all()
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
        return render_template('ticket_detail.html', ticket=ticket,
                               notes=notes, tags=tags, score_breakdown=score_breakdown)

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

        updatable = ['subject', 'customer_name', 'description', 'root_cause',
                     'resolution', 'category', 'product_area', 'severity',
                     'is_production_outage', 'is_escalation', 'involved_custom_scripts',
                     'interview_notes', 'skills_demonstrated']

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

        # Enrich ticket with parsed data
        if parsed.get('subject') and not ticket.subject:
            ticket.subject = parsed['subject']
        if parsed.get('customer_name') and not ticket.customer_name:
            ticket.customer_name = parsed['customer_name']
        if parsed.get('description'):
            ticket.description = parsed['description']

        ticket.enrichment_level = 'full' if ticket.description else 'partial'
        scoring.score_ticket(ticket)
        ticket.updated_at = datetime.utcnow()
        db.session.commit()

        return jsonify({
            'ticket_id': ticket_id,
            'parsed': {k: v for k, v in parsed.items() if k != 'full_text'},
            'ticket': ticket.to_dict(),
        })

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
        if data.get('resolution'):
            ticket.resolution = data['resolution']

        ticket.enrichment_level = 'full' if ticket.description and ticket.resolution else 'partial'
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
        return jsonify({'scored': count})

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
                enrichable = ['subject', 'customer_name', 'description', 'root_cause',
                              'resolution', 'category', 'product_area', 'severity']
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

                # Update enrichment level
                if ticket.description and ticket.resolution:
                    ticket.enrichment_level = 'full'
                elif ticket.description or ticket.subject:
                    ticket.enrichment_level = 'partial'

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

    # ── API: Search ──────────────────────────────────────────────

    @app.route('/api/search')
    def api_search():
        q = request.args.get('q', '').strip()
        if not q:
            return jsonify({'tickets': [], 'total': 0})

        like = f'%{q}%'
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
            )
        ).order_by(Ticket.final_score.desc().nullslast()).limit(50).all()

        return jsonify({
            'tickets': [t.to_dict() for t in results],
            'total': len(results),
        })

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
