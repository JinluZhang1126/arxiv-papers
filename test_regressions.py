import ast
import json
import logging
import tempfile
import types
import unittest
from pathlib import Path

SOURCE = Path(__file__).with_name('daily_arxiv.py').read_text()

def function(name, env):
    node = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'daily_arxiv.py', 'exec'), env)
    return env[name]

class Regressions(unittest.TestCase):
    def test_query_has_operator_boundaries(self):
        config = {'keywords': {'test': {'filters': ['NeRF', 'Gaussian Splatting', 'Vision-Language-Action']}}}
        env = {'logging': logging, 'yaml': types.SimpleNamespace(FullLoader=None, load=lambda *a, **k: config)}
        with tempfile.NamedTemporaryFile() as f:
            result = function('load_config', env)(f.name)
        self.assertEqual(result['kv']['test'], 'NeRF OR "Gaussian Splatting" OR Vision-Language-Action')

    def test_exhausted_http_error_is_failure(self):
        class HTTPError(Exception): pass
        class Search:
            def __init__(self, **kwargs): pass
            def results(self): raise HTTPError('unavailable')
        env={'arxiv':types.SimpleNamespace(Search=Search, HTTPError=HTTPError, SortCriterion=types.SimpleNamespace(SubmittedDate=0)), 'logging':logging,'time':types.SimpleNamespace(sleep=lambda _:None)}
        with self.assertRaises(HTTPError): function('get_daily_papers',env)('topic')

    def test_collector_has_no_git_side_effects(self):
        self.assertNotIn('subprocess.run', SOURCE)
        workflow=Path('.github/workflows/cv-arxiv-3days.yml').read_text()
        self.assertIn('git config user.name',workflow)
        self.assertIn('git push origin HEAD:main',workflow)

if __name__ == '__main__': unittest.main()
