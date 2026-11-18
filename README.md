# cryosparc-io-tools

Small **standard-library-only** tools for editing CryoSPARC files. The
first tool, `cryosparc_workflow_repeat.py`, repeats workflow jobs and updates
indexed parent input connections.

## Quick start

Python **3.10+** is required. No third-party runtime dependencies or CryoSPARC
installation are needed.

From the repository root, without installing:

```bash
# Export the bundled example unchanged so you can edit it
python cryosparc_workflow_repeat.py

# Generate 12 Class 3D jobs from the bundled single-job template
python cryosparc_workflow_repeat.py --repeats 12 --output class_3d_12.json

# Use your own workflow and change the number of 3D classes in every job
python cryosparc_workflow_repeat.py my_workflow.json -n 12 \
  --set class3D_N_K=5 -o edited_workflow.json
```

Or install it as a package (optional):

```bash
python -m pip install .
cryosparc-workflow-repeat -n 12 -o class_3d_12.json
```

or install with uv:

```bash
uv tool install -e .
```

## CLI options

| Option | Meaning |
| --- | --- |
| `input` (optional) | Existing workflow JSON; default is the bundled `class_3d_template.json`. |
| `-n, --repeats N` | For a recognized uniform series, generate **N final series jobs**. Otherwise repeat each selected job N times. |
| `--no-series-extension` | Always use the original N-copies-per-selected-job behavior (2 selected jobs with `-n 5` gives 10 repeated jobs). |
| `--jobs J1 J2` | Repeat only these job IDs (also accepts `--jobs J1,J2`); can be specified more than once. Default: all jobs. |
| `--exclude-remainder-jobs` | Exclude unselected jobs (the default). Mutually exclusive with `--include-remainder-jobs`. |
| `--include-remainder-jobs` | Keep jobs not listed in `--jobs` once, after generated jobs. Mutually exclusive with `--exclude-remainder-jobs`. |
| `--parents P1 P2` | Advance numbered input connections only for the named parents; leave connections from other parents fixed. Accepts `--parents P1,P2` too. Default: **all parents**. |
| `-o, --output FILE` | Output path; defaults to `workflow.json` (no input/repeats), or `<input-stem>_repeated_N[_PARAM_VALUE].json` with input and repeats. `--set` overrides are included in the default filename. |
| `--set PARAM=VALUE` | Set the JSON `value` of matching parameters in all output jobs; repeat as needed. |
| `--set J1:PARAM=VALUE` | Override a parameter on a specific **output** job only. |
| `--no-id-update` | Preserve the source `_id`. |
| `--no-date-update` | Preserve `createdAt`. |
| `--no-title-update` | Preserve the source title. |

Without `--repeats`, the input JSON (or bundled example) is written unchanged,
except for any requested `--set` overrides. This makes it easy to export a
starter template and edit it manually. Input files are never overwritten.

Parameter values are parsed as JSON when possible: `--set class3D_N_K=5`,
`--set class3D_target_res=12.5`, `--set some_flag=true`, or
`--set some_text='"hello"'`. If the text is not valid JSON, it is stored as a
plain string. Unknown parameter names produce an error rather than silently
creating a nonstandard CryoSPARC parameter.

## How repetitions and connections work

The bundled template has one `class_3D` job, whose particle input is
`P1.particles` and whose mask inputs are `P2.imported_mask_1` and
`P3.imported_mask_1`. Its parent declarations contain `imported_mask_1`.

With `-n 12`, the script creates jobs `J1`–`J12` in **ascending** index order:

| Job | Particle input | Mask input | Focus mask input |
| --- | --- | --- | --- |
| J1 | P1.particles | P2.imported_mask_1 | P3.imported_mask_1 |
| J2 | P1.particles | P2.imported_mask_2 | P3.imported_mask_2 |
| ... | ... | ... | ... |
| J12 | P1.particles | P2.imported_mask_12 | P3.imported_mask_12 |

The parent `connections` lists for P2 and P3 are rebuilt in ascending
numerical order (`imported_mask_1`, `imported_mask_2`, ..., `imported_mask_12`).
Unindexed inputs such as `P1.particles` remain untouched.

For a workflow containing two otherwise identical `class_3D` jobs using mask
indices 1 and 2, the default `-n 5` **extends the series to 5 total jobs**
(indices 1 through 5). This is intentionally different from the original
behavior, which can be requested explicitly with `--no-series-extension`:

```bash
# Two equivalent jobs -> five final classification jobs
python cryosparc_workflow_repeat.py two_jobs.json -n 5

# Two equivalent jobs -> ten final classification jobs
python cryosparc_workflow_repeat.py two_jobs.json -n 5 --no-series-extension

# Select a two-job series; other jobs are excluded by default
python cryosparc_workflow_repeat.py full_workflow.json -n 5 --jobs J2 J3

# Select a two-job series; retain all other input jobs once
python cryosparc_workflow_repeat.py full_workflow.json -n 5 \
  --jobs J2 J3 --include-remainder-jobs

# Only advance numbered inputs from P2; leave P3 inputs fixed
python cryosparc_workflow_repeat.py full_workflow.json -n 5 \
  --jobs J2 J3 --parents P1 P2
```

A **simple series** is detected only when at least two selected jobs have
identical job definitions except for numbered parent-output indices, and
the **varying** numbered inputs agree on the order of the jobs. Numbered
inputs that are **constant** across the selected source jobs are pinned
and stay unchanged. For example, `particles_class_0/1` and
`volume_class_0/1` advance together while every job retains the same
`imported_mask_1` solvent mask. **Input job order does not
matter:** if `J1` uses `imported_mask_1` and `J2` uses `imported_mask_0`,
`-n 5` still produces `J1` through `J5` connected to masks `_0` through
`_4`, in ascending order. Likewise, a reversed `_2`, `_1` series generates
`_1` through `_5`. A zero-based input series (containing index `_0`) stays
zero-based, so `-n 5` generates `_0` through `_4`. Other inputs are normalized to `_1`
through `_5`. If this is not true (for example, job parameters differ or jobs depend on each
other), the script conservatively falls back to block repetition.

**Slots advance from their own start index.** Each advancing numbered input
slot counts up from its own first index: `0` if the slot uses
index 0 anywhere in the series, otherwise `1`. A series of
`particles_class_0/1` with `imported_mask_1/2` therefore becomes classes `_0`..
with masks `_1`.. rather than being rejected or strided. Slots only need to
agree on the job *order*, not on the numbers.

**Selecting a single job of a series.** Pinning is judged from the *selected*
jobs. With two or more selected series jobs, an input that is the same in all
of them (for example one shared mask) is pinned. With a single job, e.g.
`--jobs J2`, there is nothing to compare, so every numbered input of the
selected parents advances, including that mask. The job's position and start
index in the series still come from all identical jobs in the workflow, so the
class series of `--jobs J2` matches the full run (including a zero-based series
staying zero-based). A lone job with no identical sibling advances each numbered
input from its own start index. Use `--parents` to name only the parents that
should advance and pin the rest (`--parents P1` keeps a P2 mask fixed either way).

For more general inputs:

- Outside series mode, each selected job is repeated N times (N **copies per job**).
  With `--no-series-extension`, a recognized series still advances its
  varying inputs from their own start indices (one series position per copy);
  shared numbered inputs remain fixed.
- With `--jobs`, other jobs are **excluded by default**. Use
  `--include-remainder-jobs` to retain them once, with internal job-ID
  references adjusted where possible. Check dependent job connections if you
  exclude jobs that other jobs depend on.
- Indexed parent output names must have a trailing underscore-number suffix,
  such as `imported_mask_7`. The parent declaration must contain at least one
  corresponding numbered name so the script can recognize that output family.
- If the starting jobs use K distinct indexed positions, each repetition uses
  the next K positions. Source positions are normalized to 0..K-1 if any
  selected source index is `_0`, otherwise to 1..K. For example, a workflow
  with inputs `_3`, `_2`, `_1` expands into 3N jobs using 1..3N; inputs
  `_0`, `_1` expand to 0..2N-1.
- Jobs are ordered by their original indexed input position (ascending) within
  each copy. Multiple independent jobs at the same index share that index.
- References from a job to another job **within** the input block are remapped
  to that copy's new job IDs. External parent inputs are preserved.
- Each parent output family is rebuilt from the generated job references.
  Unindexed parent connections are retained.
- `--parents` filters **which parent outputs are reindexed**, not which jobs
  are selected. For example, `--parents P1 P2` advances the numbered outputs
  of P2 while keeping all P3 connections unchanged. In series-extension mode,
  unselected parents use the connection from the **lowest-index job**,
  regardless of its position in the input JSON. Different numeric suffixes
  on unselected parents do not prevent detection of a series. In block mode,
  unselected parents keep each source job's original
  connections. Parent `connections` declarations are rebuilt to match the
  resulting jobs. A parent with no numbered outputs (such as P1.particles)
  may be included alongside another parent, but selecting only parents with
  no numbered outputs is an error.


The script changes workflow JSON only; it does not inspect `.mrc` files or
verify the named parent outputs exist in a CryoSPARC project. The destination
workflow must have compatible parent jobs/outputs. Inspect the exported JSON
before importing it into CryoSPARC.

## Tests

Tests use temporary directories for generated workflow files. The `-B`
flag prevents Python from creating `__pycache__` in the repository.

```bash
python -B -m unittest discover -s tests -v
```

## Repository layout

```text
cryosparc-io-tools/
  cryosparc_workflow_repeat.py         # convenient direct-run wrapper
  pyproject.toml
  README.md
  src/cryosparc_io_tools/
    __init__.py
    cryosparc_workflow_repeat.py       # implementation and console entry point
    templates/class_3d_template.json  # bundled example workflow
  tests/test_workflow_repeat.py
```

## Output names and metadata

Without an input or repeat count, `python cryosparc_workflow_repeat.py` writes
`workflow.json` in the current working directory. With an input file but no
repeat count, it writes `<input-stem>.json` in the current working directory
(and refuses to overwrite the original input file). With repetitions, the
default output is `<input-stem>_repeated_<N>.json` or
`workflow_repeated_<N>.json` when using the bundled template (with `_PARAM_VALUE` suffixes for any `--set` options). Use `-o` to
choose another name.

When `-n` is supplied, new workflow metadata is generated by default:
`_id` becomes a fresh 24-character hexadecimal identifier, `createdAt`
becomes the current UTC timestamp, and `title` describes the job type(s),
requested repeat count and any `--set` overrides. Disable each independently
with `--no-id-update`, `--no-date-update`, and `--no-title-update`.
Without `-n`, metadata is preserved to make the exported template easy to edit.

The bundled template has category `Workflow Repeat` and explains the required
particle, solvent-mask and focus-mask parent inputs in its description.

### Default filenames with parameter overrides

For example, `-n 12 --set class3D_N_K=5` creates `workflow_repeated_12_class3D_N_K_5.json` when using the bundled template. Multiple `--set` values are appended in the order supplied; filename-unsafe characters are replaced with underscores. An explicit `-o` always takes precedence.
