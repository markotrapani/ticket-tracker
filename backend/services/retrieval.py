"""Smart retrieval service for ticket chatbot.

Parses natural language questions into structured queries, searches the FTS5
index and structured fields, and formats results for LLM consumption.
"""

import re
from datetime import date, timedelta

from backend.models.database import db
from backend.models.ticket import Ticket

# ── Query Parsing ──────────────────────────────────────────────

CATEGORY_MAP = {
    'crdb': 'CRDB/Active-Active',
    'active-active': 'CRDB/Active-Active',
    'active active': 'CRDB/Active-Active',
    'geo-distributed': 'CRDB/Active-Active',
    'geo distributed': 'CRDB/Active-Active',
    'cluster': 'Cluster',
    'shard': 'Cluster',
    'failover': 'Cluster',
    'replica': 'Cluster',
    'replication': 'Cluster',
    'sentinel': 'Cluster',
    'performance': 'Performance',
    'latency': 'Performance',
    'slow': 'Performance',
    'memory': 'Performance',
    'eviction': 'Performance',
    'connectivity': 'Connectivity',
    'connection': 'Connectivity',
    'timeout': 'Connectivity',
    'dns': 'Connectivity',
    'network': 'Connectivity',
    'tls': 'TLS/SSL',
    'ssl': 'TLS/SSL',
    'certificate': 'TLS/SSL',
    'mtls': 'TLS/SSL',
    'acl': 'ACL/Auth',
    'authentication': 'ACL/Auth',
    'ldap': 'ACL/Auth',
    'saml': 'ACL/Auth',
    'corruption': 'Data Loss',
    'upgrade': 'Upgrade/Migration',
    'migration': 'Upgrade/Migration',
    'monitoring': 'Monitoring',
    'alert': 'Monitoring',
    'prometheus': 'Monitoring',
    'grafana': 'Monitoring',
    'billing': 'Billing',
    'invoice': 'Billing',
    'subscription': 'Billing',
    'configuration': 'Configuration',
    'config': 'Configuration',
    'persistence': 'Configuration',
    'rdb': 'Configuration',
    'aof': 'Configuration',
}

# Multi-word entries checked first (order matters)
CATEGORY_PHRASES = [
    ('active-active', 'CRDB/Active-Active'),
    ('active active', 'CRDB/Active-Active'),
    ('geo-distributed', 'CRDB/Active-Active'),
    ('geo distributed', 'CRDB/Active-Active'),
]

PRODUCT_MAP = {
    'redis cloud': 'product_line',
    'acre': 'product_line',
    'redis software': 'product_line',
    'essentials': 'product_line',
    'pro': 'product_line',
    'redisearch': 'additional_products',
    'redisjson': 'additional_products',
    'redistimeseries': 'additional_products',
    'redisgraph': 'additional_products',
    'redisbloom': 'additional_products',
    'kubernetes': None,  # search keyword, not a field filter
    'k8s': None,
    'openshift': None,
    'docker': None,
}

BOOLEAN_SIGNALS = {
    'production outage': {'is_production_outage': True},
    'outage': {'is_production_outage': True},
    'production down': {'is_production_outage': True},
    'escalation': {'is_escalation': True},
    'escalated': {'is_escalation': True},
    'custom script': {'involved_custom_scripts': True},
    'wrote a script': {'involved_custom_scripts': True},
    'created a script': {'involved_custom_scripts': True},
}

SEVERITY_MAP = {
    'p1': 'Critical', 'p2': 'Urgent', 'p3': 'High', 'p4': 'Normal',
    'severity 1': 'Critical', 'severity 2': 'Urgent',
    'critical': 'Critical', 'urgent': 'Urgent', 'high severity': 'High',
}

SCORE_SIGNALS = ['best', 'top', 'highest', 'most significant', 'most important',
                 'most complex', 'most interesting', 'highest scored']

STOP_WORDS = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'what', 'which',
              'where', 'when', 'how', 'did', 'do', 'does', 'can', 'could',
              'would', 'should', 'will', 'that', 'this', 'these', 'those',
              'find', 'show', 'list', 'get', 'give', 'me', 'my', 'all',
              'any', 'some', 'about', 'with', 'for', 'from', 'in', 'on',
              'of', 'to', 'and', 'or', 'not', 'no', 'but', 'if', 'by',
              'at', 'we', 'our', 'us', 'you', 'i', 'had', 'have', 'has',
              'been', 'be', 'it', 'its', 'there', 'their', 'they', 'tickets',
              'ticket', 'involved', 'involving', 'related', 'issues', 'issue',
              'problems', 'problem', 'cases', 'case', 'scored', 'score',
              'resolve', 'resolved', 'most', 'many', 'much'}


def parse_query(question):
    """Parse a natural language question into structured search parameters."""
    q = question.lower().strip()
    filters = {}
    keywords = []
    sort_by_score = False
    limit = 15

    # Check score signals first
    for signal in SCORE_SIGNALS:
        if signal in q:
            sort_by_score = True
            q = q.replace(signal, ' ')

    # Check boolean signals (multi-word, check first)
    for phrase, bool_filters in BOOLEAN_SIGNALS.items():
        if phrase in q:
            filters.update(bool_filters)
            q = q.replace(phrase, ' ')

    # Check severity
    for term, sev in SEVERITY_MAP.items():
        if term in q:
            filters['severity'] = sev
            q = q.replace(term, ' ')
            break

    # Collect ALL category candidates, then pick the best one
    category_candidates = []
    for phrase, cat in CATEGORY_PHRASES:
        if phrase in q:
            category_candidates.append((phrase, cat, True))  # (term, category, is_phrase)
    for term, cat in CATEGORY_MAP.items():
        if re.search(r'\b' + re.escape(term) + r'\b', q):
            # Don't double-count if already matched as a phrase
            if not any(term in p for p, _, _ in category_candidates):
                category_candidates.append((term, cat, False))

    if category_candidates:
        # If multiple categories detected, pick the first single-word match as the
        # primary category (e.g. "CRDB") and treat multi-word matches as keywords
        # (e.g. "data loss" becomes a search term, not a category filter)
        if len(category_candidates) > 1:
            # Prefer non-phrase (single-word) categories as the primary filter
            primary = next((c for c in category_candidates if not c[2]), category_candidates[0])
            filters['category'] = primary[1]
            q = re.sub(r'\b' + re.escape(primary[0]) + r'\b', ' ', q)
            # Keep other category terms as keywords
            for term, cat, is_phrase in category_candidates:
                if term != primary[0]:
                    # Don't strip from q — they'll become search keywords
                    pass
        else:
            term, cat, _ = category_candidates[0]
            filters['category'] = cat
            q = q.replace(term, ' ') if ' ' in term else re.sub(r'\b' + re.escape(term) + r'\b', ' ', q)

    # Check product references (use word boundaries for short terms)
    for term, field in PRODUCT_MAP.items():
        if ' ' in term:
            match = term in q
        else:
            match = bool(re.search(r'\b' + re.escape(term) + r'\b', q))
        if match:
            if field:
                filters[field] = term
            # Keep as keyword for search regardless
            q = q.replace(term, ' ') if ' ' in term else re.sub(r'\b' + re.escape(term) + r'\b', ' ', q)
            keywords.append(term)
            break

    # Check year/time filters
    year_match = re.search(r'\b(202[0-9])\b', q)
    if year_match:
        year = int(year_match.group(1))
        filters['year'] = year
        q = q.replace(year_match.group(0), ' ')

    if 'last year' in q:
        filters['year'] = date.today().year - 1
        q = q.replace('last year', ' ')
    elif 'this year' in q:
        filters['year'] = date.today().year
        q = q.replace('this year', ' ')
    elif 'recent' in q or 'recently' in q:
        filters['recent_months'] = 12
        q = re.sub(r'\brecent(ly)?\b', ' ', q)

    # Check for starred/interview
    if 'starred' in q or 'interview' in q:
        filters['is_starred'] = True
        q = re.sub(r'\b(starred|interview)\b', ' ', q)

    # Remaining words become FTS keywords (after stripping stop words)
    remaining = re.findall(r'[a-z0-9]+', q)
    remaining_keywords = [w for w in remaining if w not in STOP_WORDS and len(w) > 1]
    keywords.extend(remaining_keywords)

    # Deduplicate keywords
    seen = set()
    unique_keywords = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique_keywords.append(kw)

    return {
        'keywords': unique_keywords,
        'filters': filters,
        'sort_by_score': sort_by_score,
        'limit': limit,
        'original_question': question,
    }


# ── Ticket Retrieval ──────────────────────────────────────────

def retrieve_tickets(parsed_query):
    """Search tickets using FTS5 + structured filters, return ranked results."""
    keywords = parsed_query['keywords']
    filters = parsed_query['filters']
    limit = parsed_query['limit']
    sort_by_score = parsed_query['sort_by_score']

    # Try FTS5 first if we have keywords
    tickets = []
    if keywords:
        fts_term = ' OR '.join(keywords)
        try:
            fts_query = db.text("""
                SELECT t.id FROM tickets t
                JOIN tickets_fts fts ON t.id = fts.rowid
                WHERE tickets_fts MATCH :query
                ORDER BY rank
                LIMIT 100
            """)
            rows = db.session.execute(fts_query, {'query': fts_term}).fetchall()
            if rows:
                ids = [r.id for r in rows]
                tickets = Ticket.query.filter(Ticket.id.in_(ids)).all()
        except Exception:
            pass

        # LIKE fallback if FTS didn't work
        if not tickets:
            conditions = []
            for kw in keywords:
                like = f'%{kw}%'
                conditions.append(db.or_(
                    Ticket.subject.ilike(like),
                    Ticket.summary.ilike(like),
                    Ticket.root_cause.ilike(like),
                    Ticket.steps_taken.ilike(like),
                    Ticket.resolution.ilike(like),
                    Ticket.description.ilike(like),
                    Ticket.category.ilike(like),
                ))
            tickets = Ticket.query.filter(db.and_(*conditions)).all()
    else:
        # No keywords — just use filters
        tickets = Ticket.query.all()

    # Apply structured filters in Python (simpler than building dynamic SQL)
    filtered = []
    for t in tickets:
        if 'category' in filters:
            if not t.category or filters['category'].lower() not in t.category.lower():
                continue
        if 'severity' in filters:
            if t.severity != filters['severity']:
                continue
        if 'is_production_outage' in filters:
            if not t.is_production_outage:
                continue
        if 'is_escalation' in filters:
            if not t.is_escalation:
                continue
        if 'involved_custom_scripts' in filters:
            if not t.involved_custom_scripts:
                # Also check text fields for "script" mentions
                text = ' '.join(filter(None, [t.steps_taken, t.resolution, t.summary])).lower()
                if 'script' not in text:
                    continue
        if 'is_starred' in filters:
            if not t.is_starred:
                continue
        if 'year' in filters:
            if not t.created_date or t.created_date.year != filters['year']:
                continue
        if 'recent_months' in filters:
            cutoff = date.today() - timedelta(days=filters['recent_months'] * 30)
            if not t.created_date or t.created_date < cutoff:
                continue
        if 'product_line' in filters:
            prod = (t.product_line or '').lower()
            addl = (t.additional_products or '').lower()
            if filters['product_line'] not in prod and filters['product_line'] not in addl:
                continue
        filtered.append(t)

    # Sort: by score (desc) if score signal, otherwise by relevance (keep FTS order) + score
    if sort_by_score:
        filtered.sort(key=lambda t: t.final_score or 0, reverse=True)
    else:
        filtered.sort(key=lambda t: t.final_score or 0, reverse=True)

    return filtered[:limit]


# ── LLM Formatting ──────────────────────────────────────────

def format_for_llm(tickets, question):
    """Format retrieved tickets as structured context for LLM consumption."""
    total_in_db = Ticket.query.count()
    lines = [
        f"Question: {question}",
        f"Retrieved {len(tickets)} relevant tickets from database of {total_in_db} total.",
        "",
    ]

    for t in tickets:
        lines.append(f"=== TICKET #{t.zendesk_id} (Score: {t.final_score or 0:.0f}) ===")
        lines.append(f"Subject: {t.subject or 'N/A'}")
        lines.append(f"Category: {t.category or 'N/A'} | Product: {t.product_line or 'N/A'}")
        lines.append(f"Priority: {t.priority or 'N/A'} | Severity: {t.severity or 'N/A'}")
        lines.append(f"Customer: {t.customer_name or 'N/A'}")
        lines.append(f"Status: {t.status} | Created: {t.created_date} | "
                     f"Resolution: {t.resolution_days or 'N/A'} days")
        if t.is_production_outage:
            lines.append("Production Outage: Yes")
        if t.is_escalation:
            lines.append("Escalation: Yes")
        if t.involved_custom_scripts:
            lines.append("Custom Scripts: Yes")
        if t.summary:
            lines.append(f"Summary: {t.summary}")
        if t.root_cause:
            lines.append(f"Root Cause: {t.root_cause}")
        if t.steps_taken:
            lines.append(f"Steps Taken: {t.steps_taken}")
        if t.resolution:
            lines.append(f"Resolution: {t.resolution}")
        lines.append("")

    lines.append("---")
    lines.append("Based on the above ticket data, please answer the question.")
    return '\n'.join(lines)


def format_for_chat(tickets):
    """Format tickets as compact summaries for the chat UI."""
    results = []
    for t in tickets:
        results.append({
            'zendesk_id': t.zendesk_id,
            'subject': t.subject,
            'category': t.category,
            'product_line': t.product_line,
            'severity': t.severity,
            'final_score': t.final_score or 0,
            'customer_name': t.customer_name,
            'status': t.status,
            'resolution_days': t.resolution_days,
            'is_production_outage': t.is_production_outage,
            'is_escalation': t.is_escalation,
            'summary': t.summary,
            'root_cause': t.root_cause,
        })
    return results


# ── Top-Level API ──────────────────────────────────────────

def query_tickets(question):
    """Parse question, retrieve tickets, format for LLM. Returns full result dict."""
    parsed = parse_query(question)
    tickets = retrieve_tickets(parsed)
    formatted = format_for_llm(tickets, question)
    chat_results = format_for_chat(tickets)

    return {
        'question': question,
        'parsed_query': parsed,
        'tickets': chat_results,
        'formatted_context': formatted,
        'ticket_count': len(tickets),
    }
