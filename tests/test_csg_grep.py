import csv
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cryosparc_io_tools.cryosparc_csg_grep import (format_value, glob_to_regex, grep_document,
                                                    load_yaml, make_matcher)

CLI = [sys.executable, "-B", str(ROOT / "cryosparc_csg_grep.py")]

SAMPLE = """\
created: 2026-11-18 15:16:08.738470+00:00
group:
  description: Particles that belong maximally to class 0
  name: particles_class_0
  title: Particles class 0
  type: particle
results:
  alignments2D:
    metafile: '>J267_passthrough_particles_class_0.cs'
    num_items: 211997
    type: particle.alignments2D
  alignments3D:
    metafile: '>J267_class_00_00531_particles.cs'
    num_items: 211997
    type: particle.alignments3D
  blob:
    metafile: '>J267_passthrough_particles_class_0.cs'
    num_items: 211997
    type: particle.blob
version: v5.0.7
"""

OTHER = SAMPLE.replace("211997", "1500").replace("particles_class_0", "particles_class_1").replace(
    "  alignments3D:\n    metafile: '>J267_class_00_00531_particles.cs'\n    num_items: 1500\n"
    "    type: particle.alignments3D\n", "")


def run(args, cwd, check=True):
    return subprocess.run(CLI + args, cwd=cwd, check=check, capture_output=True, text=True)


def read_csv(path):
    with open(path, newline="") as handle:
        return list(csv.reader(handle))


class Folder(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)
        (self.dir / "a.csg").write_text(SAMPLE)
        (self.dir / "b.csg").write_text(OTHER)

    def grep(self, *args, check=True):
        return run(list(args), self.dir, check=check)


class MatcherTests(unittest.TestCase):
    def test_glob_is_segment_aware(self):
        match = make_matcher("results.*.num_items", "glob", ".", False)
        self.assertTrue(match("results.blob.num_items"))
        self.assertFalse(match("results.a.b.num_items"))
        self.assertFalse(match("results.blob.num_items.x"))
        self.assertTrue(make_matcher("results.**.num_items", "glob", ".", False)("results.a.b.num_items"))
        self.assertTrue(make_matcher("results.**", "glob", ".", False)("results.a.b"))
        self.assertTrue(make_matcher("results.alignments?D.type", "glob", ".", False)("results.alignments3D.type"))
        self.assertTrue(make_matcher("results.alignments[23]D.type", "glob", ".", False)("results.alignments2D.type"))
        self.assertFalse(make_matcher("results.alignments[!2]D.type", "glob", ".", False)("results.alignments2D.type"))

    def test_glob_respects_custom_separator(self):
        self.assertTrue(make_matcher("results/*/num_items", "glob", "/", False)("results/blob/num_items"))
        self.assertFalse(make_matcher("results/*/num_items", "glob", "/", False)("results/a/b/num_items"))
        self.assertTrue(make_matcher("a.b", "glob", "/", False)("a.b"))

    def test_regex_searches_and_fixed_is_literal(self):
        self.assertTrue(make_matcher(r"num_items$", "regex", ".", False)("results.blob.num_items"))
        self.assertFalse(make_matcher(r"^num_items$", "regex", ".", False)("results.blob.num_items"))
        self.assertTrue(make_matcher("a*b", "fixed", ".", False)("a*b"))
        self.assertFalse(make_matcher("a*b", "fixed", ".", False)("aXb"))
        self.assertFalse(make_matcher("results.blob", "fixed", ".", False)("results.blob.num_items"))

    def test_ignore_case(self):
        self.assertTrue(make_matcher("RESULTS.BLOB", "fixed", ".", True)("results.blob"))
        self.assertFalse(make_matcher("RESULTS.BLOB", "fixed", ".", False)("results.blob"))

    def test_glob_to_regex_escapes_literals(self):
        self.assertIsNotNone(re.fullmatch(glob_to_regex("a+b(c)"), "a+b(c)"))
        self.assertIsNotNone(re.fullmatch(glob_to_regex("a[b"), "a[b"))


class DocumentTests(Folder):
    def test_timestamp_text_is_preserved(self):
        self.assertEqual(load_yaml(str(self.dir / "a.csg"))["created"], "2026-11-18 15:16:08.738470+00:00")

    def test_hits_follow_pattern_order_and_report_missing(self):
        document = load_yaml(str(self.dir / "a.csg"))
        hits, missing = grep_document(document, ["version", "group.name", "nothing.here"])
        self.assertEqual(hits, [("version", "v5.0.7"), ("group.name", "particles_class_0")])
        self.assertEqual(missing, ["nothing.here"])

    def test_containers_report_only_when_nothing_inside_matched(self):
        document = {"a": {"b": 1, "c": [10, 20]}, "d": {}}
        self.assertEqual(grep_document(document, ["a"])[0], [("a", {"b": 1, "c": [10, 20]})])
        self.assertEqual(grep_document(document, ["a", "a.b"])[0], [("a.b", 1)])
        self.assertEqual(grep_document(document, ["a.c.1"])[0], [("a.c.1", 20)])
        self.assertEqual(grep_document(document, [])[0],
                         [("a.b", 1), ("a.c.0", 10), ("a.c.1", 20), ("d", {})])

    def test_format_value(self):
        self.assertEqual([format_value(v) for v in (None, True, False, 3, 1.5, "x")],
                         ["", "true", "false", "3", "1.5", "x"])
        self.assertEqual(format_value({"a": [1, 2]}), '{"a":[1,2]}')


class CliTests(Folder):
    def test_single_exact_key(self):
        result = self.grep("a.csg", "-k", "results.blob.num_items")
        self.assertEqual(result.stdout, "results.blob.num_items\t211997\n")
        self.assertEqual(result.stderr, "")

    def test_values_only(self):
        self.assertEqual(self.grep("a.csg", "-k", "results.blob.num_items", "--values-only").stdout, "211997\n")

    def test_glob_regex_and_fixed(self):
        glob_out = self.grep("a.csg", "-k", "results.*.num_items").stdout.splitlines()
        self.assertEqual(len(glob_out), 3)
        regex_out = self.grep("a.csg", "--regex", "-k", r"alignments\dD\.num_items").stdout.splitlines()
        self.assertEqual(regex_out, ["results.alignments2D.num_items\t211997",
                                     "results.alignments3D.num_items\t211997"])
        fixed = self.grep("a.csg", "--fixed", "-k", "results.*.num_items", check=False)
        self.assertEqual((fixed.returncode, fixed.stdout), (1, ""))
        self.assertIn("no match", fixed.stderr)

    def test_multiple_keys_in_order(self):
        result = self.grep("a.csg", "-k", "version", "-k", "group.name", "-e", "results.blob.type")
        self.assertEqual(result.stdout.splitlines(),
                         ["version\tv5.0.7", "group.name\tparticles_class_0", "results.blob.type\tparticle.blob"])

    def test_no_key_prints_every_value(self):
        lines = self.grep("a.csg").stdout.splitlines()
        self.assertIn("created\t2026-11-18 15:16:08.738470+00:00", lines)
        self.assertIn("results.blob.num_items\t211997", lines)
        self.assertEqual(len(lines), 1 + 4 + 9 + 1)

    def test_filename_prefix_only_with_several_files(self):
        out = self.grep("*.csg", "-k", "group.name").stdout.splitlines()
        self.assertEqual(out, ["a.csg\tgroup.name\tparticles_class_0", "b.csg\tgroup.name\tparticles_class_1"])

    def test_custom_separator(self):
        out = self.grep("a.csg", "--sep", "/", "-k", "results/*/num_items").stdout.splitlines()
        self.assertEqual(len(out), 3)
        self.assertTrue(all(line.startswith("results/") for line in out))

    def test_wide_csv_unions_columns_and_blanks_missing(self):
        result = self.grep("*.csg", "-k", "results.alignments3D.num_items", "-k", "group.name",
                           "--output-csv", "out.csv", check=False)
        self.assertEqual(result.returncode, 1)          # b.csg lacks alignments3D
        self.assertIn("b.csg: no match for 'results.alignments3D.num_items'", result.stderr)
        self.assertEqual(read_csv(self.dir / "out.csv"),
                         [["file", "results.alignments3D.num_items", "group.name"],
                          ["a.csg", "211997", "particles_class_0"],
                          ["b.csg", "", "particles_class_1"]])

    def test_wildcard_key_columns_and_overwrite(self):
        (self.dir / "out.csv").write_text("old\n" * 10)
        self.grep("*.csg", "-k", "results.*.num_items", "--output-csv", "out.csv")
        rows = read_csv(self.dir / "out.csv")
        self.assertEqual(rows[0], ["file", "results.alignments2D.num_items",
                                   "results.alignments3D.num_items", "results.blob.num_items"])
        self.assertEqual(rows[1], ["a.csg", "211997", "211997", "211997"])
        self.assertEqual(rows[2], ["b.csg", "1500", "", "1500"])

    def test_long_csv(self):
        self.grep("*.csg", "-k", "group.name", "-k", "version", "--output-csv", "long.csv", "--long")
        self.assertEqual(read_csv(self.dir / "long.csv"),
                         [["file", "key", "value"],
                          ["a.csg", "group.name", "particles_class_0"], ["a.csg", "version", "v5.0.7"],
                          ["b.csg", "group.name", "particles_class_1"], ["b.csg", "version", "v5.0.7"]])

    def test_container_value_is_json_and_csv_quotes_it(self):
        self.grep("a.csg", "-k", "results.blob", "--output-csv", "c.csv")
        row = read_csv(self.dir / "c.csv")[1]
        self.assertEqual(json.loads(row[1])["num_items"], 211997)

    def test_list_indices(self):
        (self.dir / "list.yaml").write_text("items:\n  - name: first\n  - name: second\n")
        self.assertEqual(self.grep("list.yaml", "-k", "items.1.name", "--values-only").stdout, "second\n")
        self.assertEqual(self.grep("list.yaml", "-k", "items.*.name", "--values-only").stdout, "first\nsecond\n")

    def test_ignore_case(self):
        self.assertEqual(self.grep("a.csg", "-i", "--fixed", "-k", "GROUP.NAME", "--values-only").stdout,
                         "particles_class_0\n")

    def test_bad_regex_and_files_are_loud(self):
        bad = self.grep("a.csg", "--regex", "-k", "(", check=False)
        self.assertEqual(bad.returncode, 2)
        (self.dir / "broken.csg").write_text("a: [unclosed\n")
        result = self.grep("a.csg", "broken.csg", "missing.csg", "-k", "version", check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("a.csg\tversion\tv5.0.7", result.stdout)
        self.assertIn("broken.csg", result.stderr)
        self.assertIn("no file matches 'missing.csg'", result.stderr)

    def test_empty_yaml_is_reported(self):
        (self.dir / "empty.yaml").write_text("")
        result = self.grep("empty.yaml", check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no values found", result.stderr)


if __name__ == "__main__":
    unittest.main()
