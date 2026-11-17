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
| `--exclude-remainder-jobs` | Drop jobs not listed in `--jobs`. Otherwise keep them once, after generated jobs. Requires `--jobs`. |
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

# Select a two-job series; preserve all other input jobs once
python cryosparc_workflow_repeat.py full_workflow.json -n 5 --jobs J2 J3

# Repeat only J2 and J3 and discard the other jobs
python cryosparc_workflow_repeat.py full_workflow.json -n 5 \
  --jobs J2 J3 --exclude-remainder-jobs
```

A **simple series** is detected only when at least two selected jobs have
identical job definitions except for matching numbered parent-output indices,
and each job uses exactly one distinct index. The jobs may appear in any
order. The generated series is always sorted and renumbered from index 1.
If this is not true (for example, job parameters differ or jobs depend on each
other), the script conservatively falls back to block repetition.

For more general inputs:

- Outside series mode, each selected job is repeated N times (N **copies per job**).
- With `--jobs`, other jobs are retained once by default, with their internal
  job-ID references adjusted where possible. `--exclude-remainder-jobs` omits
  those jobs; check dependent job connections if you choose to exclude them.
- Indexed parent output names must have a trailing underscore-number suffix,
  such as `imported_mask_7`. The parent declaration must contain at least one
  corresponding numbered name so the script can recognize that output family.
- If the starting jobs use K distinct indexed positions, each repetition uses
  the next K positions; source positions are normalized to 1..K. For example,
  a workflow with inputs `_3`, `_2`, `_1` expands into 3N jobs using 1..3N.
- Jobs are ordered by their original indexed input position (ascending) within
  each copy. Multiple independent jobs at the same index share that index.
- References from a job to another job **within** the input block are remapped
  to that copy's new job IDs. External parent inputs are preserved.
- Each parent output family is rebuilt from the generated job references.
  Unindexed parent connections are retained.

The script changes workflow JSON only; it does not inspect `.mrc` files or
verify the named parent outputs exist in a CryoSPARC project. The destination
workflow must have compatible parent jobs/outputs. Inspect the exported JSON
before importing it into CryoSPARC.

## Tests

```bash
python -m unittest discover -s tests -v
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
