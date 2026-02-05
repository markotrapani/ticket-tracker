# Ticket Tracker - CLAUDE.md

A personal ticket tracking application for cataloging ZenDesk support tickets, enriching them with details, and scoring their significance for interview preparation.

## Tech Stack

- **Backend:** Python 3.10+, Flask 3.1, SQLAlchemy 2.0, SQLite
- **Frontend:** Jinja2 templates, Bootstrap 5 (dark theme), vanilla JS, Chart.js
- **CLI:** Click 8.1
- **PDF Parsing:** pdfplumber

## Project Structure

```
ticket-tracker/
├── backend/
│   ├── app.py              # Flask app factory + all routes (pages + JSON API)
│   ├── config.py           # Configuration (DB path, directories)
│   ├── cli.py              # Click CLI commands
│   ├── models/
│   │   ├── database.py     # SQLAlchemy instance
│   │   └── ticket.py       # Ticket, TicketTag, TicketNote, ScoreHistory models
│   └── services/
│       ├── csv_importer.py # ZenDesk CSV import (semicolon-delimited)
│       ├── pdf_parser.py   # ZenDesk PDF parsing for ticket enrichment
│       ├── scoring.py      # Significance scoring engine (metadata + content)
│       └── stats.py        # Statistics and dashboard queries
├── frontend/
│   ├── templates/          # Jinja2 HTML templates
│   └── static/             # CSS + JS assets
├── data/                   # SQLite DB + attachments (gitignored)
├── sample_data/            # Original CSV export
├── run.py                  # Web server entry point
└── requirements.txt
```

## Running the Application

```bash
# Setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Import tickets from CSV
python backend/cli.py import-csv sample_data/Tickets_Created_tickets_default_Drill_in_02052026_1415.csv

# Start web UI
python run.py
# Visit http://localhost:5000

# CLI commands
python backend/cli.py stats
python backend/cli.py show <ticket_id>
python backend/cli.py list --min-score 25 -n 20
python backend/cli.py search "keyword"
python backend/cli.py top --min-score 20
python backend/cli.py update <ticket_id> --star --category "CRDB"
python backend/cli.py export --format markdown --starred
python backend/cli.py rescore
```

## Significance Scoring (0-100)

Composite score from up to three sources:

**Metadata Auto-Score (0-55 max)** - calculated for all tickets from CSV data:
- Duration (0-30): days to resolve as complexity proxy
- Status Complexity (0-15): still-open long-lived tickets
- Recency (0-10): more recent = more interview-relevant

**Content Score (0-45 max)** - calculated when ticket is enriched:
- Technical Depth (0-20): keyword analysis for RCA, debugging, scripting
- Business Impact (0-15): outage keywords, escalation flags
- Resolution Quality (0-10): has root cause, resolution, custom scripts

**Manual Score (0-100)** - user override, takes priority over computed scores

## Data Flow

1. **CSV Import** -> basic metadata (ID, status, dates) -> auto-score
2. **PDF Import / Paste** -> description, comments -> content-score
3. **Manual Edit** -> category, tags, notes, interview notes -> enrichment
4. **Manual Score** -> override computed score

## Key Design Decisions

- SQLite for zero-setup persistence (single file in data/)
- Enrichment levels: metadata_only -> partial -> full (tracks completeness)
- Score cascade: manual_score > (auto + content) > auto alone
- Auto-score capped at 55 to encourage enrichment
- All fields nullable except zendesk_id and created_date
- ZenDesk URL auto-generated from ticket ID

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full project roadmap with current progress and planned features.

## Development Guidelines

- All code stays within /ticket-tracker/ directory
- No external project dependencies or references
- Follow conventional commit format
- Test changes by running `python run.py` and verifying in browser
- CLI and web UI share the same models and services layer
