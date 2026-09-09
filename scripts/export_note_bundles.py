#!/usr/bin/env python3
"""Export one text bundle per un-enriched ticket for story mining."""
import os, sqlite3, sys, argparse
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser()
ap.add_argument('--out', default=os.path.join(ROOT, 'story-backfill', 'bundles'))
ap.add_argument('--db', default=os.path.join(ROOT, 'data', 'tickets.db'))
ap.add_argument('--limit', type=int, default=0)
ap.add_argument('--offset', type=int, default=0)
ap.add_argument('--max-chars', type=int, default=90000)
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
c = sqlite3.connect(a.db); c.row_factory = sqlite3.Row
cur = c.cursor()
q = """SELECT * FROM tickets WHERE (interview_notes IS NULL OR interview_notes='')
       ORDER BY final_score DESC, created_date DESC"""
if a.limit: q += f" LIMIT {a.limit} OFFSET {a.offset}"
rows = cur.fetchall() if not cur.execute(q) else cur.fetchall()
n = 0
for r in rows:
    meta = [f"{k}: {r[k]}" for k in ('zendesk_id','created_date','solved_date','status','organization',
            'customer_name','subject','category','product_area','product_line','additional_products',
            'severity','priority','is_production','is_escalation','is_production_outage',
            'jira_ticket_ids','final_score','engagement_type','first_response_hours') if r[k] not in (None,'')]
    cur2 = c.cursor()
    cur2.execute("""SELECT author, note_type, is_internal, created_at, content FROM ticket_notes
                    WHERE ticket_id=? ORDER BY created_at""", (r['id'],))
    parts = [f"===== TICKET METADATA =====\n" + "\n".join(meta),
             f"\n\n===== HEURISTIC (PRE-EXISTING, UNRELIABLE) =====\n"
             f"description: {(r['description'] or '')[:1500]}\nroot_cause: {r['root_cause']}\n"
             f"steps_taken: {r['steps_taken']}\nresolution: {r['resolution']}",
             "\n\n===== COMMENT THREAD ====="]
    total = sum(len(p) for p in parts)
    for nt in cur2.fetchall():
        blk = (f"\n\n--- {nt['created_at']} | {nt['author']} | {nt['note_type']} | "
               f"internal={nt['is_internal']} ---\n{nt['content']}")
        if total + len(blk) > a.max_chars:
            parts.append(f"\n\n[TRUNCATED: bundle exceeded {a.max_chars} chars]")
            break
        parts.append(blk); total += len(blk)
    with open(os.path.join(a.out, f"{r['zendesk_id']}.txt"), 'w', encoding='utf-8') as fh:
        fh.write("".join(parts))
    n += 1
print(f"exported {n} bundles to {a.out}")
