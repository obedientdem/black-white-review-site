import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class ArchitectureTests(unittest.TestCase):
    def test_required_structure(self):
        for name in ['site', 'backend', 'tests', 'scripts', 'docs']:
            self.assertTrue((ROOT / name).is_dir(), name)

    def test_decision_covers_requirements(self):
        text = (ROOT / 'docs/ARCHITECTURE.md').read_text(encoding='utf-8')
        for term in ['GitHub Pages', 'SMTP', 'CSRF', '权限矩阵', '开奖', 'P4', '待验证']:
            self.assertIn(term, text)

    def test_runtime_dependencies(self):
        from flask import Flask
        from werkzeug.security import generate_password_hash, check_password_hash
        self.assertTrue(check_password_hash(generate_password_hash('runtime-probe'), 'runtime-probe'))
        self.assertIsNotNone(Flask(__name__).test_client())
