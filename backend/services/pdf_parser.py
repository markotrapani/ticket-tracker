"""ZenDesk PDF parser for ticket enrichment.

Parses PDF exports from ZenDesk's print view to extract ticket fields.

ZenDesk print PDF structure:
- Page header on every page: "M/D/YY, H:MM PM #NNNNNN - Subject"
- Page footer on every page: "about:blank N/NN"
- Page 1: Metadata table (Requester, Status, Priority, Problem Summary, etc.)
- Pages 2+: Comments with author/timestamp headers
- Internal notes marked with "Internal note" after author line
- Bot messages from "Redis Support Bot Agent"
"""

import re
from pathlib import Path


def parse_zendesk_pdf(pdf_path):
    """Extract structured data from a ZenDesk ticket PDF.

    Returns dict with: ticket_id, subject, customer_name, description,
    problem_summary, resolution_summary, priority, severity, product_line,
    assignee, is_production, comments, full_text.
    """
    import pdfplumber

    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages_text.append(text)

    full_text = "\n".join(pages_text)
    page1 = pages_text[0] if pages_text else ""
    conversation_text = "\n".join(pages_text[1:]) if len(pages_text) > 1 else ""

    ticket_id = _extract_ticket_id(pdf_path, full_text)
    metadata = _extract_metadata(page1)
    comments = _extract_comments(full_text)

    # Build description from first customer comment + problem summary
    first_customer_msg = _get_first_customer_message(comments)
    description = first_customer_msg or metadata.get('problem_summary') or ''

    result = {
        'ticket_id': ticket_id,
        'subject': metadata.get('subject'),
        'customer_name': metadata.get('requester_name'),
        'description': description,
        'root_cause': metadata.get('problem_summary'),
        'resolution': metadata.get('resolution_summary'),
        'priority': metadata.get('priority'),
        'severity': metadata.get('severity'),
        'product_line': metadata.get('product_line'),
        'assignee': metadata.get('assignee'),
        'is_production': metadata.get('is_production', False),
        'comments': comments,
        'full_text': full_text,
    }

    return result


def _extract_ticket_id(pdf_path, text):
    """Extract ticket ID from filename or content."""
    filename = Path(pdf_path).name
    match = re.search(r'#?(\d{5,7})', filename)
    if match:
        return match.group(1)
    match = re.search(r'#(\d{5,7})', text[:500])
    if match:
        return match.group(1)
    return None


def _extract_metadata(page1_text):
    """Extract structured metadata from page 1 of the ZenDesk PDF."""
    meta = {}
    lines = page1_text.split('\n')

    # Subject: second line typically has "#NNNNNN Subject text"
    for line in lines[:5]:
        match = re.match(r'#\d{5,7}\s+(.+)', line)
        if match:
            meta['subject'] = match.group(1).strip()
            break

    # Requester: look for "Name <email>" pattern on the Requester line
    for line in lines:
        match = re.search(r'(?:Requester|Received via)\s+.*?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s+<([^>]+)>', line)
        if match:
            meta['requester_name'] = match.group(1).strip()
            meta['requester_email'] = match.group(2).strip()
            break
    # Fallback: look for standalone "Name <email>" near top
    if 'requester_name' not in meta:
        for line in lines[:10]:
            match = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s+<([^>]+@[^>]+)>', line)
            if match:
                name = match.group(1).strip()
                # Skip known non-customer names
                if name not in ('Marko Trapani',):
                    meta['requester_name'] = name
                    meta['requester_email'] = match.group(2).strip()
                    break

    # Problem Summary and Resolution Summary extraction
    # ZenDesk PDF metadata table is multi-column, so pdfplumber flattens columns:
    #   Line N:   "Customer Impact Issue Timezone Problem Summary"  (headers)
    #   Line N+1: "Other India /Kolkata (UTC +05:30) Migration fails due to..."  (values)
    #   Line N+2: "Resolution Summary Additional Products"  (headers)
    #   Line N+3: "The solution was... additional_products_riot"  (values)
    text = '\n'.join(lines)

    # Problem Summary: find value line after the header row containing "Problem Summary"
    for i, line in enumerate(lines):
        if 'Problem Summary' in line and 'SF Sync' not in line and i + 1 < len(lines):
            val = lines[i + 1].strip()
            # Value line has Customer Impact + Timezone + Problem Summary concatenated
            # Strip timezone prefix: "Other India /Kolkata (UTC +05:30) Actual text"
            cleaned = re.sub(r'^.*?(?:UTC\s*[+\-]\d{2}:\d{2}\)\s*)', '', val)
            if cleaned:
                meta['problem_summary'] = cleaned
            break

    # Resolution Summary: find value line after "Resolution Summary" header
    for i, line in enumerate(lines):
        if line.strip().startswith('Resolution Summary') and 'SF Sync' not in line and i + 1 < len(lines):
            val = lines[i + 1].strip()
            # Value line has Resolution Summary + Additional Products concatenated
            # Strip trailing "additional_products_*" noise
            cleaned = re.sub(r'\s+additional_products\S*$', '', val, flags=re.IGNORECASE)
            if cleaned:
                meta['resolution_summary'] = cleaned
            break

    # Priority, Severity from the metadata table rows
    for line in lines:
        # Line format: "Solved Solved - Normal Support - L3 Marko Trapani"
        priority_match = re.search(r'(?:Normal|High|Urgent|Low)\s+(Support\s*-\s*L\d)\s+(.+)', line)
        if priority_match:
            meta['assignee'] = priority_match.group(2).strip()
    # Look for priority value
    for line in lines:
        if re.search(r'\b(Normal|High|Urgent|Low)\b', line):
            prio = re.search(r'\b(Normal|High|Urgent|Low)\b', line)
            if prio and 'priority' not in meta:
                meta['priority'] = prio.group(1)
                break

    # Severity
    for line in lines:
        if 'Severity' in line:
            idx = lines.index(line)
            if idx + 1 < len(lines):
                sev_line = lines[idx + 1] if 'Severity' not in lines[idx + 1] else line
                sev_match = re.search(r'\b(Normal|High|Urgent|Low|Critical)\b', sev_line)
                if sev_match:
                    meta['severity'] = sev_match.group(1)

    # Product Line
    for line in lines:
        if 'Redis Cloud' in line:
            pl_match = re.search(r'(Redis Cloud[^\s]*(?:::[\w\s()]+)?)', line)
            if pl_match:
                meta['product_line'] = pl_match.group(1).strip()
                break

    # Production environment
    meta['is_production'] = any('production environment' in line.lower() and 'yes' in line.lower()
                                for line in lines)
    # Also check for the "This is a production environment" / "Yes" pattern
    if not meta['is_production']:
        for i, line in enumerate(lines):
            if 'This is a production environment' in line:
                if 'Yes' in line or (i + 1 < len(lines) and 'Yes' in lines[i + 1]):
                    meta['is_production'] = True

    return meta


def _extract_comments(text):
    """Extract individual comments with author, timestamp, and body.

    Handles both public comments and internal notes.
    Filters out page headers, footers, and bot auto-messages.
    """
    lines = text.split('\n')
    comments = []
    current = None

    # Pattern for comment headers: "Author Name  Month DD, YYYY at HH:MM AM/PM"
    comment_header = re.compile(
        r'^(.+?)\s+((?:January|February|March|April|May|June|July|August|'
        r'September|October|November|December)\s+\d{1,2},\s+\d{4}\s+at\s+\d{1,2}:\d{2}\s+[AP]M)$'
    )

    # Patterns to skip
    page_header = re.compile(r'^\d+/\d+/\d+,\s+\d+:\d+\s+[AP]M\s+#\d+')
    page_footer = re.compile(r'^about:blank\s+\d+/\d+$')

    for line in lines:
        # Skip page headers and footers
        if page_header.match(line) or page_footer.match(line):
            continue

        header_match = comment_header.match(line)
        if header_match:
            # Save previous comment
            if current:
                current['body'] = _clean_comment_body(current['body'])
                if current['body']:  # Only keep non-empty comments
                    comments.append(current)

            author = header_match.group(1).strip()
            timestamp = header_match.group(2).strip()
            current = {
                'author': author,
                'timestamp': timestamp,
                'body': '',
                'is_internal': False,
            }
            continue

        if current is not None:
            # Check for internal note marker (appears right after header)
            if line.strip() == 'Internal note' and not current['body'].strip():
                current['is_internal'] = True
                continue
            current['body'] += line + '\n'

    # Don't forget last comment
    if current:
        current['body'] = _clean_comment_body(current['body'])
        if current['body']:
            comments.append(current)

    return comments


def _clean_comment_body(body):
    """Clean up a comment body, removing signatures and noise."""
    lines = body.strip().split('\n')
    filtered = []
    skip_signatures = False

    for line in lines:
        stripped = line.strip()

        # Skip common ZenDesk / email signature patterns
        if stripped in ('Blog | X | LinkedIn', 'The Redis Team', 'Best Regards,',
                        'Best,', 'Regards,', 'Thank you,'):
            skip_signatures = True
            continue
        if skip_signatures and (not stripped or stripped.startswith('subscribe to')):
            continue
        if skip_signatures and stripped:
            skip_signatures = False

        # Skip page references
        if re.match(r'^about:blank\s+\d+/\d+$', stripped):
            continue
        # Skip repeated page headers
        if re.match(r'^\d+/\d+/\d+,\s+\d+:\d+\s+[AP]M', stripped):
            continue

        filtered.append(line)

    result = '\n'.join(filtered).strip()

    # Remove trailing signature blocks (name + title + company)
    result = re.sub(r'\n(?:Technical Support Engineer|Support Engineer),?\s*(?:Redis|The Redis Team)\s*$',
                    '', result, flags=re.IGNORECASE)

    return result.strip()


def _get_first_customer_message(comments):
    """Get the first non-internal, non-bot comment as the ticket description."""
    bot_authors = {'Redis Support Bot Agent', 'Analyzer Bot'}

    for c in comments:
        if c['is_internal']:
            continue
        if c['author'] in bot_authors:
            continue
        # Skip automated responses
        if 'This is an automated response' in c['body']:
            continue
        if c['body']:
            return c['body']
    return None


def build_summary(parsed):
    """Build a concise situation summary for STAR format interviews.

    Combines ticket metadata context with the initial customer problem
    to create a 2-4 sentence overview suitable for the "Situation" part
    of a STAR response.
    """
    parts = []

    # Context: who, what product, when
    customer = parsed.get('customer_name')
    subject = parsed.get('subject')
    product = parsed.get('product_line')
    severity = parsed.get('severity')
    priority = parsed.get('priority')
    is_production = parsed.get('is_production', False)

    context = ''
    if customer:
        context += f"Customer {customer}"
    else:
        context += "A customer"
    if product:
        context += f" using {product}"
    if is_production:
        context += " (production environment)"
    if subject:
        context += f" reported: {subject}."
    else:
        context += " reported an issue."
    parts.append(context)

    if severity or priority:
        flag_parts = []
        if priority:
            flag_parts.append(f"Priority: {priority}")
        if severity:
            flag_parts.append(f"Severity: {severity}")
        parts.append(' | '.join(flag_parts))

    # Problem summary from metadata (if available)
    problem_summary = parsed.get('root_cause', '')
    if problem_summary:
        parts.append(f"Problem: {problem_summary}")

    # First customer message excerpt (the initial report)
    comments = parsed.get('comments', [])
    bot_authors = {'Redis Support Bot Agent', 'Analyzer Bot'}
    first_msg = None
    for c in comments:
        if c.get('is_internal'):
            continue
        if c.get('author', '') in bot_authors:
            continue
        if 'This is an automated response' in c.get('body', ''):
            continue
        if c.get('body', '').strip():
            first_msg = c['body'].strip()
            break

    if first_msg:
        # Take a reasonable excerpt of the initial report
        excerpt = first_msg[:600]
        if len(first_msg) > 600:
            # Try to break at a sentence boundary
            last_period = excerpt.rfind('.')
            if last_period > 300:
                excerpt = excerpt[:last_period + 1]
            else:
                excerpt += '...'
        parts.append(f"\nInitial report:\n{excerpt}")

    return '\n'.join(parts).strip()


def synthesize_investigation(parsed):
    """Build rich root_cause, steps_taken, and resolution from conversation thread.

    Returns 3 narratives for interview preparation:
      - root_cause:  The technical root cause (what was wrong)
      - steps_taken:  What the support engineer did (investigation, debugging,
                      coordination with R&D, custom scripts, analysis)
      - resolution:  The fix/workaround and outcome (what was delivered)

    Data sources:
      - Internal notes → steps_taken (engineer's investigation diary)
      - Engineer public messages → resolution (technical explanations & solutions)
      - Metadata summaries → root_cause / resolution headers
    """
    comments = parsed.get('comments', [])
    customer_name = (parsed.get('customer_name') or '').lower()
    metadata_root_cause = parsed.get('root_cause', '')
    metadata_resolution = parsed.get('resolution', '')

    bot_authors = {'redis support bot agent', 'analyzer bot'}

    # Classify comments
    internal_notes = []
    engineer_messages = []

    for c in comments:
        author = c.get('author', '')
        body = c.get('body', '').strip()
        if not body or len(body) < 30:
            continue
        if author.lower() in bot_authors:
            continue
        if 'This is an automated response' in body:
            continue

        if c.get('is_internal'):
            internal_notes.append(c)
        elif not customer_name or customer_name not in author.lower():
            # Not the customer, not a bot, not internal → engineer public message
            engineer_messages.append(c)

    # ── Root Cause ────────────────────────────────────────────
    # The technical finding: what was actually wrong.
    # Uses metadata summary + key technical findings from internal notes.
    root_cause_parts = []
    if metadata_root_cause:
        root_cause_parts.append(metadata_root_cause)

    # Look for internal notes that explain the root cause (contain diagnostic findings)
    rca_keywords = [
        'root cause', 'the issue is', 'the problem is', 'caused by',
        'found that', 'turns out', 'because', 'due to', 'the reason',
        'confirmed that', 'discovered', 'identified',
    ]
    for note in internal_notes:
        body_lower = note['body'].lower()
        if any(kw in body_lower for kw in rca_keywords):
            body = note['body'].strip()
            if len(body) > 1500:
                body = body[:1500] + '...'
            root_cause_parts.append(
                f"\n[{note['author']} - {note['timestamp']}]\n{body}"
            )

    # ── Steps Taken ───────────────────────────────────────────
    # What the support engineer specifically did: investigation steps,
    # debugging, analysis, coordination with R&D, custom tooling.
    # ALL internal notes are the engineer's investigation diary.
    steps_parts = []
    if internal_notes:
        for note in internal_notes:
            body = note['body'].strip()
            if len(body) > 1500:
                body = body[:1500] + '...'
            steps_parts.append(
                f"[{note['author']} - {note['timestamp']}]\n{body}"
            )

    # ── Resolution & Actions ──────────────────────────────────
    # The fix/workaround: what was communicated and delivered to the customer.
    # Uses metadata summary + substantive engineer public messages.
    resolution_parts = []
    if metadata_resolution:
        resolution_parts.append(metadata_resolution)

    if engineer_messages:
        # Include the most substantive engineer messages (longest = most detail)
        substantive = sorted(
            engineer_messages, key=lambda c: len(c['body']), reverse=True
        )
        for msg in substantive[:6]:
            body = msg['body'].strip()
            if len(body) > 1500:
                body = body[:1500] + '...'
            resolution_parts.append(
                f"\n[{msg['author']} - {msg['timestamp']}]\n{body}"
            )

    root_cause = '\n'.join(root_cause_parts).strip()
    steps_taken = '\n\n---\n\n'.join(steps_parts).strip()
    resolution = '\n'.join(resolution_parts).strip()

    # Cap total length to keep DB manageable
    for text, name in [(root_cause, 'root_cause'), (steps_taken, 'steps_taken'),
                       (resolution, 'resolution')]:
        if len(text) > 10000:
            text = text[:10000] + '\n...(truncated)'

    return (
        root_cause or metadata_root_cause or '',
        steps_taken or '',
        resolution or metadata_resolution or '',
    )


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
        r'^\d+/\d+/\d+,\s+\d+:\d+\s+[AP]M',
        r'^about:blank\s+\d+/\d+',
    ]

    lines = text.split('\n')
    filtered = []
    for line in lines:
        if any(re.match(pat, line, re.IGNORECASE) for pat in skip_patterns):
            continue
        filtered.append(line)

    return '\n'.join(filtered).strip()
