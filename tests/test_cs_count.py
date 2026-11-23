import csv
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cryosparc_io_tools.cryosparc_cs_count import count_particles

CLI = [sys.executable, "-B", str(ROOT / "cryosparc_cs_count.py")]


def make_cs(path, rows, with_objects=False):
    """Write a structured array the way CryoSPARC .cs files are (np.save, .cs name)."""
    fields = [("uid", "<u8"), ("blob/idx", "<u4"), ("ctf/df1_A", "<f4")]
    if with_objects:
        fields.append(("blob/path", "O"))
    array = np.zeros(rows, dtype=fields)
    if with_objects:
        array["blob/path"] = ">J1/extract/particles.mrc"
    with open(path, "wb") as handle:
        np.save(handle, array)
    return Path(path)


def run(args, cwd, check=True):
    return subprocess.run(CLI + args, cwd=cwd, check=check, capture_output=True, text=True)


class CountTests(unittest.TestCase):
    def test_counts_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            for rows in (0, 1, 5, 123456):
                self.assertEqual(count_particles(make_cs(Path(directory) / f"p{rows}.cs", rows)), rows)

    def test_object_columns_are_not_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_cs(Path(directory) / "obj.cs", 9, with_objects=True)
            self.assertEqual(count_particles(path), 9)

    def test_rejects_non_numpy_and_scalars(self):
        with tempfile.TemporaryDirectory() as directory:
            text = Path(directory) / "text.cs"
            text.write_text("not a numpy file at all")
            with self.assertRaisesRegex(ValueError, "NumPy"):
                count_particles(text)
            scalar = Path(directory) / "scalar.cs"
            with open(scalar, "wb") as handle:
                np.save(handle, np.array(3))
            with self.assertRaisesRegex(ValueError, "single value"):
                count_particles(scalar)

    def test_missing_file_raises(self):
        with self.assertRaises(OSError):
            count_particles("does_not_exist.cs")


class CliTests(unittest.TestCase):
    def test_single_file(self):
        with tempfile.TemporaryDirectory() as directory:
            make_cs(Path(directory) / "a.cs", 4)
            result = run(["a.cs"], directory)
            self.assertEqual(result.stdout, "4\ta.cs\n")
            self.assertEqual(result.stderr, "")

    def test_wildcards_are_expanded_by_the_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            make_cs(folder / "b_2.cs", 2)
            make_cs(folder / "b_10.cs", 10)
            make_cs(folder / "other.txt", 99)
            result = run(["b_*.cs"], directory)          # no shell: the tool must expand it
            self.assertEqual(result.stdout.splitlines(), ["10\tb_10.cs", "2\tb_2.cs"])

    def test_several_arguments_keep_order_and_drop_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            make_cs(folder / "x.cs", 1)
            make_cs(folder / "y.cs", 2)
            result = run(["y.cs", "x.cs", "*.cs"], directory)
            self.assertEqual(result.stdout.splitlines(), ["2\ty.cs", "1\tx.cs"])

    def test_output_csv_has_header_and_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            make_cs(folder / "a.cs", 3)
            make_cs(folder / "b.cs", 8)
            (folder / "counts.csv").write_text("stale,data\n1,2\n3,4\n5,6\n")
            run(["*.cs", "--output-csv", "counts.csv"], directory)
            with open(folder / "counts.csv", newline="") as handle:
                self.assertEqual(list(csv.reader(handle)),
                                 [["file", "particles"], ["a.cs", "3"], ["b.cs", "8"]])

    def test_bad_input_is_loud_but_other_files_still_count(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            make_cs(folder / "good.cs", 6)
            (folder / "bad.cs").write_text("junk")
            result = run(["good.cs", "bad.cs", "missing.cs", "none_*.cs", "--output-csv", "o.csv"],
                         directory, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "6\tgood.cs\nWritten csv file to: o.csv\n")
            self.assertIn("bad.cs", result.stderr)
            self.assertIn("no file matches 'missing.cs'", result.stderr)
            self.assertIn("no file matches 'none_*.cs'", result.stderr)
            with open(folder / "o.csv", newline="") as handle:
                self.assertEqual(list(csv.reader(handle)), [["file", "particles"], ["good.cs", "6"]])

    def test_no_arguments_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(run([], directory, check=False).returncode, 2)


if __name__ == "__main__":
    unittest.main()
