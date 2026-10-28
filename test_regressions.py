import ast
import json
import logging
import tempfile
import types
import unittest
import datetime
from email.utils import parsedate_to_datetime
from unittest.mock import mock_open
from rss_source import parse_feed, matches
from pathlib import Path

SOURCE = Path(__file__).with_name('daily_arxiv.py').read_text()

def function(name, env):
    node = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'daily_arxiv.py', 'exec'), env)
    return env[name]

class Regressions(unittest.TestCase):
    def collector(self, statuses, retry_after=0):
        class HTTPError(Exception):
            def __init__(self, status): self.status = status
        calls, sleeps = [], []
        def results(search):
            status = statuses[len(calls)]
            calls.append(status)
            if status: raise HTTPError(status)
            return iter(())
        client = types.SimpleNamespace(results=results, retry_after_seconds=retry_after)
        env = {'arxiv': types.SimpleNamespace(Search=lambda **kw: kw, HTTPError=HTTPError,
            SortCriterion=types.SimpleNamespace(SubmittedDate=0)), 'logging': logging,
            'time': types.SimpleNamespace(sleep=sleeps.append)}
        return function('get_daily_papers', env), client, calls, sleeps, HTTPError

    def test_rate_limit_then_recovery(self):
        collect, client, calls, sleeps, _ = self.collector([429, 429, 0])
        self.assertEqual(collect('topic', client=client), ({'topic': {}}, {'topic': {}}))
        self.assertEqual(sleeps, [60, 120])
        self.assertEqual(calls, [429, 429, 0])

    def test_exhausted_rate_limit_is_failure(self):
        collect, client, calls, sleeps, error = self.collector([429] * 4)
        with self.assertRaises(error): collect('topic', client=client)
        self.assertEqual(len(calls), 4)
        self.assertEqual(sleeps, [60, 120, 240])

    def test_retry_after_is_respected(self):
        collect, client, _, sleeps, _ = self.collector([503, 0], retry_after=200)
        collect('topic', client=client)
        self.assertEqual(sleeps, [200])

    def test_long_cooldown_stops_without_early_retry(self):
        collect, client, calls, sleeps, error = self.collector([429], retry_after=3600)
        with self.assertRaises(error): collect('topic', client=client)
        self.assertEqual(calls, [429])
        self.assertEqual(sleeps, [])

    def test_invalid_query_is_not_retried(self):
        collect, client, calls, sleeps, error = self.collector([400])
        with self.assertRaises(error): collect('topic', client=client)
        self.assertEqual(calls, [400])
        self.assertEqual(sleeps, [])

    def test_client_page_size_and_retry_after_header(self):
        def client(**kwargs):
            return types.SimpleNamespace(**kwargs, _session=types.SimpleNamespace(hooks={'response': []}))
        env = {'arxiv': types.SimpleNamespace(Client=client), 'datetime': datetime,
               'parsedate_to_datetime': parsedate_to_datetime}
        result = function('create_arxiv_client', env)(20)
        self.assertEqual(result.page_size, 20)
        self.assertEqual(result.num_retries, 0)
        self.assertGreaterEqual(result.delay_seconds, 3)
        hook = result._session.hooks['response'][0]
        hook(types.SimpleNamespace(headers={'Retry-After': '120'}))
        self.assertEqual(result.retry_after_seconds, 120)
        hook(types.SimpleNamespace(headers={}))
        self.assertEqual(result.retry_after_seconds, 0)

    def test_topics_share_one_client(self):
        clients, used = [], []
        def create(_):
            obj = object(); clients.append(obj); return obj
        def collect(topic, **kwargs):
            used.append(kwargs['client']); return {}, {}
        env = {'logging': logging, 'create_arxiv_client': create, 'get_daily_papers': collect,
               'datetime': datetime, 'json': json, 'open': mock_open(),
               'arxiv': types.SimpleNamespace(HTTPError=RuntimeError)}
        function('demo', env)(kv={'one': 'a', 'two': 'b'}, max_results=20,
            publish_readme=False, publish_gitpage=False, publish_wechat=False,
            show_badge=False, update_paper_links=False)
        self.assertEqual(len(clients), 1)
        self.assertEqual(used, [clients[0], clients[0]])

    def test_rate_limit_hands_off_without_extra_api_calls(self):
        collect, client, calls, sleeps, error = self.collector([429])
        with self.assertRaises(error): collect('topic', client=client, fallback_on_rate_limit=True)
        self.assertEqual(calls, [429])
        self.assertEqual(sleeps, [])

    def test_rss_does_not_overwrite_existing_api_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'index.json'
            p.write_text(json.dumps({'topic': {'old': 'original'}}))
            update = function('update_json_file', {'json': json})
            update(str(p), [{'topic': {'old': 'rss date', 'new': 'rss row'}}], replace_existing=False)
            self.assertEqual(json.loads(p.read_text()), {'topic': {'old': 'original', 'new': 'rss row'}})

    def test_rss_keyword_boundaries(self):
        self.assertTrue(matches('A Vision-Language-Action method', ['Vision Language Action']))
        self.assertFalse(matches('a nonhumanoid example', ['Humanoid']))

    def test_rss_partial_coverage_dates_and_duplicates(self):
        item = '''<item><title>Human-Object Interaction | a test</title>
          <link>https://arxiv.org/abs/2610.12345</link><description>abstract</description>
          <pubDate>Wed, 28 Oct 2026 00:00:00 -0400</pubDate>
          <dc:creator>First Author, Last Author</dc:creator><arxiv:announce_type>replace</arxiv:announce_type></item>'''
        xml = ('''<rss xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom"><channel>
          <pubDate>Wed, 28 Oct 2026 00:00:00 -0400</pubDate>''' + item + item + '</channel></rss>').encode()
        now = datetime.datetime(2026, 10, 28, 5, tzinfo=datetime.timezone.utc)
        rows, _, status = parse_feed(xml, {'HOI': {'filters': ['Human Object Interaction']}}, 20, now)
        self.assertEqual(len(rows[0]['HOI']), 1)
        self.assertIn('RSS replace', rows[0]['HOI']['2610.12345'])
        self.assertIn('&#124;', rows[0]['HOI']['2610.12345'])
        self.assertEqual(status['mode'], 'partial_recent_rss')
        self.assertIn('NOT been recovered', status['notice'])
        stale = now + datetime.timedelta(days=5)
        with self.assertRaises(ValueError): parse_feed(xml, {}, 20, stale)
        with self.assertRaises(ValueError): parse_feed(b'<html>unavailable</html>', {}, 20, now)

    def test_failed_fallback_does_not_write_archive(self):
        class HTTPError(Exception): status = 429
        def collect(*a, **kw): raise HTTPError()
        def rss(*a, **kw): raise ValueError('feed unavailable')
        output = mock_open()
        env = {'logging': logging, 'create_arxiv_client': lambda _: object(),
               'get_daily_papers': collect, 'collect_recent_rss': rss,
               'arxiv': types.SimpleNamespace(HTTPError=HTTPError), 'open': output}
        with self.assertRaises(ValueError):
            function('demo', env)(kv={'test': 'q'}, keywords={}, max_results=20,
                publish_readme=True, publish_gitpage=True, publish_wechat=False,
                show_badge=False, update_paper_links=False)
        output.assert_not_called()

    def test_query_has_operator_boundaries(self):
        config = {'keywords': {'test': {'filters': ['NeRF', 'Gaussian Splatting', 'Vision-Language-Action']}}}
        env = {'logging': logging, 'yaml': types.SimpleNamespace(FullLoader=None, load=lambda *a, **k: config)}
        with tempfile.NamedTemporaryFile() as f:
            result = function('load_config', env)(f.name)
        self.assertEqual(result['kv']['test'], 'NeRF OR "Gaussian Splatting" OR Vision-Language-Action')

    def test_collector_has_no_git_side_effects(self):
        self.assertNotIn('subprocess.run', SOURCE)
        workflow=Path('.github/workflows/cv-arxiv-3days.yml').read_text()
        self.assertIn('git config user.name',workflow)
        self.assertIn('git push origin HEAD:main',workflow)
        weekly=Path('.github/workflows/update_paper_links.yml').read_text()
        self.assertNotIn('github-actions-x/commit',weekly)
        self.assertIn('git push origin HEAD:main',weekly)

    def test_date_index_matches_rss_labels(self):
        js=Path('docs/assets/date-index.js').read_text()
        self.assertIn(r'match(/^\d{4}-\d{2}-\d{2}/)',js)
        self.assertNotIn(r'match(/^\d{4}-\d{2}-\d{2}$/)',js)

if __name__ == '__main__': unittest.main()
