"""CLI for Ticket Tracker - quick lookups and management."""

import json
import sys
import os

import click

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import create_app
from backend.models.database import db
from backend.models.ticket import Ticket, TicketTag, TicketNote
from backend.services.csv_importer import import_csv as do_import_csv
from backend.services import scoring, stats


app = create_app()


@click.group()
def cli():
    """Ticket Tracker - Manage ZenDesk support tickets."""
    pass


@cli.command('import-csv')
@click.argument('csv_path', type=click.Path(exists=True))
@click.option('--update/--no-update', default=True, help='Update existing tickets or skip')
def cmd_import_csv(csv_path, update):
    """Import tickets from ZenDesk CSV export."""
    with app.app_context():
        results = do_import_csv(csv_path, update_existing=update)
        click.echo(f"Import complete:")
        click.echo(f"  New:     {results['new']}")
        click.echo(f"  Updated: {results['updated']}")
        click.echo(f"  Skipped: {results['skipped']}")
        if results['errors']:
            click.echo(f"  Errors:  {len(results['errors'])}")
            for err in results['errors'][:10]:
                click.echo(f"    - {err}")


@cli.command('show')
@click.argument('zendesk_id')
def cmd_show(zendesk_id):
    """Show full details for a ticket."""
    with app.app_context():
        ticket = Ticket.query.filter_by(zendesk_id=zendesk_id).first()
        if not ticket:
            click.echo(f"Ticket {zendesk_id} not found.")
            return

        click.echo(f"\n{'='*60}")
        click.echo(f"Ticket #{ticket.zendesk_id}  {'*' if ticket.is_starred else ''}")
        click.echo(f"{'='*60}")
        click.echo(f"Status:      {ticket.status}")
        click.echo(f"Group:       {ticket.group_name}")
        click.echo(f"Assignee:    {ticket.assignee}")
        click.echo(f"Created:     {ticket.created_date}")
        click.echo(f"Solved:      {ticket.solved_date or 'Open'}")
        if ticket.resolution_days is not None:
            click.echo(f"Resolution:  {ticket.resolution_days} days")
        click.echo(f"URL:         {ticket.zendesk_url}")
        click.echo(f"Enrichment:  {ticket.enrichment_level}")
        click.echo(f"\nScores:")
        click.echo(f"  Auto:      {ticket.auto_score}")
        click.echo(f"  Content:   {ticket.content_score or '-'}")
        click.echo(f"  Manual:    {ticket.manual_score or '-'}")
        click.echo(f"  Final:     {ticket.final_score}")

        if ticket.subject:
            click.echo(f"\nSubject:     {ticket.subject}")
        if ticket.customer_name:
            click.echo(f"Customer:    {ticket.customer_name}")
        if ticket.category:
            click.echo(f"Category:    {ticket.category}")
        if ticket.product_area:
            click.echo(f"Product:     {ticket.product_area}")
        if ticket.severity:
            click.echo(f"Severity:    {ticket.severity}")

        tags = ticket.tag_list
        if tags:
            click.echo(f"Tags:        {', '.join(tags)}")

        if ticket.description:
            click.echo(f"\nDescription:\n{ticket.description[:500]}")
        if ticket.root_cause:
            click.echo(f"\nRoot Cause:\n{ticket.root_cause[:300]}")
        if ticket.resolution:
            click.echo(f"\nResolution:\n{ticket.resolution[:300]}")
        if ticket.interview_notes:
            click.echo(f"\nInterview Notes:\n{ticket.interview_notes}")

        click.echo()


@cli.command('list')
@click.option('--status', type=click.Choice(['Open', 'Closed', 'Pending', 'Solved', 'Hold', 'all']), default='all')
@click.option('--min-score', type=float, help='Minimum significance score')
@click.option('--starred', is_flag=True, help='Show only starred tickets')
@click.option('--category', help='Filter by category')
@click.option('--limit', '-n', default=20)
@click.option('--sort', type=click.Choice(['score', 'date', 'resolution_time']), default='score')
def cmd_list(status, min_score, starred, category, limit, sort):
    """List tickets with filters."""
    with app.app_context():
        query = Ticket.query

        if status != 'all':
            query = query.filter_by(status=status)
        if min_score is not None:
            query = query.filter(Ticket.final_score >= min_score)
        if starred:
            query = query.filter_by(is_starred=True)
        if category:
            query = query.filter_by(category=category)

        if sort == 'score':
            query = query.order_by(Ticket.final_score.desc().nullslast())
        elif sort == 'date':
            query = query.order_by(Ticket.created_date.desc())
        elif sort == 'resolution_time':
            query = query.order_by(
                (Ticket.solved_date - Ticket.created_date).desc()
            )

        tickets = query.limit(limit).all()

        if not tickets:
            click.echo("No tickets found matching criteria.")
            return

        click.echo(f"\n{'ID':<8} {'Score':>5}  {'Status':<8} {'Created':<12} {'Days':>5}  {'Subject'}")
        click.echo('-' * 80)
        for t in tickets:
            days = f"{t.resolution_days}" if t.resolution_days is not None else "open"
            subject = (t.subject or '')[:35]
            star = '*' if t.is_starred else ' '
            score = f"{t.final_score:.0f}" if t.final_score else "-"
            click.echo(f"{star}{t.zendesk_id:<7} {score:>5}  {t.status:<8} {str(t.created_date):<12} {days:>5}  {subject}")

        click.echo(f"\nShowing {len(tickets)} tickets")


@cli.command('search')
@click.argument('query')
@click.option('--limit', '-n', default=20)
def cmd_search(query, limit):
    """Search tickets by keyword."""
    with app.app_context():
        like = f'%{query}%'
        results = Ticket.query.filter(
            db.or_(
                Ticket.zendesk_id.like(like),
                Ticket.subject.like(like),
                Ticket.customer_name.like(like),
                Ticket.description.like(like),
                Ticket.root_cause.like(like),
                Ticket.resolution.like(like),
                Ticket.category.like(like),
            )
        ).order_by(Ticket.final_score.desc().nullslast()).limit(limit).all()

        if not results:
            click.echo(f"No tickets matching '{query}'")
            return

        click.echo(f"\nSearch results for '{query}' ({len(results)} found):\n")
        click.echo(f"{'ID':<8} {'Score':>5}  {'Status':<8} {'Created':<12} {'Subject'}")
        click.echo('-' * 70)
        for t in results:
            subject = (t.subject or '')[:35]
            score = f"{t.final_score:.0f}" if t.final_score else "-"
            click.echo(f"{t.zendesk_id:<8} {score:>5}  {t.status:<8} {str(t.created_date):<12} {subject}")


@cli.command('stats')
def cmd_stats():
    """Show summary statistics."""
    with app.app_context():
        overview = stats.get_overview()
        click.echo(f"\n{'='*50}")
        click.echo(f"Ticket Tracker Statistics")
        click.echo(f"{'='*50}")
        click.echo(f"Total tickets:         {overview['total']}")
        click.echo(f"Enriched:              {overview['enriched']}")
        click.echo(f"Starred:               {overview['starred']}")
        click.echo(f"Average score:         {overview['avg_score']}")
        click.echo(f"Avg resolution (days): {overview['avg_resolution_days']}")

        click.echo(f"\nBy Status:")
        for status, count in sorted(overview['status_counts'].items()):
            click.echo(f"  {status:<12} {count}")

        # Score distribution
        dist = stats.get_score_distribution()
        click.echo(f"\nScore Distribution:")
        for bucket in dist:
            bar = '#' * (bucket['count'] // 5)
            click.echo(f"  {bucket['range']:<8} {bucket['count']:>4} {bar}")

        # Resolution times
        res_times = stats.get_resolution_time_distribution()
        click.echo(f"\nResolution Time:")
        for bucket in res_times:
            bar = '#' * (bucket['count'] // 5)
            click.echo(f"  {bucket['range']:<12} {bucket['count']:>4} {bar}")


@cli.command('top')
@click.option('--min-score', type=float, default=20)
@click.option('--limit', '-n', default=20)
def cmd_top(min_score, limit):
    """Show top significance tickets for interview prep."""
    with app.app_context():
        tickets = stats.get_top_tickets(limit=limit, min_score=min_score)
        if not tickets:
            click.echo("No tickets found above minimum score.")
            return

        click.echo(f"\nTop {len(tickets)} tickets (score >= {min_score}):\n")
        click.echo(f"{'ID':<8} {'Score':>5}  {'Days':>5}  {'Created':<12} {'Subject'}")
        click.echo('-' * 70)
        for t in tickets:
            days = f"{t.resolution_days}" if t.resolution_days is not None else "open"
            subject = (t.subject or 'Not enriched')[:35]
            star = '*' if t.is_starred else ' '
            click.echo(f"{star}{t.zendesk_id:<7} {t.final_score:>5.0f}  {days:>5}  {str(t.created_date):<12} {subject}")


@cli.command('update')
@click.argument('zendesk_id')
@click.option('--subject', '-s', help='Set ticket subject')
@click.option('--category', '-c', help='Set category')
@click.option('--customer', help='Set customer name')
@click.option('--star/--unstar', default=None, help='Star/unstar ticket')
@click.option('--score', type=float, help='Set manual significance score (0-100)')
@click.option('--tag', '-t', multiple=True, help='Add tag(s)')
@click.option('--note', help='Add a note')
def cmd_update(zendesk_id, subject, category, customer, star, score, tag, note):
    """Update a ticket's fields."""
    with app.app_context():
        ticket = Ticket.query.filter_by(zendesk_id=zendesk_id).first()
        if not ticket:
            click.echo(f"Ticket {zendesk_id} not found.")
            return

        if subject:
            ticket.subject = subject
        if category:
            ticket.category = category
        if customer:
            ticket.customer_name = customer
        if star is not None:
            ticket.is_starred = star
        if score is not None:
            ticket.manual_score = max(0, min(100, score))
        if tag:
            for t in tag:
                t = t.strip().lower()
                existing = TicketTag.query.filter_by(ticket_id=ticket.id, tag=t).first()
                if not existing:
                    db.session.add(TicketTag(ticket_id=ticket.id, tag=t))
        if note:
            db.session.add(TicketNote(ticket_id=ticket.id, content=note))

        scoring.score_ticket(ticket)
        db.session.commit()
        click.echo(f"Ticket {zendesk_id} updated. Final score: {ticket.final_score}")


@cli.command('export')
@click.option('--format', 'fmt', type=click.Choice(['json', 'markdown']), default='markdown')
@click.option('--starred', is_flag=True, help='Export only starred tickets')
@click.option('--min-score', type=float, default=0)
@click.option('--output', '-o', type=click.Path(), help='Output file')
def cmd_export(fmt, starred, min_score, output):
    """Export tickets for interview prep."""
    with app.app_context():
        query = Ticket.query.filter(Ticket.final_score >= min_score)
        if starred:
            query = query.filter_by(is_starred=True)
        tickets = query.order_by(Ticket.final_score.desc()).all()

        if not tickets:
            click.echo("No tickets to export.")
            return

        if fmt == 'json':
            content = json.dumps([t.to_dict() for t in tickets], indent=2, default=str)
        else:
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
            content = '\n'.join(lines)

        if output:
            with open(output, 'w') as f:
                f.write(content)
            click.echo(f"Exported {len(tickets)} tickets to {output}")
        else:
            click.echo(content)


@cli.command('next-unenriched')
@click.option('--limit', '-n', default=10)
def cmd_next_unenriched(limit):
    """Show next unenriched tickets (highest auto-score first)."""
    with app.app_context():
        total = Ticket.query.filter_by(enrichment_level='metadata_only').count()
        tickets = Ticket.query.filter_by(enrichment_level='metadata_only').order_by(
            Ticket.final_score.desc().nullslast()
        ).limit(limit).all()

        if not tickets:
            click.echo("All tickets are enriched!")
            return

        click.echo(f"\nUnenriched tickets: {total} remaining\n")
        click.echo(f"{'ID':<8} {'Score':>5}  {'Status':<8} {'Created':<12} {'Days':>5}  {'URL'}")
        click.echo('-' * 90)
        for t in tickets:
            days = f"{t.resolution_days}" if t.resolution_days is not None else "open"
            click.echo(f"{t.zendesk_id:<8} {t.auto_score or 0:>5.0f}  {t.status:<8} "
                        f"{str(t.created_date):<12} {days:>5}  {t.zendesk_url}")


@cli.command('rescore')
def cmd_rescore():
    """Recalculate all ticket scores."""
    with app.app_context():
        tickets = Ticket.query.all()
        for ticket in tickets:
            scoring.score_ticket(ticket)
        db.session.commit()
        click.echo(f"Rescored {len(tickets)} tickets.")


if __name__ == '__main__':
    cli()
