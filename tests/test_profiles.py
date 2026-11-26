import copy
import tempfile
import unittest
from pathlib import Path

from overlay.config import DEFAULT_CONFIG
from overlay.core import profiles


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "profiles"

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        p = profiles.create("Собес в Acme", {"mode": "interview", "context": "Python backend"}, self.root)
        self.assertEqual(p.slug, "собес-в-acme")
        p.notes = "Junior Python developer"
        profiles.save(p, self.root)
        again = profiles.load(p.slug, self.root)
        self.assertEqual(
            (again.name, again.notes, again.settings["mode"]), ("Собес в Acme", "Junior Python developer", "interview")
        )
        self.assertEqual([q.slug for q in profiles.list_profiles(self.root)], [p.slug])
        self.assertEqual(profiles.create("Собес в Acme", root=self.root).slug, "собес-в-acme-2")
        profiles.delete(p.slug, self.root)
        self.assertIsNone(profiles.load(p.slug, self.root))

    def test_documents(self):
        p = profiles.create("Lecture", root=self.root)
        source = Path(self.tmp.name) / "notes.md"
        source.write_text("# Graphs\n\nDijkstra finds shortest paths.", encoding="utf-8")
        doc = profiles.import_document(p, source, self.root)
        self.assertEqual((doc.file, doc.title), ("notes.txt", "notes"))
        self.assertIn("Dijkstra", profiles.document_text(p, doc, self.root))
        block = profiles.system_block(p, self.root)
        self.assertIn('<document title="notes">', block)
        self.assertIn('<profile name="Lecture">', block)
        doc.enabled = False
        self.assertEqual(profiles.system_block(p, self.root), "")
        doc.enabled = True
        profiles.remove_document(p, doc, self.root)
        self.assertEqual(profiles.load(p.slug, self.root).docs, [])
        with self.assertRaises(ValueError):
            profiles.import_document(p, Path(self.tmp.name) / "x.exe", self.root)

    def test_pdf_import(self):
        from pypdf import PdfWriter

        p = profiles.create("CV", root=self.root)
        pdf = Path(self.tmp.name) / "cv.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with open(pdf, "wb") as f:
            writer.write(f)
        doc = profiles.import_document(p, pdf, self.root)
        self.assertEqual(doc.title, "cv")

    def test_size_limit_trims_documents(self):
        p = profiles.create("Big", root=self.root)
        for i in range(3):
            source = Path(self.tmp.name) / f"d{i}.txt"
            source.write_text("x" * 100, encoding="utf-8")
            profiles.import_document(p, source, self.root)
        block = profiles.system_block(p, self.root, limit=150)
        self.assertIn("size limit", block)
        self.assertEqual(block.count("<document"), 2)
        self.assertEqual(profiles.total_chars(p, self.root), 300)

    def test_config_sync(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["mode"], cfg["glossary"] = "solve", "Django"
        p = profiles.create("Math", profiles.settings_from_config(cfg), self.root)
        fresh = copy.deepcopy(DEFAULT_CONFIG)
        profiles.apply_to_config(p, fresh)
        self.assertEqual((fresh["mode"], fresh["glossary"], fresh["profile"]), ("solve", "Django", p.slug))
        self.assertEqual(profiles.system_block(None), "")


if __name__ == "__main__":
    unittest.main()
