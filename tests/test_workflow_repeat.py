import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cryosparc_io_tools.cryosparc_workflow_repeat import repeat_workflow, set_parameters, template


class RepeatTests(unittest.TestCase):
    def test_bundled_template_uses_mask_1(self):
        workflow = template()
        self.assertEqual(workflow["jobs"]["J1"]["groups"][1][0], "P2.imported_mask_1")
        self.assertEqual(workflow["jobs"]["J1"]["groups"][2][0], "P3.imported_mask_1")
        self.assertEqual(workflow["parents"]["P2"]["groups"]["mask"]["connections"], ["imported_mask_1"])
        self.assertEqual(workflow["parents"]["P3"]["groups"]["mask"]["connections"], ["imported_mask_1"])

    def test_repeat_12_ascending(self):
        original = template()
        output = repeat_workflow(original, 12)
        self.assertEqual(len(output["jobs"]), 12)
        for idx in range(1, 13):
            groups = output["jobs"][f"J{idx}"]["groups"]
            self.assertEqual(groups, [["P1.particles", "particles"],
                                      [f"P2.imported_mask_{idx}", "mask"],
                                      [f"P3.imported_mask_{idx}", "mask_focus"]])
        for parent in ("P2", "P3"):
            self.assertEqual(output["parents"][parent]["groups"]["mask"]["connections"],
                             [f"imported_mask_{i}" for i in range(1, 13)])
        self.assertEqual(original["jobs"]["J1"]["groups"][1][0], "P2.imported_mask_1")

    def test_override(self):
        workflow = repeat_workflow(template(), 3)
        set_parameters(workflow, ["class3D_N_K=5", "J2:class3D_target_res=10.5"])
        self.assertEqual([j["parameters"]["class3D_N_K"]["value"] for j in workflow["jobs"].values()], [5, 5, 5])
        self.assertEqual(workflow["jobs"]["J2"]["parameters"]["class3D_target_res"]["value"], 10.5)
        self.assertEqual(workflow["jobs"]["J1"]["parameters"]["class3D_target_res"]["value"], 15)

    def test_multi_job_staggered_indices(self):
        sample = template()
        one = copy.deepcopy(sample["jobs"]["J1"])
        sample["jobs"]["J1"]["groups"][1][0] = "P2.imported_mask_2"
        sample["jobs"]["J1"]["groups"][2][0] = "P3.imported_mask_2"
        one["groups"][1][0] = "P2.imported_mask_1"
        one["groups"][2][0] = "P3.imported_mask_1"
        sample["jobs"] = {"J1": sample["jobs"]["J1"], "J2": one}
        result = repeat_workflow(sample, 5)
        self.assertEqual([v["groups"][1][0] for v in result["jobs"].values()],
                         [f"P2.imported_mask_{i}" for i in range(1, 6)])
        self.assertEqual(result["parents"]["P2"]["groups"]["mask"]["connections"],
                         [f"imported_mask_{i}" for i in range(1, 6)])
        old_behavior = repeat_workflow(sample, 5, no_series_extension=True)
        self.assertEqual(len(old_behavior["jobs"]), 10)
        self.assertEqual([v["groups"][1][0] for v in old_behavior["jobs"].values()],
                         [f"P2.imported_mask_{i}" for i in range(1, 11)])


    def test_nonseries_parameters_trigger_block_repetition(self):
        sample = template()
        second = copy.deepcopy(sample["jobs"]["J1"])
        second["groups"][1][0] = "P2.imported_mask_2"
        second["groups"][2][0] = "P3.imported_mask_2"
        second["parameters"]["class3D_N_K"]["value"] = 8
        sample["jobs"]["J2"] = second
        result = repeat_workflow(sample, 5)
        self.assertEqual(len(result["jobs"]), 10)

    def test_selection_and_remainder(self):
        sample = template()
        original = copy.deepcopy(sample["jobs"]["J1"])
        original["groups"][1][0] = "P2.imported_mask_2"
        original["groups"][2][0] = "P3.imported_mask_2"
        sample["jobs"]["J2"] = original
        extra = copy.deepcopy(original)
        extra["jobType"] = "heterogeneous_refine"
        extra["groups"] = [["J1.class_0", "volume"]]
        sample["jobs"]["J3"] = extra
        output = repeat_workflow(sample, 5, jobs=["J1", "J2"])
        self.assertEqual(len(output["jobs"]), 6)
        self.assertEqual(output["jobs"]["J6"]["groups"][0][0], "J1.class_0")
        only = repeat_workflow(sample, 5, jobs=["J1", "J2"], exclude_remainder_jobs=True)
        self.assertEqual(len(only["jobs"]), 5)
        self.assertTrue(all(j["jobType"] == "class_3D" for j in only["jobs"].values()))
        old = repeat_workflow(sample, 5, jobs=["J1", "J2"], no_series_extension=True)
        self.assertEqual(len(old["jobs"]), 11)
        self.assertEqual(old["jobs"]["J11"]["groups"][0][0], "J1.class_0")

    def test_selected_single_job_repeats_and_unknown_id(self):
        sample = template()
        sample["jobs"]["J2"] = copy.deepcopy(sample["jobs"]["J1"])
        result = repeat_workflow(sample, 5, jobs=["J1"], exclude_remainder_jobs=True)
        self.assertEqual(len(result["jobs"]), 5)
        with self.assertRaisesRegex(ValueError, "Unknown job"):
            repeat_workflow(sample, 3, jobs=["J99"])

    def test_cli_series_selection_from_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            sample = template()
            second = copy.deepcopy(sample["jobs"]["J1"])
            second["groups"][1][0] = "P2.imported_mask_2"
            second["groups"][2][0] = "P3.imported_mask_2"
            sample["jobs"]["J2"] = second
            source = folder / "series.json"
            source.write_text(json.dumps(sample))
            command = [sys.executable, "-B", str(ROOT / "cryosparc_workflow_repeat.py"),
                       str(source), "-n", "5", "--jobs", "J1", "J2", "--exclude-remainder-jobs"]
            subprocess.run(command, check=True, capture_output=True, cwd=folder)
            generated = json.loads((folder / "series_repeated_5.json").read_text())
            self.assertEqual(len(generated["jobs"]), 5)
            self.assertEqual(generated["jobs"]["J5"]["groups"][1][0], "P2.imported_mask_5")
            subprocess.run(command + ["--no-series-extension", "-o", "legacy.json"],
                           check=True, capture_output=True, cwd=folder)
            self.assertEqual(len(json.loads((folder / "legacy.json").read_text())["jobs"]), 10)

    def test_internal_job_reference(self):
        sample = template()
        second = copy.deepcopy(sample["jobs"]["J1"])
        second["groups"] = [["J1.class_0", "volume"]]
        sample["jobs"]["J2"] = second
        output = repeat_workflow(sample, 2)
        self.assertEqual(output["jobs"]["J2"]["groups"][0][0], "J1.class_0")
        self.assertEqual(output["jobs"]["J4"]["groups"][0][0], "J3.class_0")

    def test_cli_no_repeats_exports_unchanged_template(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "workflow.json"
            subprocess.run([sys.executable, "-B", str(ROOT / "cryosparc_workflow_repeat.py"), "-o", str(output)], check=True, capture_output=True, cwd=directory)
            self.assertEqual(json.loads(output.read_text()), template())

    def test_cli_default_output_is_in_calling_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([sys.executable, "-B", str(ROOT / "cryosparc_workflow_repeat.py"), "-n", "2"],
                           check=True, capture_output=True, cwd=directory)
            output = Path(directory) / "workflow_repeated_2.json"
            self.assertTrue(output.is_file())
            self.assertEqual(len(json.loads(output.read_text())["jobs"]), 2)


    def test_template_metadata(self):
        w = template()
        self.assertEqual(w["category"], "Workflow Repeat")
        self.assertIn("wildcard expansion", w["description"])

    def test_cli_custom_input_name_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "my_masks.json"
            source.write_text(json.dumps(template()))
            subprocess.run([sys.executable, "-B", str(ROOT / "cryosparc_workflow_repeat.py"),
                            str(source), "-n", "2", "--set", "class3D_N_K=5"],
                           check=True, capture_output=True, cwd=directory)
            result = json.loads((folder / "my_masks_repeated_2_class3D_N_K_5.json").read_text())
            self.assertNotEqual(result["_id"], template()["_id"])
            self.assertRegex(result["_id"], r"^[0-9a-f]{24}$")
            self.assertNotEqual(result["createdAt"], template()["createdAt"])
            self.assertEqual(result["title"], "class_3D repeated 2 times class3D_N_K=5")
            self.assertIn("class3D_N_K=5", result["title"])

    def test_multiple_overrides_in_default_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([sys.executable, "-B", str(ROOT / "cryosparc_workflow_repeat.py"),
                            "-n", "2", "--set", "class3D_N_K=5",
                            "--set", "class3D_target_res=10.5"],
                           check=True, capture_output=True, cwd=directory)
            output = Path(directory) / "workflow_repeated_2_class3D_N_K_5_class3D_target_res_10.5.json"
            self.assertTrue(output.is_file())
            self.assertEqual(len(json.loads(output.read_text())["jobs"]), 2)

    def test_explicit_output_unchanged_by_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "chosen.json"
            subprocess.run([sys.executable, "-B", str(ROOT / "cryosparc_workflow_repeat.py"),
                            "-n", "2", "--set", "class3D_N_K=5", "-o", str(output)],
                           check=True, capture_output=True, cwd=directory)
            self.assertTrue(output.is_file())

    def test_cli_metadata_opt_out(self):
        with tempfile.TemporaryDirectory() as directory:
            source = template()
            output = Path(directory) / "out.json"
            subprocess.run([sys.executable, "-B", str(ROOT / "cryosparc_workflow_repeat.py"),
                            "-n", "2", "--no-id-update", "--no-date-update",
                            "--no-title-update", "-o", str(output)],
                           check=True, capture_output=True, cwd=directory)
            result = json.loads(output.read_text())
            for field in ("_id", "createdAt", "title"):
                self.assertEqual(result[field], source[field])

    def test_reject_unknown_parameter(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            set_parameters(template(), ["not_a_parameter=5"])


if __name__ == "__main__":
    unittest.main()
