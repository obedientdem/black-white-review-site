import pathlib
import re
import unittest
from xml.etree import ElementTree

ROOT = pathlib.Path(__file__).resolve().parents[1]

class FrontendTests(unittest.TestCase):
    def test_offline_entry_has_local_dependencies(self):
        html=(ROOT/'site/index.html').read_text(encoding='utf-8')
        for path in re.findall(r'(?:src|href)="([^"#]+)"',html):
            self.assertFalse(path.startswith('http'),path)
            self.assertTrue((ROOT/'site'/path).is_file(),path)
        self.assertIn('name="viewport"',html)
        self.assertIn('href="#main"',html)

    def test_original_art_is_valid(self):
        for path in (ROOT/'site/assets').glob('*.svg'):
            node=ElementTree.parse(path).getroot()
            self.assertEqual(node.attrib['viewBox'],'0 0 1200 760')
            self.assertNotIn('http', ''.join(node.itertext()))

    def test_navigation_and_accessibility(self):
        js=(ROOT/'site/app.js').read_text(encoding='utf-8')
        for route in ['reviews','news','activities','business','about','legal','account']:
            self.assertIn(route,js)
        css=(ROOT/'site/style.css').read_text(encoding='utf-8')
        self.assertIn('max-width:760px',css)
        self.assertIn('prefers-reduced-motion',css)
        self.assertIn(':focus-visible',css)
        self.assertNotIn('innerHTML=data',js)
