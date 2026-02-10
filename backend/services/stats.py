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


# ── Category Auto-Suggestion ─────────────────────────────────

CATEGORY_KEYWORDS = {
    'CRDB/Active-Active': ['crdb', 'active-active', 'active active', 'conflict resolution',
                           'causal consistency', 'ovc', 'observer', 'participant',
                           'geo-distributed', 'geo distributed', 'syncer'],
    'Cluster': ['cluster', 'shard', 'resharding', 'slot', 'node', 'failover',
                'replica', 'replication', 'sentinel', 'oss cluster'],
    'Performance': ['latency', 'throughput', 'slow', 'performance', 'memory',
                    'cpu', 'eviction', 'big key', 'hotspot', 'pipeline'],
    'Connectivity': ['connection', 'timeout', 'network', 'dns', 'proxy',
                     'endpoint', 'port', 'firewall', 'vpc peering', 'private link'],
    'Configuration': ['config', 'configuration', 'parameter', 'module',
                      'policy', 'persistence', 'rdb', 'aof', 'snapshot'],
    'Upgrade/Migration': ['upgrade', 'migration', 'migrate', 'version',
                          'maintenance', 'rolling upgrade', 'import', 'export'],
    'TLS/SSL': ['tls', 'ssl', 'certificate', 'cert', 'mtls', 'encryption',
                'starttls', 'ca bundle'],
    'ACL/Auth': ['acl', 'authentication', 'authorization', 'ldap', 'saml',
                 'rbac', 'password', 'user permission', 'role'],
    'Data Loss': ['data loss', 'missing key', 'missing data', 'data corruption',
                  'rdb corrupt', 'restore', 'backup fail'],
    'Monitoring': ['alert', 'monitor', 'metric', 'prometheus', 'grafana',
                   'datadog', 'new relic', 'log', 'audit'],
    'Billing': ['billing', 'invoice', 'cost', 'pricing', 'subscription',
                'payment', 'contract'],
}


def suggest_category(ticket):
    """Suggest a category based on ticket content keywords.

    Uses weighted scoring: keywords in subject/problem_summary/resolution
    count 3x more than keywords in the full description, since the description
    often contains noise (stack traces, config details, etc.).
    """
    # Subject is the highest-signal field
    subject_text = (ticket.subject or '').lower()

    # High-signal fields (root cause, resolution)
    focused_text = ' '.join(filter(None, [
        ticket.root_cause, ticket.resolution, ticket.interview_notes
    ])).lower()

    # Full text including description (may contain noise like stack traces)
    full_text = ' '.join(filter(None, [
        ticket.subject, ticket.description, ticket.root_cause,
        ticket.resolution, ticket.interview_notes
    ])).lower()

    if not full_text.strip():
        return {'suggested': None, 'confidence': 0, 'matches': {}}

    scores = {}
    matches = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        found_subject = [kw for kw in keywords if kw in subject_text]
        found_focused = [kw for kw in keywords if kw in focused_text]
        found_full = [kw for kw in keywords if kw in full_text]
        if found_full:
            # Subject matches: 5 pts, focused (RCA/resolution): 3 pts, description: 1 pt
            desc_only = len(found_full) - len(set(found_focused) | set(found_subject))
            score = len(found_subject) * 5 + len(found_focused) * 3 + max(desc_only, 0)
            scores[category] = score
            matches[category] = found_full

    if not scores:
        return {'suggested': None, 'confidence': 0, 'matches': {}}

    best = max(scores, key=scores.get)
    confidence = min(scores[best] / 9.0, 1.0)  # 9+ weighted points = full confidence
    return {
        'suggested': best,
        'confidence': round(confidence, 2),
        'matches': matches,
        'all_scores': scores,
    }


# ── STAR Format Generator ────────────────────────────────────

def generate_star_format(ticket):
    """Generate STAR format prompts for interview prep."""
    days = ticket.resolution_days
    days_str = f"{days} days" if days is not None else "ongoing"

    situation = f"A {ticket.status.lower()} support ticket (#{ticket.zendesk_id}) "
    if ticket.customer_name:
        situation += f"from {ticket.customer_name} "
    situation += f"opened on {ticket.created_date}"
    if ticket.category:
        situation += f" related to {ticket.category}"
    situation += "."
    if ticket.subject:
        situation += f" The issue: {ticket.subject}."

    task = "As the assigned L3 support engineer, I needed to "
    if ticket.is_production_outage:
        task += "urgently resolve a production outage affecting the customer. "
    elif ticket.is_escalation:
        task += "handle an escalated case requiring deep technical investigation. "
    else:
        task += "diagnose the root cause and provide a resolution. "
    task += f"The ticket was open for {days_str}."

    action = ""
    if ticket.description:
        action = ticket.description[:300]
    if ticket.root_cause:
        action += f"\n\nRoot cause identified: {ticket.root_cause[:200]}"
    if ticket.involved_custom_scripts:
        action += "\nDeveloped custom scripts/tooling as part of the resolution."
    if not action:
        action = "(Enrich this ticket with description and root cause to auto-generate this section)"

    result = ""
    if ticket.resolution:
        result = ticket.resolution[:300]
    if ticket.solved_date:
        result += f"\nResolved on {ticket.solved_date} ({days_str})."
    if not result.strip():
        result = "(Enrich this ticket with resolution details to auto-generate this section)"

    return {
        'situation': situation.strip(),
        'task': task.strip(),
        'action': action.strip(),
        'result': result.strip(),
        'ticket_id': ticket.zendesk_id,
        'score': ticket.final_score,
    }


# ── Skill Coverage Gap Analysis ──────────────────────────────

SKILL_CATEGORIES = {
    'Debugging & RCA': ['root cause', 'debug', 'strace', 'core dump', 'stack trace', 'gdb'],
    'CRDB/Active-Active': ['crdb', 'active-active', 'geo-distributed', 'conflict resolution'],
    'Cluster Management': ['cluster', 'shard', 'failover', 'replica', 'sentinel'],
    'Performance Tuning': ['latency', 'throughput', 'performance', 'optimization', 'slow'],
    'Security (TLS/ACL)': ['tls', 'ssl', 'acl', 'authentication', 'certificate', 'ldap'],
    'Data Recovery': ['data loss', 'backup', 'restore', 'rdb', 'corruption'],
    'Migration/Upgrade': ['migration', 'upgrade', 'version', 'import', 'export'],
    'Scripting/Automation': ['script', 'automated', 'custom tool', 'python', 'bash'],
    'Customer Escalation': ['escalation', 'p1', 'critical', 'outage', 'emergency'],
    'Monitoring/Alerting': ['monitor', 'alert', 'prometheus', 'grafana', 'metric'],
}


def get_skill_gaps():
    """Analyze skill coverage across enriched and starred tickets."""
    enriched = Ticket.query.filter(
        Ticket.enrichment_level != 'metadata_only'
    ).all()
    starred = Ticket.query.filter_by(is_starred=True).all()

    def analyze_pool(tickets):
        coverage = {}
        for skill, keywords in SKILL_CATEGORIES.items():
            matching = []
            for t in tickets:
                text = ' '.join(filter(None, [
                    t.subject, t.description, t.root_cause,
                    t.resolution, t.interview_notes, t.category
                ])).lower()
                if any(kw in text for kw in keywords):
                    matching.append({
                        'zendesk_id': t.zendesk_id,
                        'subject': t.subject,
                        'score': t.final_score,
                    })
            coverage[skill] = {
                'count': len(matching),
                'top_tickets': sorted(matching, key=lambda x: x['score'] or 0, reverse=True)[:3],
            }
        return coverage

    enriched_coverage = analyze_pool(enriched)
    starred_coverage = analyze_pool(starred)

    gaps = []
    for skill in SKILL_CATEGORIES:
        if starred_coverage[skill]['count'] == 0:
            gaps.append({
                'skill': skill,
                'enriched_count': enriched_coverage[skill]['count'],
                'suggestion': f"Star a ticket demonstrating {skill}" if enriched_coverage[skill]['count'] > 0
                              else f"Enrich tickets related to {skill}",
            })

    return {
        'enriched_coverage': enriched_coverage,
        'starred_coverage': starred_coverage,
        'gaps': gaps,
        'total_enriched': len(enriched),
        'total_starred': len(starred),
    }


# ── Best Tickets to Mention ──────────────────────────────────

def get_best_tickets(limit=15):
    """Auto-generate a diverse list of best tickets for interviews.
    Picks top-scored tickets while ensuring category diversity.
    """
    all_tickets = Ticket.query.filter(
        Ticket.final_score >= 15
    ).order_by(Ticket.final_score.desc()).all()

    selected = []
    categories_used = set()

    # First pass: one per category (highest scored)
    for t in all_tickets:
        cat = t.category or 'Uncategorized'
        if cat not in categories_used and len(selected) < limit:
            selected.append(t)
            categories_used.add(cat)

    # Second pass: fill remaining slots with highest scores
    for t in all_tickets:
        if t not in selected and len(selected) < limit:
            selected.append(t)

    return [{
        'zendesk_id': t.zendesk_id,
        'subject': t.subject,
        'customer_name': t.customer_name,
        'category': t.category,
        'final_score': t.final_score,
        'resolution_days': t.resolution_days,
        'enrichment_level': t.enrichment_level,
        'is_starred': t.is_starred,
        'why': _explain_selection(t),
    } for t in selected]


def _explain_selection(ticket):
    """Generate a brief explanation of why this ticket was selected."""
    reasons = []
    if ticket.resolution_days and ticket.resolution_days >= 30:
        reasons.append(f"Long investigation ({ticket.resolution_days} days)")
    if ticket.is_production_outage:
        reasons.append("Production outage")
    if ticket.is_escalation:
        reasons.append("Escalation")
    if ticket.involved_custom_scripts:
        reasons.append("Custom scripting")
    if ticket.category:
        reasons.append(f"Demonstrates {ticket.category}")
    if ticket.final_score and ticket.final_score >= 40:
        reasons.append(f"High significance ({ticket.final_score:.0f})")
    if not reasons:
        reasons.append("Metadata-based selection")
    return '; '.join(reasons)


# ── Year-over-Year ───────────────────────────────────────────

def get_year_over_year():
    """Return ticket counts grouped by year and month for comparison."""
    rows = db.session.query(
        func.strftime('%Y', Ticket.created_date).label('year'),
        func.strftime('%m', Ticket.created_date).label('month'),
        func.count(Ticket.id).label('count')
    ).group_by('year', 'month').order_by('year', 'month').all()

    years = {}
    for r in rows:
        if r.year not in years:
            years[r.year] = {}
        years[r.year][r.month] = r.count

    return {
        'years': years,
        'months': ['01', '02', '03', '04', '05', '06',
                    '07', '08', '09', '10', '11', '12'],
    }


# ── Resolution Time Trends ───────────────────────────────────

def get_resolution_trends():
    """Return average resolution time by quarter."""
    rows = db.session.query(
        func.strftime('%Y', Ticket.created_date).label('year'),
        (func.cast((func.cast(func.strftime('%m', Ticket.created_date), db.Integer) - 1) / 3 + 1, db.Integer)).label('quarter'),
        func.avg(func.julianday(Ticket.solved_date) - func.julianday(Ticket.created_date)).label('avg_days'),
        func.count(Ticket.id).label('count')
    ).filter(
        Ticket.solved_date.isnot(None)
    ).group_by('year', 'quarter').order_by('year', 'quarter').all()

    return [{
        'period': f"{r.year} Q{r.quarter}",
        'avg_days': round(r.avg_days, 1) if r.avg_days else 0,
        'count': r.count,
    } for r in rows]
