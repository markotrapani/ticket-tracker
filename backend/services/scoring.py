"""Significance scoring engine for ticket tracker.

Scores range from 0-100:
- Metadata-based auto-score: 0-55 (available for all tickets from CSV data)
- Content-based score: 0-45 (when ticket is enriched with description/resolution)
- Manual score: 0-100 (user override, takes priority over computed scores)
"""

import json
from datetime import date


class MetadataScorer:
    """Score tickets using only CSV metadata (created_date, solved_date, status)."""

    def score(self, ticket):
        components = {}

        # 1. Resolution Duration Score (0-30 points)
        if ticket.solved_date and ticket.created_date:
            days = (ticket.solved_date - ticket.created_date).days
            if days >= 60:
                components['duration'] = 30
            elif days >= 30:
                components['duration'] = 25
            elif days >= 14:
                components['duration'] = 20
            elif days >= 7:
                components['duration'] = 15
            elif days >= 3:
                components['duration'] = 10
            elif days >= 1:
                components['duration'] = 5
            else:
                components['duration'] = 2
        else:
            # Still open/unsolved - assign mid-range
            components['duration'] = 15

        # 2. Status Complexity (0-15 points)
        if ticket.status in ('Open', 'Pending', 'Hold'):
            days_open = (date.today() - ticket.created_date).days
            if days_open >= 90:
                components['status_complexity'] = 15
            elif days_open >= 30:
                components['status_complexity'] = 10
            else:
                components['status_complexity'] = 5
        else:
            components['status_complexity'] = 0

        # 3. Recency (0-10 points) - more recent = more interview-relevant
        months_ago = (date.today() - ticket.created_date).days / 30.0
        if months_ago <= 6:
            components['recency'] = 10
        elif months_ago <= 12:
            components['recency'] = 8
        elif months_ago <= 24:
            components['recency'] = 5
        else:
            components['recency'] = 2

        total = min(sum(components.values()), 55)
        return total, components


class ContentScorer:
    """Score tickets using enriched content (description, root_cause, resolution)."""

    TECHNICAL_DEPTH_KEYWORDS = {
        'high': ['root cause', 'core dump', 'stack trace', 'memory leak', 'race condition',
                 'deadlock', 'data loss', 'replication', 'failover', 'cluster',
                 'custom script', 'wrote a script', 'automated', 'patch',
                 'debug', 'strace', 'gdb', 'redis-cli', 'rdb analysis',
                 'active-active', 'crdb', 'oss cluster', 'sentinel'],
        'medium': ['configuration', 'upgrade', 'migration', 'backup', 'restore',
                   'monitoring', 'alert', 'threshold', 'tuning', 'optimization',
                   'tls', 'ssl', 'certificate', 'acl', 'authentication', 'ldap'],
        'low': ['how to', 'documentation', 'question', 'billing', 'access',
                'password', 'login', 'permission']
    }

    OUTAGE_KEYWORDS = ['outage', 'downtime', 'production down', 'service interruption',
                       'sla breach', 'p1', 'critical', 'emergency', 'severity 1',
                       'major incident', 'service degradation']

    def score(self, ticket):
        components = {}
        full_text = ' '.join(filter(None, [
            ticket.description, ticket.root_cause, ticket.resolution,
            ticket.interview_notes, ticket.subject
        ])).lower()

        if not full_text.strip():
            return 0, components

        # 4. Technical Depth (0-20 points)
        high_matches = sum(1 for kw in self.TECHNICAL_DEPTH_KEYWORDS['high'] if kw in full_text)
        med_matches = sum(1 for kw in self.TECHNICAL_DEPTH_KEYWORDS['medium'] if kw in full_text)
        low_matches = sum(1 for kw in self.TECHNICAL_DEPTH_KEYWORDS['low'] if kw in full_text)

        if high_matches >= 3:
            components['technical_depth'] = 20
        elif high_matches >= 1:
            components['technical_depth'] = 15
        elif med_matches >= 2:
            components['technical_depth'] = 10
        elif med_matches >= 1:
            components['technical_depth'] = 7
        elif low_matches >= 1:
            components['technical_depth'] = 3
        else:
            components['technical_depth'] = 0

        # 5. Business Impact (0-15 points)
        outage_matches = sum(1 for kw in self.OUTAGE_KEYWORDS if kw in full_text)
        impact = 0
        if outage_matches >= 2:
            impact += 10
        elif outage_matches >= 1:
            impact += 6
        if ticket.is_production_outage:
            impact = max(impact, 12)
        if ticket.is_escalation:
            impact += 3
        components['business_impact'] = min(impact, 15)

        # 6. Resolution Quality (0-10 points)
        has_root_cause = bool(ticket.root_cause and len(ticket.root_cause) > 50)
        has_resolution = bool(ticket.resolution and len(ticket.resolution) > 50)
        has_custom_scripts = ticket.involved_custom_scripts or 'script' in full_text

        quality = 0
        if has_root_cause:
            quality += 4
        if has_resolution:
            quality += 3
        if has_custom_scripts:
            quality += 3
        components['resolution_quality'] = min(quality, 10)

        total = min(sum(components.values()), 45)
        return total, components


def calculate_final_score(ticket):
    """Calculate composite final score. manual > (auto+content) > auto."""
    if ticket.manual_score is not None:
        return ticket.manual_score
    score = ticket.auto_score or 0
    if ticket.content_score is not None:
        score += ticket.content_score
    return min(round(score, 1), 100)


def score_ticket(ticket):
    """Run full scoring pipeline on a single ticket."""
    meta_scorer = MetadataScorer()
    auto_score, auto_components = meta_scorer.score(ticket)
    ticket.auto_score = auto_score

    content_scorer = ContentScorer()
    content_score, content_components = content_scorer.score(ticket)
    if content_score > 0:
        ticket.content_score = content_score

    ticket.final_score = calculate_final_score(ticket)

    return {
        'auto_score': auto_score,
        'auto_components': auto_components,
        'content_score': content_score if content_score > 0 else None,
        'content_components': content_components if content_score > 0 else None,
        'final_score': ticket.final_score,
    }
