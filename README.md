# cryosparc-io-tools

Small command-line tools for CryoSPARC files.

| Tool | Does |
| --- | --- |
| `cryosparc_workflow_repeat.py` | Repeat workflow jobs and sweep parameters, like a for loop for CryoSPARC. |
| `cryosparc_cs_count.py` | Count the particles in `.cs` files. |
| `cryosparc_csg_grep.py` | Pull values out of `.csg` (or any YAML) files by key path. |

## Install

Python 3.10+. Run straight from a checkout, or install (`uv tool install -e .` or `python -m pip install .`) to get the commands `cryosparc-workflow-repeat`, `cryosparc-cs-count`, `cryosparc-csg-grep` and `cryosparc-io-tools-test`.

The workflow repeater has no dependencies. `cs_count` needs **numpy** and `csg_grep` needs **PyYAML** (both installed with the package).

## cryosparc_workflow_repeat.py

```bash
python cryosparc_workflow_repeat.py                              # export the bundled example template
python cryosparc_workflow_repeat.py -n 12 -o class_3d_12.json    # 12 jobs from the template
python cryosparc_workflow_repeat.py my.json -n 12 --set class3D_N_K=5
python cryosparc_workflow_repeat.py my.json -n 12 --set class3D_N_K=2:10:2   # sweep (see below)
```

With no `-n` and no sweep the input is written unchanged (apart from scalar `--set` values), so you can export a template and edit it. Input files are never overwritten.

| Option | Meaning |
| --- | --- |
| `input` | Workflow JSON (default: bundled `class_3d_template.json`). |
| `-n, --repeats N` | Series of identical-but-for-index jobs: extend to **N jobs**. Otherwise: N copies of each selected job. |
| `--no-series-extension` | Always use N copies per selected job. |
| `--jobs J1 J2` | Only repeat these jobs (default: all). |
| `--include-remainder-jobs` / `--exclude-remainder-jobs` | Keep the unselected jobs once, or drop them (default). |
| `--parents P1 P2` | Advance numbered inputs only for these parents; others stay fixed (default: all). |
| `--parents none` | Advance nothing: `-n 5 --parents none` is plain replication. |
| `--set [JOB:]PARAM=VALUE` | Set a parameter. Value is JSON if it parses, else text. Ranges and lists sweep (below). |
| `--combine product\|zip` | Several sweeps: every combination (default) or in lockstep. |
| `--job-sep`, `--range-sep`, `--list-sep` | Change the `:`, `:` and `,` used in `--set`. |
| `--max-jobs N` | Refuse to write more than N jobs (default 1000). |
| `-o, --output FILE` | Output path (default below). |
| `--no-id-update`, `--no-date-update`, `--no-title-update` | Keep the source `_id`, `createdAt` or title. |

### Sweeping parameters

Each range or list in `--set` is an **axis**; the repeated jobs are copied once per combination of axis values.

| Value | Meaning |
| --- | --- |
| `K=5` | One value (not a sweep). |
| `K=2:10:2` | `start:stop:step` = 2, 4, 6, 8, 10 (**stop inclusive**). |
| `K=2:10` | Step defaults to 1 (or -1 if stop < start). |
| `res=0.1:0.5:0.1` | Float steps are exact. |
| `K=2,4,8` | Curated list; items may be ranges (`2:4,8,10:20:5`). |
| `J2:K=2:4` | Sweep only source job `J2`. |

```bash
python cryosparc_workflow_repeat.py my.json -n 12 --set K=2:10:2     # 12 masks x 5 values = 60 jobs
python cryosparc_workflow_repeat.py my.json --set K=2:10:2           # 5 jobs, masks untouched
python cryosparc_workflow_repeat.py my.json -n 5 --parents none --set res=8,10,12   # 5 identical repeats x 3
python cryosparc_workflow_repeat.py my.json --set K=2:10:2 --set res=8,10,12 --combine zip   # needs equal lengths
```

- `-n` and sweeps are independent: `-n` advances numbered connections (masks 1..N), sweeps multiply on top. The first swept `--set` varies slowest, the `-n` position fastest. `-n 1` renumbers connections to start at the first index; omit `-n` to leave them alone.
- `JOB:` is a job ID **in the source workflow**. In an extended series any member's ID selects the whole series. Targeted values are applied after untargeted ones.
- Only the jobs a sweep changes, plus everything downstream of them, are copied. Other jobs are left out (a note says which) unless `--include-remainder-jobs`, which keeps them once after the copies. A copied job that uses a left-out job is an error. So with two jobs, `--set J2:K=2:8:1` gives 7 jobs, or 8 with `--include-remainder-jobs`. Kept jobs take scalar values only.
- A value is a range only if every piece is a number, so `C:/data` stays text. Quote numeric-looking text as JSON (`'"12:30:00"'`). JSON lists (`[1,2]`) are single list-valued parameters, not sweeps.

### How repetition works

- **Series.** If at least two selected jobs are identical except for numbered parent-output indices (like `imported_mask_1`, `imported_mask_2`) and agree on order, `-n 5` extends the series to 5 jobs. Input order doesn't matter, a zero-based series stays zero-based, and inputs that are the same in every job (one shared mask) stay pinned. Otherwise it falls back to block repetition.
- **Block repetition.** Each selected job is copied N times; references between selected jobs follow each copy, external inputs are kept.
- **Indices.** Each numbered input counts up from its own start index (0 if it uses `_0`, else 1). Names need a trailing `_<number>` and the parent must declare at least one such name.
- **Single job.** `--jobs J2` advances every numbered input of the selected parents; use `--parents` to pin the rest.
- **Parents.** `--parents` chooses which outputs are reindexed, not which jobs are selected. Parent `connections` are rebuilt from the generated jobs.
- **Excluded jobs.** With `--jobs`, other jobs are dropped unless `--include-remainder-jobs`; check dependencies if you drop a job others use.

The tool edits workflow JSON only; it doesn't check that the parent outputs exist in a project, so inspect the result before importing it into CryoSPARC.

### Output names and metadata

Default output is `workflow.json` (no input), `<input-stem>.json`, or with `-n` `<input-stem>_repeated_<N>.json` (`workflow_repeated_<N>.json` for the template), with `--set` text appended (unsafe characters become `_`). With `-n` or a sweep a fresh `_id`, `createdAt` and `title` are generated.

## cryosparc_cs_count.py

Prints the number of particles in each `.cs` file as `count<TAB>file`. Only the NumPy header is read, so it is instant on huge files.

```bash
python cryosparc_cs_count.py J42/*.cs
python cryosparc_cs_count.py particles_*.cs --output-csv counts.csv    # columns: file,particles
```

Wildcards are expanded by the tool (so they work on Windows too). Unreadable files and patterns that match nothing are reported on stderr, the rest are still counted, and the exit code is 1. NumPy-format `.cs` files only. An existing CSV is overwritten.

## cryosparc_csg_grep.py

Prints values from `.csg` or any YAML file. Every value has a path such as `results.blob.num_items`; list items use their index (`items.0.name`).

```bash
python cryosparc_csg_grep.py J267_class_0.csg -k results.blob.num_items
python cryosparc_csg_grep.py *.csg -k results.blob.num_items -k group.name --output-csv summary.csv
python cryosparc_csg_grep.py *.csg -k 'results.*.num_items' --long --output-csv long.csv
python cryosparc_csg_grep.py a.csg                                  # no -k: every value
```

| Option | Meaning |
| --- | --- |
| `-k, -e, --key PATTERN` | Path to extract; repeat for several. Results follow the order given. |
| (default matching) | Shell-style: `*`/`?` stay within one level, `**` crosses levels, `[abc]` sets. |
| `--regex` | Pattern is a regular expression searched in the full path. |
| `--fixed` | Pattern is an exact path, no wildcards. |
| `-i, --ignore-case` | Case-insensitive keys. |
| `--sep STR` | Level separator in paths (default `.`); use it if your keys contain dots. |
| `--values-only` | Print just values, one per line. |
| `--output-csv FILE` | Also write a CSV: `file` plus one column per matched path (blank where a file lacks it). |
| `--long` | With `--output-csv`: columns `file,key,value`, one row per match. |

Output lines are `key<TAB>value`, prefixed with the file name when there are several files. A section (dict or list) is printed as compact JSON only when nothing inside it matched. Timestamps are kept as written. A pattern with no match in a file prints a warning and gives exit code 1; unreadable files are reported and skipped. Only keys are matched, not values.

## Tests

```bash
python run_tests.py              # all tools, with a per-tool summary (exit code 1 on failure)
python run_tests.py cs_count -v  # one tool, verbose
cryosparc-io-tools-test          # same, when installed (use `uv run` or the venv)
```

Tests live in `tests/` (`test_workflow_repeat.py`, `test_cs_count.py`, `test_csg_grep.py`) and use temporary directories.

## Layout

```text
cryosparc_workflow_repeat.py  cryosparc_cs_count.py  cryosparc_csg_grep.py  run_tests.py   # run-from-checkout wrappers
src/cryosparc_io_tools/       # implementations, _common.py (wildcards, CSV), run_tests.py, templates/
tests/
```
