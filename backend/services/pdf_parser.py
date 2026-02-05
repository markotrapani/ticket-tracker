"""ZenDesk PDF parser for ticket enrichment.

Parses PDF exports from ZenDesk's print view to extract ticket fields.
"""

import re
from pathlib import Path


def parse_zendesk_pdf(pdf_path):
    """Extract structured data from a ZenDesk ticket PDF.

    Returns dict with: ticket_id, subject, customer_name, description,
    comments, full_text. Fields are None if not extractable.
    """
    import pdfplumber

    full_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

    result = {
        'ticket_id': _extract_ticket_id(pdf_path, full_text),
        'subject': _extract_subject(full_text),
        'customer_name': _extract_customer(full_text),
        'description': _clean_description(full_text),
        'comments': _extract_comments(full_text),
        'full_text': full_text,
    }

    return result


def _extract_ticket_id(pdf_path, text):
    """Extract ticket ID from filename or content."""
    filename = Path(pdf_path).name
    match = re.search(r'(\d{5,7})', filename)
    if match:
        return match.group(1)
    match = re.search(r'#(\d{5,7})', text[:500])
    if match:
        return match.group(1)
    return None


def _extract_subject(text):
    """Extract ticket subject from first line."""
    match = re.search(r'#\d{5,7}\s+(.+?)(?:\n|$)', text[:500])
    if match:
        return match.group(1).strip()
    # Fallback: first non-empty line
    for line in text.split('\n')[:5]:
        line = line.strip()
        if line and len(line) > 10:
            return line
    return None


def _extract_customer(text):
    """Extract customer name from subject line if present."""
    match = re.search(r'#\d{5,7}\s+(\w[\w\s]*?)\s*-\s+', text[:500])
    if match:
        return match.group(1).strip()
    return None


def _clean_description(text):
    """Clean up extracted text, removing ZenDesk UI noise."""
    skip_patterns = [
        r'^Focus Score',
        r'^Ticket Location',
        r'^Ticket Clusters',
        r'^Redis Support Bot',
        r'^Analyzer Bot',
        r'^File uploaded to SFTP',
        r'^Package.*analyzed',
    ]

    lines = text.split('\n')
    filtered = []
    for line in lines:
        if any(re.match(pat, line, re.IGNORECASE) for pat in skip_patterns):
            continue
        filtered.append(line)

    return '\n'.join(filtered).strip()


def _extract_comments(text):
    """Best-effort extraction of individual comments with author and timestamp."""
    comment_pattern = re.compile(
        r'([A-Z][a-z]+ [A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\n'
        r'(\w+ \d{1,2}, \d{4} at \d{1,2}:\d{2} [AP]M)\n\n'
        r'(.*?)(?=\n[A-Z][a-z]+ [A-Z][a-z]+\n\w+ \d{1,2}, \d{4}|\Z)',
        re.DOTALL
    )
    comments = []
    for match in comment_pattern.finditer(text):
        comments.append({
            'author': match.group(1),
            'timestamp': match.group(2),
            'body': match.group(3).strip()
        })
    return comments
