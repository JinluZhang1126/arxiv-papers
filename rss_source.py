"""Official arXiv announcement-feed fallback; never claims historical search coverage."""
import datetime
from email.utils import parsedate_to_datetime
from html import escape, unescape
import re
import urllib.request
import xml.etree.ElementTree as ET

FEED_URL = 'https://rss.arxiv.org/rss/cs.CV+cs.RO+cs.AI+cs.LG'
NS = {'dc': 'http://purl.org/dc/elements/1.1/', 'arxiv': 'http://arxiv.org/schemas/atom'}


def normalize(text):
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', unescape(text).lower()).split())


def matches(text, filters):
    haystack = ' ' + normalize(text) + ' '
    return any(' ' + normalize(term) + ' ' in haystack for term in filters)


def safe_cell(value):
    return escape(' '.join(value.split()), quote=True).replace('|', '&#124;')


def parse_feed(raw, keywords, max_results, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if len(raw) > 8_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Oversized or unsafe RSS XML')
    root = ET.fromstring(raw)
    channel = root.find('channel')
    if root.tag != 'rss' or channel is None:
        raise ValueError('Expected official RSS 2 channel')
    date = parsedate_to_datetime(channel.findtext('pubDate', ''))
    age = (now - date).total_seconds()
    if not 0 <= age <= 72 * 3600:
        raise ValueError('RSS announcement date is stale or in the future')
    rows, web, counts = {}, {}, {}
    items = channel.findall('item')
    parsed = {}
    for item in items:
        url = item.findtext('link', '')
        match = re.fullmatch(r'https://arxiv\.org/abs/(\d{4}\.\d{4,5})(?:v\d+)?', url)
        if not match:
            raise ValueError('Unexpected official paper URL')
        paper_id = match.group(1)
        title = item.findtext('title', '').strip()
        authors = item.findtext('dc:creator', '', NS).strip()
        description = item.findtext('description', '')
        kind = item.findtext('arxiv:announce_type', '', NS)
        if not title or not authors or kind not in {'new', 'cross', 'replace', 'replace-cross'}:
            raise ValueError('Incomplete announcement metadata')
        announced = parsedate_to_datetime(item.findtext('pubDate', '')).date().isoformat()
        # Keep the first canonical item if the combined feed repeats a cross-list.
        parsed.setdefault(paper_id, (title, authors, description, kind, announced, url))
    for topic, spec in keywords.items():
        selected = [(pid, v) for pid, v in parsed.items() if matches(v[0] + ' ' + v[2], spec['filters'])]
        selected.sort(reverse=True)
        counts[topic] = {'matched': len(selected), 'selected': min(max_results, len(selected))}
        rows[topic], web[topic] = {}, {}
        for pid, (title, authors, _, kind, announced, url) in selected[:max_results]:
            label = f'{announced} (RSS {kind})'
            rows[topic][pid] = f'|**{label}**|**{safe_cell(title)}**|{safe_cell(authors)}|[{pid}]({url})|null|\n'
            web[topic][pid] = f'- {label}, **{safe_cell(title)}**, {safe_cell(authors)}, Paper: [{url}]({url})\n'
    status = {'mode': 'partial_recent_rss', 'api_status': 429, 'source': FEED_URL,
        'retrieved_at': now.isoformat(), 'feed_announcement_date': date.isoformat(),
        'feed_items': len(items), 'unique_items': len(parsed), 'feed_cap_reached': len(items) >= 2000,
        'topics': counts,
        'notice': ('Partial coverage: arXiv search API returned HTTP 429. Used the official recent '
                   'cs.CV/cs.RO/cs.AI/cs.LG RSS announcement feed only. RSS dates in new rows are '
                   'announcement/update dates, not submission dates. Existing records are preserved; '
                   'historical gaps and categories outside this feed have NOT been recovered.')}
    return [{k: v} for k, v in rows.items()], [{k: v} for k, v in web.items()], status


def collect_recent_rss(keywords, max_results):
    # One documented public-feed request, no proxy/identity changes or API retries here.
    with urllib.request.urlopen(FEED_URL, timeout=60) as response:
        raw = response.read(8_000_001)
    return parse_feed(raw, keywords, max_results)
