#!/usr/bin/env python3
"""Apply AI-written story files to the tracker DB using only the stdlib.

Functional mirror of scripts/apply_story_files.py, but talks to SQLite directly
instead of going through Flask/SQLAlchemy, so it runs in environments where the
venv (Flask, Flask-SQLAlchemy) is not available. Parsing regexes, STAR-field
overwrite behaviour, tagging, starring and rescoring all match the original.

Story file format (one markdown per ticket, named <zendesk_id>.md):
  # <ID> — <title>
  ...
  ## Story strength: <n>/5 — ...
  ## Interview angles
  ...
  ## STAR fields for DB
  - summary: ...
  - root_cause: ...
  - steps_taken: ...
  - resolution: ...

Usage:
  python3 scripts/apply_story_files_stdlib.py <dir-with-story-md-files> \
      [--db data/tickets.db] [--star-min 4] [--tag story-backfill-2026] [--dry-run]
"""
import argparse
import importlib.util
import os
import re
import sqlite3
import sys
from datetime import date, datetime

STAR_KEYS = ('summary', 'root_cause', 'steps_taken', 'resolution')
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_scoring():
    """Import backend/services/scoring.py without pulling in Flask."""
    path = os.path.join(REPO_ROOT, 'backend', 'services', 'scoring.py')
    spec = importlib.util.spec_from_file_location('scoring', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _as_date(value):
    """Coerce an ISO date/datetime string to a datetime.date; pass through None."""
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    for fmt in ('%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(text[:len('%Y-%m-%dT%H:%M:%S')
                                          if 'T' in fmt or ' ' in fmt else 10],
                                     fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def parse_story(text):
    """Identical parsing contract to the original script."""
    out = {}
    m = re.search(r'^#\s*(\d{5,7})\s*[—-]\s*(.+)$', text, re.M)
    out['id'] = m.group(1) if m else None
    out['title'] = m.group(2).strip() if m else None
    m = re.search(r'Story strength:\**\s*(\d)\s*/\s*5', text)
    out['strength'] = int(m.group(1)) if m else None

    star_block = text.split('## STAR fields', 1)
    star_text = star_block[1] if len(star_block) > 1 else ''
    for key in STAR_KEYS:
        pat = re.compile(
            r'-\s*\**' + key + r'\**\s*:\s*\**(.*?)'
            r'(?=\n-\s*\**(?:' + '|'.join(STAR_KEYS) + r')\**\s*:|\Z)', re.S)
        m = pat.search(star_text)
        out[key] = re.sub(r'\s+', ' ', m.group(1)).strip() if m else None
    return out


class TicketShim:
    """Attribute bag matching the fields scoring.py reads."""
    FIELDS = ('auto_score', 'content_score', 'manual_score', 'final_score',
              'created_date', 'solved_date', 'status', 'subject', 'description',
              'root_cause', 'resolution', 'steps_taken', 'interview_notes',
              'involved_custom_scripts', 'is_escalation', 'is_production_outage')

    DATE_FIELDS = ('created_date', 'solved_date')

    def __init__(self, row):
        for f in self.FIELDS:
            setattr(self, f, row[f] if f in row.keys() else None)
        # SQLAlchemy hands scoring.py real date objects; raw sqlite3 hands us
        # strings. Coerce so date arithmetic in scoring.py works unchanged.
        for f in self.DATE_FIELDS:
            setattr(self, f, _as_date(getattr(self, f)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('story_dir')
    ap.add_argument('--db', default=os.path.join(REPO_ROOT, 'data', 'tickets.db'))
    ap.add_argument('--star-min', type=int, default=4)
    ap.add_argument('--tag', default='story-backfill-2026')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    scoring = load_scoring()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    files = sorted(f for f in os.listdir(args.story_dir)
                   if f.endswith('.md') and not f.startswith('_'))
    applied = missing = bad = 0

    for fname in files:
        with open(os.path.join(args.story_dir, fname), encoding='utf-8') as fh:
            text = fh.read()
        s = parse_story(text)
        zid = s['id'] or fname[:-3]

        cur.execute("SELECT * FROM tickets WHERE zendesk_id=?", (str(zid),))
        row = cur.fetchone()
        if not row:
            missing += 1
            print(f"  #{zid}  NOT IN DB")
            continue
        if not all(s.get(k) for k in STAR_KEYS):
            bad += 1
            print(f"  #{zid}  could not parse all STAR fields: "
                  f"{[k for k in STAR_KEYS if not s.get(k)]}")
            continue

        shim = TicketShim(row)
        for k in STAR_KEYS:
            setattr(shim, k, s[k])
        shim.interview_notes = text
        scoring.score_ticket(shim)

        starred = None
        if s['strength'] is not None:
            starred = 1 if s['strength'] >= args.star_min else 0

        if args.dry_run:
            print(f"  #{zid}  [dry] strength={s['strength']} "
                  f"score={shim.final_score:5.1f}  {s['title']}")
            applied += 1
            continue

        cur.execute("""UPDATE tickets SET summary=?, root_cause=?, steps_taken=?,
                       resolution=?, interview_notes=?, enrichment_level='full',
                       is_starred=COALESCE(?, is_starred), auto_score=?,
                       content_score=?, final_score=?, updated_at=?
                       WHERE id=?""",
                    (s['summary'], s['root_cause'], s['steps_taken'], s['resolution'],
                     text, starred, shim.auto_score, shim.content_score,
                     shim.final_score, datetime.utcnow().isoformat(), row['id']))

        for tag in (args.tag, f"strength-{s['strength']}" if s['strength'] else None):
            if not tag:
                continue
            cur.execute("SELECT 1 FROM ticket_tags WHERE ticket_id=? AND tag=?",
                        (row['id'], tag))
            if not cur.fetchone():
                cur.execute("""INSERT INTO ticket_tags (ticket_id, tag, source, created_at)
                               VALUES (?,?,?,?)""",
                            (row['id'], tag, 'story-mining',
                             datetime.utcnow().isoformat()))
        conn.commit()
        applied += 1
        print(f"  #{zid}  strength={s['strength']}  star={starred}  "
              f"score={shim.final_score:5.1f}  {s['title']}")

    conn.close()
    print(f"\napplied={applied} missing={missing} unparsed={bad}")


if __name__ == '__main__':
    main()
