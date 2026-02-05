"""Statistics service for dashboard and analytics."""

from datetime import date
from collections import Counter

from sqlalchemy import func, case
from backend.models.database import db
from backend.models.ticket import Ticket


def get_overview():
    """Return summary statistics for the dashboard."""
    total = Ticket.query.count()
    enriched = Ticket.query.filter(Ticket.enrichment_level != 'metadata_only').count()
    starred = Ticket.query.filter_by(is_starred=True).count()
    avg_score = db.session.query(func.avg(Ticket.final_score)).scalar() or 0

    status_counts = dict(
        db.session.query(Ticket.status, func.count(Ticket.id))
        .group_by(Ticket.status).all()
    )

    avg_resolution = db.session.query(
        func.avg(func.julianday(Ticket.solved_date) - func.julianday(Ticket.created_date))
    ).filter(Ticket.solved_date.isnot(None)).scalar() or 0

    return {
        'total': total,
        'enriched': enriched,
        'starred': starred,
        'avg_score': round(avg_score, 1),
        'status_counts': status_counts,
        'avg_resolution_days': round(avg_resolution, 1),
    }


def get_timeline():
    """Return monthly ticket volume."""
    rows = db.session.query(
        func.strftime('%Y-%m', Ticket.created_date).label('month'),
        func.count(Ticket.id).label('count')
    ).group_by('month').order_by('month').all()

    return [{'month': r.month, 'count': r.count} for r in rows]


def get_score_distribution():
    """Return score distribution in buckets of 10."""
    tickets = Ticket.query.filter(Ticket.final_score.isnot(None)).all()
    buckets = Counter()
    for t in tickets:
        bucket = int(t.final_score // 10) * 10
        bucket = min(bucket, 90)  # cap label at 90-100
        buckets[f'{bucket}-{bucket+10}'] = buckets.get(f'{bucket}-{bucket+10}', 0) + 1

    labels = [f'{i}-{i+10}' for i in range(0, 100, 10)]
    return [{'range': label, 'count': buckets.get(label, 0)} for label in labels]


def get_category_breakdown():
    """Return ticket counts by category."""
    rows = db.session.query(
        func.coalesce(Ticket.category, 'Uncategorized').label('category'),
        func.count(Ticket.id).label('count'),
        func.avg(Ticket.final_score).label('avg_score')
    ).group_by('category').order_by(func.count(Ticket.id).desc()).all()

    return [{'category': r.category, 'count': r.count,
             'avg_score': round(r.avg_score, 1) if r.avg_score else 0} for r in rows]


def get_resolution_time_distribution():
    """Return resolution time distribution in day-range buckets."""
    tickets = Ticket.query.filter(Ticket.solved_date.isnot(None)).all()
    buckets = {'Same day': 0, '1-3 days': 0, '4-7 days': 0,
               '1-2 weeks': 0, '2-4 weeks': 0, '1-3 months': 0, '3+ months': 0}

    for t in tickets:
        days = (t.solved_date - t.created_date).days
        if days == 0:
            buckets['Same day'] += 1
        elif days <= 3:
            buckets['1-3 days'] += 1
        elif days <= 7:
            buckets['4-7 days'] += 1
        elif days <= 14:
            buckets['1-2 weeks'] += 1
        elif days <= 30:
            buckets['2-4 weeks'] += 1
        elif days <= 90:
            buckets['1-3 months'] += 1
        else:
            buckets['3+ months'] += 1

    return [{'range': k, 'count': v} for k, v in buckets.items()]


def get_top_tickets(limit=20, min_score=0):
    """Return highest-scored tickets."""
    return Ticket.query.filter(
        Ticket.final_score >= min_score
    ).order_by(Ticket.final_score.desc()).limit(limit).all()
