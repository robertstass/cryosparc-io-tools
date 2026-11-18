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
        output = repeat_workflow(sample, 5, jobs=["J1", "J2"],
                                 exclude_remainder_jobs=False)
        self.assertEqual(len(output["jobs"]), 6)
        self.assertEqual(output["jobs"]["J6"]["groups"][0][0], "J1.class_0")
        only = repeat_workflow(sample, 5, jobs=["J1", "J2"], exclude_remainder_jobs=True)
        self.assertEqual(len(only["jobs"]), 5)
        self.assertTrue(all(j["jobType"] == "class_3D" for j in only["jobs"].values()))
        default_excluded = repeat_workflow(sample, 5, jobs=["J1", "J2"])
        self.assertEqual(len(default_excluded["jobs"]), 5)
        old = repeat_workflow(sample, 5, jobs=["J1", "J2"],
                              no_series_extension=True, exclude_remainder_jobs=False)
        self.assertEqual(len(old["jobs"]), 11)
        self.assertEqual(old["jobs"]["J11"]["groups"][0][0], "J1.class_0")

    def test_zero_based_single_job_series(self):
        sample = template()
        for parent in ("P2", "P3"):
            sample["jobs"]["J1"]["groups"][1 if parent == "P2" else 2][0] = (
                f"{parent}.imported_mask_0")
            sample["parents"][parent]["groups"]["mask"]["connections"] = ["imported_mask_0"]
        output = repeat_workflow(sample, 5)
        self.assertEqual([job["groups"][1][0] for job in output["jobs"].values()],
                         [f"P2.imported_mask_{i}" for i in range(5)])
        self.assertEqual(output["parents"]["P2"]["groups"]["mask"]["connections"],
                         [f"imported_mask_{i}" for i in range(5)])

    def test_zero_based_two_job_series_and_forced_block(self):
        sample = template()
        second = copy.deepcopy(sample["jobs"]["J1"])
        for parent, group_index in (("P2", 1), ("P3", 2)):
            sample["jobs"]["J1"]["groups"][group_index][0] = f"{parent}.imported_mask_0"
            sample["parents"][parent]["groups"]["mask"]["connections"] = ["imported_mask_0", "imported_mask_1"]
        sample["jobs"]["J2"] = second
        extended = repeat_workflow(sample, 5)
        self.assertEqual(len(extended["jobs"]), 5)
        self.assertEqual([job["groups"][2][0] for job in extended["jobs"].values()],
                         [f"P3.imported_mask_{i}" for i in range(5)])
        blocked = repeat_workflow(sample, 5, no_series_extension=True)
        self.assertEqual(len(blocked["jobs"]), 10)
        self.assertEqual([job["groups"][1][0] for job in blocked["jobs"].values()],
                         [f"P2.imported_mask_{i}" for i in range(10)])

    def test_reversed_zero_based_series_ascending(self):
        sample = template()
        # Deliberately reverse the source order: J1 -> 1, J2 -> 0.
        zero = copy.deepcopy(sample["jobs"]["J1"])
        for parent, group_index in (("P2", 1), ("P3", 2)):
            zero["groups"][group_index][0] = f"{parent}.imported_mask_0"
            sample["parents"][parent]["groups"]["mask"]["connections"] = [
                "imported_mask_1", "imported_mask_0"]
        sample["jobs"]["J2"] = zero
        output = repeat_workflow(sample, 5)
        self.assertEqual(list(output["jobs"]), [f"J{i}" for i in range(1, 6)])
        for parent, group_index in (("P2", 1), ("P3", 2)):
            self.assertEqual([job["groups"][group_index][0] for job in output["jobs"].values()],
                             [f"{parent}.imported_mask_{i}" for i in range(5)])
            self.assertEqual(output["parents"][parent]["groups"]["mask"]["connections"],
                             [f"imported_mask_{i}" for i in range(5)])

    def test_reversed_one_based_series_ascending(self):
        sample = template()
        low = copy.deepcopy(sample["jobs"]["J1"])
        for group_index, parent in ((1, "P2"), (2, "P3")):
            sample["jobs"]["J1"]["groups"][group_index][0] = f"{parent}.imported_mask_2"
        sample["jobs"]["J2"] = low
        result = repeat_workflow(sample, 5)
        self.assertEqual([job["groups"][1][0] for job in result["jobs"].values()],
                         [f"P2.imported_mask_{i}" for i in range(1, 6)])

    def test_reversed_series_selected_parent_uses_lowest_index_prototype(self):
        sample = template()
        low = copy.deepcopy(sample["jobs"]["J1"])
        # P2 determines series order; P3 has independent, reversed indices.
        sample["jobs"]["J1"]["groups"][1][0] = "P2.imported_mask_1"
        sample["jobs"]["J1"]["groups"][2][0] = "P3.imported_mask_0"
        low["groups"][1][0] = "P2.imported_mask_0"
        low["groups"][2][0] = "P3.imported_mask_1"
        sample["jobs"]["J2"] = low
        output = repeat_workflow(sample, 5, selected_parents=["P2"])
        self.assertEqual(len(output["jobs"]), 5)
        self.assertEqual([job["groups"][1][0] for job in output["jobs"].values()],
                         [f"P2.imported_mask_{i}" for i in range(5)])
        self.assertEqual([job["groups"][2][0] for job in output["jobs"].values()],
                         ["P3.imported_mask_1"] * 5)
        self.assertEqual(output["parents"]["P3"]["groups"]["mask"]["connections"],
                         ["imported_mask_1"])

    def test_reversed_series_forced_block_and_parent_filter_ascending(self):
        sample = template()
        low = copy.deepcopy(sample["jobs"]["J1"])
        sample["jobs"]["J1"]["groups"][1][0] = "P2.imported_mask_1"
        sample["jobs"]["J1"]["groups"][2][0] = "P3.imported_mask_0"
        low["groups"][1][0] = "P2.imported_mask_0"
        low["groups"][2][0] = "P3.imported_mask_1"
        sample["jobs"]["J2"] = low
        output = repeat_workflow(sample, 3, selected_parents=["P2"],
                                 no_series_extension=True)
        self.assertEqual(len(output["jobs"]), 6)
        self.assertEqual([job["groups"][1][0] for job in output["jobs"].values()],
                         [f"P2.imported_mask_{i}" for i in range(6)])
        self.assertEqual([job["groups"][2][0] for job in output["jobs"].values()],
                         ["P3.imported_mask_1", "P3.imported_mask_0"] * 3)
        self.assertEqual(output["parents"]["P3"]["groups"]["mask"]["connections"],
                         ["imported_mask_0", "imported_mask_1"])

    def test_cli_reversed_series_still_ascending(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            sample = template()
            low = copy.deepcopy(sample["jobs"]["J1"])
            for group_index, parent in ((1, "P2"), (2, "P3")):
                low["groups"][group_index][0] = f"{parent}.imported_mask_0"
            sample["jobs"]["J2"] = low
            source = folder / "reverse.json"
            source.write_text(json.dumps(sample))
            subprocess.run([sys.executable, "-B", str(ROOT / "cryosparc_workflow_repeat.py"),
                            str(source), "-n", "5"], cwd=folder, check=True,
                           capture_output=True)
            output = json.loads((folder / "reverse_repeated_5.json").read_text())
            self.assertEqual([job["groups"][1][0] for job in output["jobs"].values()],
                             [f"P2.imported_mask_{i}" for i in range(5)])

    def test_parent_selection_advances_only_selected_parent(self):
        sample = template()
        second = copy.deepcopy(sample["jobs"]["J1"])
        second["groups"][1][0] = "P2.imported_mask_2"
        second["groups"][2][0] = "P3.imported_mask_2"
        sample["jobs"]["J2"] = second
        result = repeat_workflow(sample, 5, selected_parents=["P1", "P2"])
        self.assertEqual([job["groups"][1][0] for job in result["jobs"].values()],
                         [f"P2.imported_mask_{i}" for i in range(1, 6)])
        self.assertEqual([job["groups"][2][0] for job in result["jobs"].values()],
                         ["P3.imported_mask_1"] * 5)
        self.assertEqual(result["parents"]["P2"]["groups"]["mask"]["connections"],
                         [f"imported_mask_{i}" for i in range(1, 6)])
        self.assertEqual(result["parents"]["P3"]["groups"]["mask"]["connections"],
                         ["imported_mask_1"])
        with self.assertRaisesRegex(ValueError, "Unknown parent"):
            repeat_workflow(sample, 5, selected_parents=["P9"])
        with self.assertRaisesRegex(ValueError, "No numbered parent outputs"):
            repeat_workflow(sample, 5, selected_parents=["P1"])

    def test_parent_selection_with_forced_block_repetition(self):
        sample = template()
        second = copy.deepcopy(sample["jobs"]["J1"])
        second["groups"][1][0] = "P2.imported_mask_2"
        second["groups"][2][0] = "P3.imported_mask_2"
        sample["jobs"]["J2"] = second
        result = repeat_workflow(sample, 3, selected_parents=["P2"], no_series_extension=True)
        self.assertEqual(len(result["jobs"]), 6)
        self.assertEqual([job["groups"][1][0] for job in result["jobs"].values()],
                         [f"P2.imported_mask_{i}" for i in range(1, 7)])
        self.assertEqual([job["groups"][2][0] for job in result["jobs"].values()],
                         ["P3.imported_mask_1", "P3.imported_mask_2"] * 3)

    def test_cli_parent_selection_and_remainder_switches(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            sample = template()
            second = copy.deepcopy(sample["jobs"]["J1"])
            second["groups"][1][0] = "P2.imported_mask_2"
            second["groups"][2][0] = "P3.imported_mask_2"
            sample["jobs"]["J2"] = second
            extra = copy.deepcopy(second)
            extra["jobType"] = "other_type"
            extra["groups"] = [["P1.particles", "particles"]]
            sample["jobs"]["J3"] = extra
            source = folder / "input.json"
            source.write_text(json.dumps(sample))
            command = [sys.executable, "-B", str(ROOT / "cryosparc_workflow_repeat.py"),
                       str(source), "-n", "5", "--jobs", "J1", "J2", "--parents", "P1", "P2"]
            subprocess.run(command, check=True, capture_output=True, cwd=folder)
            generated = json.loads((folder / "input_repeated_5.json").read_text())
            self.assertEqual(len(generated["jobs"]), 5)
            self.assertEqual(generated["jobs"]["J5"]["groups"][1][0], "P2.imported_mask_5")
            self.assertEqual(generated["jobs"]["J5"]["groups"][2][0], "P3.imported_mask_1")
            subprocess.run(command + ["--include-remainder-jobs", "-o", "included.json"],
                           check=True, capture_output=True, cwd=folder)
            self.assertEqual(len(json.loads((folder / "included.json").read_text())["jobs"]), 6)
            failed = subprocess.run(command + ["--include-remainder-jobs", "--exclude-remainder-jobs"],
                                    capture_output=True, text=True, cwd=folder)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("not allowed with", failed.stderr)

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

    @staticmethod
    def _refinement_series_with_fixed_mask():
        """Synthetic two-job, reverse-ordered class_0/1 series (no JSON fixture)."""
        sample = template()
        sample["jobs"] = {}
        sample["parents"] = {
            "P1": {"jobType": "class_3D", "groups": {
                "particle": {"name": "particles", "connections": ["particles_class_1", "particles_class_0"],
                             "required": ["blob", "ctf"]},
                "volume": {"name": "volume", "connections": ["volume_class_1", "volume_class_0"],
                           "required": ["map"]}}},
            "P2": {"jobType": "import_volumes", "groups": {
                "mask": {"name": "mask", "connections": ["imported_mask_1"], "required": ["mask"]}}}
        }
        for job_id, index in (("J1", 1), ("J2", 0)):
            sample["jobs"][job_id] = {
                "title": "", "description": "", "jobType": "nonuniform_refine_new",
                "groups": [[f"P1.particles_class_{index}", "particles"],
                           [f"P1.volume_class_{index}", "volume"],
                           ["P2.imported_mask_1", "mask"]],
                "individualResults": [], "parameters": {}
            }
        return sample

    def test_zero_based_mixed_fixed_mask_series(self):
        sample = self._refinement_series_with_fixed_mask()
        output = repeat_workflow(sample, 5)
        self.assertEqual(list(output["jobs"]), [f"J{i}" for i in range(1, 6)])
        for i, job in enumerate(output["jobs"].values()):
            self.assertEqual(job["groups"], [[f"P1.particles_class_{i}", "particles"],
                                              [f"P1.volume_class_{i}", "volume"],
                                              ["P2.imported_mask_1", "mask"]])
        self.assertEqual(output["parents"]["P1"]["groups"]["particle"]["connections"],
                         [f"particles_class_{i}" for i in range(5)])
        self.assertEqual(output["parents"]["P1"]["groups"]["volume"]["connections"],
                         [f"volume_class_{i}" for i in range(5)])
        # The mask is the same in both source jobs, so it is pinned.
        self.assertEqual(output["parents"]["P2"]["groups"]["mask"]["connections"],
                         ["imported_mask_1"])
        self.assertEqual(sample["jobs"]["J1"]["groups"][0][0], "P1.particles_class_1")

    def test_zero_based_mixed_fixed_mask_with_parents_filter(self):
        sample = self._refinement_series_with_fixed_mask()
        default = repeat_workflow(sample, 5)
        for selected in (["P1"], ["P1", "P2"]):
            with self.subTest(selected=selected):
                output = repeat_workflow(sample, 5, selected_parents=selected)
                self.assertEqual(len(output["jobs"]), 5)
                self.assertEqual([job["groups"][0][0] for job in output["jobs"].values()],
                                 [f"P1.particles_class_{i}" for i in range(5)])
                self.assertEqual([job["groups"][2][0] for job in output["jobs"].values()],
                                 ["P2.imported_mask_1"] * 5)
                self.assertEqual(output["jobs"], default["jobs"])

    def test_zero_based_mixed_fixed_mask_forced_block(self):
        output = repeat_workflow(self._refinement_series_with_fixed_mask(), 5,
                                 no_series_extension=True)
        self.assertEqual(len(output["jobs"]), 10)
        self.assertEqual([job["groups"][0][0] for job in output["jobs"].values()],
                         [f"P1.particles_class_{i}" for i in range(10)])
        self.assertEqual([job["groups"][2][0] for job in output["jobs"].values()],
                         ["P2.imported_mask_1"] * 10)

    def test_cli_zero_based_mixed_fixed_mask(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "refinement.json"
            source.write_text(json.dumps(self._refinement_series_with_fixed_mask()))
            subprocess.run([sys.executable, "-B", str(ROOT / "cryosparc_workflow_repeat.py"),
                            str(source), "-n", "5"], cwd=folder, check=True, capture_output=True)
            output = json.loads((folder / "refinement_repeated_5.json").read_text())
            self.assertEqual(len(output["jobs"]), 5)
            self.assertEqual(output["jobs"]["J1"]["groups"][0][0], "P1.particles_class_0")
            self.assertEqual(output["jobs"]["J5"]["groups"][1][0], "P1.volume_class_4")
            self.assertEqual(output["jobs"]["J5"]["groups"][2][0], "P2.imported_mask_1")
            self.assertEqual(output["parents"]["P2"]["groups"]["mask"]["connections"],
                             ["imported_mask_1"])

    def test_single_selected_job_advances_mask_that_all_jobs_pin(self):
        """-n 6 pins the shared mask; --jobs J2 has no such evidence so it advances."""
        sample = self._refinement_series_with_fixed_mask()
        everything = repeat_workflow(sample, 6)
        self.assertEqual({j["groups"][2][0] for j in everything["jobs"].values()}, {"P2.imported_mask_1"})
        for selected in (["J1"], ["J2"]):
            with self.subTest(selected=selected):
                single = repeat_workflow(sample, 6, jobs=selected)
                # The class series (order and zero-based start) matches the full run.
                self.assertEqual([j["groups"][:2] for j in single["jobs"].values()],
                                 [j["groups"][:2] for j in everything["jobs"].values()])
                self.assertEqual([j["groups"][2][0] for j in single["jobs"].values()],
                                 [f"P2.imported_mask_{i}" for i in range(1, 7)])
                self.assertEqual(single["parents"]["P2"]["groups"]["mask"]["connections"],
                                 [f"imported_mask_{i}" for i in range(1, 7)])

    def test_single_selected_job_with_parents_filter_and_fixed_mask(self):
        sample = self._refinement_series_with_fixed_mask()
        output = repeat_workflow(sample, 6, jobs=["J2"], selected_parents=["P1"])
        self.assertEqual([j["groups"][0][0] for j in output["jobs"].values()],
                         [f"P1.particles_class_{i}" for i in range(6)])
        self.assertEqual([j["groups"][2][0] for j in output["jobs"].values()],
                         ["P2.imported_mask_1"] * 6)

    def test_single_selected_job_lockstep_series_stays_zero_based(self):
        sample = self._refinement_series_with_fixed_mask()
        sample["parents"]["P2"]["groups"]["mask"]["connections"] = ["imported_mask_1", "imported_mask_0"]
        for job_id, index in (("J1", 1), ("J2", 0)):
            sample["jobs"][job_id]["groups"][2][0] = f"P2.imported_mask_{index}"
        everything = repeat_workflow(sample, 4)
        single = repeat_workflow(sample, 4, jobs=["J2"])
        self.assertEqual([j["groups"] for j in single["jobs"].values()],
                         [j["groups"] for j in everything["jobs"].values()])
        self.assertEqual([j["groups"][2][0] for j in single["jobs"].values()],
                         [f"P2.imported_mask_{i}" for i in range(4)])
        # Fixing the mask keeps it constant for the single-job case too.
        fixed = repeat_workflow(sample, 4, jobs=["J2"], selected_parents=["P1"])
        self.assertEqual([j["groups"][2][0] for j in fixed["jobs"].values()],
                         ["P2.imported_mask_0"] * 4)

    def test_cli_single_job_matches_default_and_parents_default_is_all(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            sample = self._refinement_series_with_fixed_mask()
            # Make the mask vary with the class index so "all parents" matters.
            sample["parents"]["P2"]["groups"]["mask"]["connections"] = ["imported_mask_1", "imported_mask_0"]
            for job_id, index in (("J1", 1), ("J2", 0)):
                sample["jobs"][job_id]["groups"][2][0] = f"P2.imported_mask_{index}"
            (folder / "test.json").write_text(json.dumps(sample))

            def run(name, *extra):
                subprocess.run([sys.executable, "-B", str(ROOT / "cryosparc_workflow_repeat.py"),
                                "test.json", "-n", "6", "-o", name, *extra],
                               cwd=folder, check=True, capture_output=True)
                return [j["groups"] for j in json.loads((folder / name).read_text())["jobs"].values()]

            default = run("a.json")
            self.assertEqual(default, run("b.json", "--jobs", "J2"))
            self.assertEqual(default, run("c.json", "--parents", "P1", "P2"))   # explicit all
            self.assertEqual([g[2][0] for g in default], [f"P2.imported_mask_{i}" for i in range(6)])
            self.assertEqual({g[2][0] for g in run("d.json", "--parents", "P1")}, {"P2.imported_mask_0"})

    @classmethod
    def _offset_series(cls, pairs=((0, 1), (1, 2))):
        """Series whose slots start at different indices: classes 0.., masks 1.."""
        sample = cls._refinement_series_with_fixed_mask()
        sample["parents"]["P2"]["groups"]["mask"]["connections"] = [f"imported_mask_{m}" for _, m in pairs]
        for number, (cls_index, mask_index) in enumerate(pairs, 1):
            sample["jobs"][f"J{number}"] = copy.deepcopy(sample["jobs"]["J1"])
            sample["jobs"][f"J{number}"]["groups"] = [
                [f"P1.particles_class_{cls_index}", "particles"],
                [f"P1.volume_class_{cls_index}", "volume"],
                [f"P2.imported_mask_{mask_index}", "mask"]]
        return sample

    def test_slots_advance_from_their_own_start_index(self):
        sample = self._offset_series()
        for label, kwargs in (("all", {}), ("J1", {"jobs": ["J1"]}), ("J2", {"jobs": ["J2"]})):
            with self.subTest(label=label):
                output = repeat_workflow(sample, 5, **kwargs)
                self.assertEqual([j["groups"][0][0] for j in output["jobs"].values()],
                                 [f"P1.particles_class_{i}" for i in range(5)])
                self.assertEqual([j["groups"][2][0] for j in output["jobs"].values()],
                                 [f"P2.imported_mask_{i}" for i in range(1, 6)])
        self.assertEqual(output["parents"]["P2"]["groups"]["mask"]["connections"],
                         [f"imported_mask_{i}" for i in range(1, 6)])
        self.assertEqual(output["parents"]["P1"]["groups"]["particle"]["connections"],
                         [f"particles_class_{i}" for i in range(5)])

    def test_offset_series_reversed_and_forced_block(self):
        sample = self._offset_series(((1, 2), (0, 1)))     # J1 is the later step
        output = repeat_workflow(sample, 3, no_series_extension=True)
        self.assertEqual([j["groups"][0][0] for j in output["jobs"].values()],
                         [f"P1.particles_class_{i}" for i in range(6)])
        self.assertEqual([j["groups"][2][0] for j in output["jobs"].values()],
                         [f"P2.imported_mask_{i}" for i in range(1, 7)])

    def test_offset_series_with_parents_filter(self):
        sample = self._offset_series()
        output = repeat_workflow(sample, 4, selected_parents=["P1"])
        self.assertEqual([j["groups"][0][0] for j in output["jobs"].values()],
                         [f"P1.particles_class_{i}" for i in range(4)])
        self.assertEqual({j["groups"][2][0] for j in output["jobs"].values()}, {"P2.imported_mask_1"})

    def test_parents_selection_with_single_job_and_all_jobs(self):
        sample = self._refinement_series_with_fixed_mask()
        advancing = repeat_workflow(sample, 6, jobs=["J2"])["jobs"]
        # Naming every parent changes nothing; --parents P1 pins the mask again.
        self.assertEqual(repeat_workflow(sample, 6, jobs=["J2"], selected_parents=["P1", "P2"])["jobs"], advancing)
        pinned = repeat_workflow(sample, 6, jobs=["J2"], selected_parents=["P1"])
        self.assertEqual({j["groups"][2][0] for j in pinned["jobs"].values()}, {"P2.imported_mask_1"})
        self.assertEqual(pinned["parents"]["P2"]["groups"]["mask"]["connections"], ["imported_mask_1"])
        # With all jobs the mask is pinned by the input whichever parents are named.
        self.assertEqual(repeat_workflow(sample, 6, selected_parents=["P1", "P2"])["jobs"],
                         repeat_workflow(sample, 6)["jobs"])

    def test_constant_slot_pinned_when_two_series_jobs_selected_among_others(self):
        sample = self._refinement_series_with_fixed_mask()
        sample["jobs"]["J3"] = {"title": "", "description": "", "jobType": "other",
                                "groups": [["P2.imported_mask_1", "mask"]],
                                "individualResults": [], "parameters": {}}
        output = repeat_workflow(sample, 4, jobs=["J1", "J2", "J3"])
        series = [j for j in output["jobs"].values() if j["jobType"] == "nonuniform_refine_new"]
        self.assertEqual([j["groups"][0][0] for j in series], [f"P1.particles_class_{i}" for i in range(8)][:len(series)])
        self.assertEqual({j["groups"][2][0] for j in series}, {"P2.imported_mask_1"})

    def test_lone_job_without_siblings_advances_each_slot_from_its_own_start(self):
        sample = self._refinement_series_with_fixed_mask()
        del sample["jobs"]["J1"]
        sample["jobs"]["J2"]["groups"][2][0] = "P2.imported_mask_1"   # class_0 + mask_1
        output = repeat_workflow(sample, 5)
        self.assertEqual([j["groups"][0][0] for j in output["jobs"].values()],
                         [f"P1.particles_class_{i}" for i in range(5)])
        self.assertEqual([j["groups"][2][0] for j in output["jobs"].values()],
                         [f"P2.imported_mask_{i}" for i in range(1, 6)])

    def test_reject_unknown_parameter(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            set_parameters(template(), ["not_a_parameter=5"])


if __name__ == "__main__":
    unittest.main()
