"""Repeat CryoSPARC workflow jobs and reconcile parent connections.

No third-party dependencies are required.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import secrets
import sys
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any

INDEXED_NAME = re.compile(r"^(?P<stem>.+_)(?P<index>[0-9]+)$")
JOB_NAME = re.compile(r"^J([0-9]+)$")


def template() -> dict[str, Any]:
    """Return a fresh copy of the bundled, editable example workflow."""
    location = resources.files("cryosparc_io_tools").joinpath("templates/class_3d_template.json")
    return json.loads(location.read_text(encoding="utf-8"))


def _as_numbered_connection(source: str, parents: dict[str, Any]) -> tuple[str, str, int] | None:
    """Identify a numbered parent output, e.g. P2.imported_mask_12."""
    if not isinstance(source, str) or "." not in source:
        return None
    parent_id, output = source.split(".", 1)
    if parent_id not in parents:
        return None
    match = INDEXED_NAME.fullmatch(output)
    if match is None:
        return None
    # Restrict automatic reindexing to names belonging to actual parent groups.
    declared = [
        item
        for group in parents[parent_id].get("groups", {}).values()
        for item in group.get("connections", [])
    ]
    if not any(
        isinstance(item, str) and (m := INDEXED_NAME.fullmatch(item))
        and m.group("stem") == match.group("stem")
        for item in declared
    ):
        return None
    return parent_id, match.group("stem"), int(match.group("index"))


def _job_indices(job: dict[str, Any], parents: dict[str, Any]) -> set[int]:
    indices = set()
    for pair in job.get("groups", []):
        if isinstance(pair, list) and len(pair) >= 1:
            match = _as_numbered_connection(pair[0], parents)
            if match is not None:
                indices.add(match[2])
    return indices


def _series_order(jobs: dict[str, Any], parents: dict[str, Any]) -> list[str] | None:
    """Return ascending job IDs only for a uniform, numbered one-job-per-index series.

    Require exactly the same job structure, settings and parent families after
    stripping numeric suffixes. Multiple distinct indices within a job, shared
    indices between jobs, and internal job references prevent extension.
    """
    if len(jobs) < 2:
        return None
    canonical = None
    ordered = []
    for job_id, original in jobs.items():
        indices = _job_indices(original, parents)
        if len(indices) != 1:
            return None
        index = next(iter(indices))
        job = copy.deepcopy(original)
        saw_numbered = False
        for pair in job.get("groups", []):
            if not isinstance(pair, list) or not pair or not isinstance(pair[0], str):
                continue
            source = pair[0]
            numbered = _as_numbered_connection(source, parents)
            if numbered:
                if numbered[2] != index:
                    return None
                pair[0] = f"{numbered[0]}.{numbered[1]}#"
                saw_numbered = True
            elif source.split(".", 1)[0] in jobs:
                return None
        if not saw_numbered or (canonical is not None and job != canonical):
            return None
        canonical = job
        ordered.append((index, int(JOB_NAME.fullmatch(job_id).group(1)), job_id))
    if len({x[0] for x in ordered}) != len(ordered):
        return None
    return [entry[2] for entry in sorted(ordered)]


def _reconcile_parents(jobs: dict[str, Any], parents: dict[str, Any]) -> None:
    """Rebuild indexed parent declarations from all jobs, ascending numerically."""
    used: dict[str, set[str]] = {}
    for job in jobs.values():
        for pair in job.get("groups", []):
            if isinstance(pair, list) and pair and isinstance(pair[0], str) and "." in pair[0]:
                parent, output = pair[0].split(".", 1)
                if parent in parents:
                    used.setdefault(parent, set()).add(output)

    def numeric_key(name: str) -> tuple[str, int, str]:
        match = INDEXED_NAME.fullmatch(name)
        return (match.group("stem"), int(match.group("index")), "") if match else (name, -1, name)

    for parent_id, parent in parents.items():
        for group in parent.get("groups", {}).values():
            current = group.get("connections", [])
            if not isinstance(current, list):
                continue
            indexed_stems = {
                match.group("stem") for entry in current
                if isinstance(entry, str) and (match := INDEXED_NAME.fullmatch(entry))
            }
            kept = [entry for entry in current
                    if not (isinstance(entry, str) and (match := INDEXED_NAME.fullmatch(entry))
                            and match.group("stem") in indexed_stems)]
            added = [output for output in used.get(parent_id, set())
                     if (match := INDEXED_NAME.fullmatch(output)) and match.group("stem") in indexed_stems]
            group["connections"] = list(dict.fromkeys(kept + sorted(added, key=numeric_key)))


def repeat_workflow(workflow: dict[str, Any], repeats: int, *,
                    jobs: list[str] | None = None,
                    no_series_extension: bool = False,
                    exclude_remainder_jobs: bool = False) -> dict[str, Any]:
    """Extend uniform series to N jobs, otherwise repeat selected jobs N times.

    The default series extension requires >=2 identical jobs varying only by
    their numbered parent outputs; N then means the final series size.
    Otherwise N means copies of each selected job. Unselected jobs are retained
    once unless exclude_remainder_jobs is set.
    """
    if repeats < 1:
        raise ValueError("--repeats must be a positive integer")
    result = copy.deepcopy(workflow)
    original_jobs = workflow.get("jobs")
    parents = result.get("parents", {})
    if not isinstance(original_jobs, dict) or not original_jobs:
        raise ValueError("Input JSON must have a non-empty 'jobs' object")
    if not isinstance(parents, dict):
        raise ValueError("Input JSON 'parents' must be an object")
    original_names = list(original_jobs)
    if any(JOB_NAME.fullmatch(name) is None for name in original_names):
        raise ValueError("Job IDs must be in the form J1, J2, ...")
    selected_names = list(dict.fromkeys(jobs)) if jobs is not None else original_names
    if not selected_names:
        raise ValueError("--jobs must specify at least one job")
    unknown = sorted(set(selected_names) - set(original_names))
    if unknown:
        raise ValueError("Unknown job ID(s): " + ", ".join(unknown))
    selected = {name: original_jobs[name] for name in selected_names}
    series = None if no_series_extension else _series_order(selected, parents)
    indices = sorted({index for job in selected.values() for index in _job_indices(job, parents)})
    index_position = {value: position for position, value in enumerate(indices)}
    width = len(indices)

    def rank(name: str) -> tuple[float, int]:
        values = sorted(_job_indices(original_jobs[name], parents))
        return (values[0] if values else float("inf"), int(JOB_NAME.fullmatch(name).group(1)))

    sorted_names = sorted(selected_names, key=rank)
    generated: dict[str, Any] = {}
    first_id_map: dict[str, str] = {}
    remainder_names = [name for name in original_names if name not in selected and not exclude_remainder_jobs]
    # Generate selected first, then unselected jobs. No original job IDs are reused.
    count = repeats if series is not None else repeats * len(sorted_names)
    remainder_map = {name: f"J{count + i + 1}" for i, name in enumerate(remainder_names)}

    def rewrite(job: dict[str, Any], index_map: dict[int, int], id_map: dict[str, str]) -> dict[str, Any]:
        rewritten = copy.deepcopy(job)
        for pair in rewritten.get("groups", []):
            if not isinstance(pair, list) or not pair or not isinstance(pair[0], str):
                continue
            source = pair[0]
            matched = _as_numbered_connection(source, parents)
            if matched is not None and matched[2] in index_map:
                parent, stem, original_index = matched
                pair[0] = f"{parent}.{stem}{index_map[original_index]}"
            elif "." in source:
                provider, output = source.split(".", 1)
                if provider in id_map:
                    pair[0] = f"{id_map[provider]}.{output}"
        return rewritten

    if series is not None:
        # All templates are identical except numbered parent inputs. Use the
        # first job as the prototype and extend the series to N total jobs.
        base_name = series[0]
        base_index = next(iter(_job_indices(selected[base_name], parents)))
        for i in range(repeats):
            generated[f"J{i + 1}"] = rewrite(selected[base_name], {base_index: i + 1}, remainder_map)
        # Remainder references to original series jobs map to corresponding
        # indices when present, or to the final generated job if N is smaller.
        for i, name in enumerate(series):
            first_id_map[name] = f"J{min(i + 1, repeats)}"
    else:
        for repeat in range(repeats):
            start = repeat * len(sorted_names) + 1
            id_map = {old: f"J{start + offset}" for offset, old in enumerate(sorted_names)}
            if repeat == 0:
                first_id_map.update(id_map)
            combined_map = {**remainder_map, **id_map}
            indices_for_copy = {old: repeat * width + position + 1
                                for old, position in index_position.items()}
            for old in sorted_names:
                generated[id_map[old]] = rewrite(selected[old], indices_for_copy, combined_map)

    if remainder_names:
        all_maps = {**first_id_map, **remainder_map}
        for old in remainder_names:
            generated[remainder_map[old]] = rewrite(original_jobs[old], {}, all_maps)
    result["jobs"] = generated
    _reconcile_parents(generated, parents)
    return result

def set_parameters(workflow: dict[str, Any], overrides: list[str]) -> None:
    """Apply parameter.value updates to each matching job (or a selected job).

    Syntax: PARAM=JSON_VALUE or JOB_ID:PARAM=JSON_VALUE.
    Values are parsed as JSON first; otherwise treated as literal strings.
    """
    for override in overrides:
        if "=" not in override:
            raise ValueError(f"Invalid --set {override!r}: expected PARAM=VALUE")
        selector, raw = override.split("=", 1)
        job_id, sep, param = selector.partition(":")
        if not sep:
            job_id, param = "", job_id
        if not param:
            raise ValueError(f"Invalid --set {override!r}: parameter is missing")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        matches = 0
        for current_id, job in workflow.get("jobs", {}).items():
            if job_id and current_id != job_id:
                continue
            record = job.get("parameters", {}).get(param)
            if isinstance(record, dict) and "value" in record:
                record["value"] = value
                matches += 1
        if not matches:
            raise ValueError(f"Parameter {param!r} not found in " + (job_id or "any job"))


def update_metadata(workflow: dict[str, Any], repeats: int, overrides: list[str], *,
                    update_id: bool = True, update_date: bool = True,
                    update_title: bool = True) -> None:
    """Refresh top-level metadata for a newly generated workflow."""
    if update_id:
        # CryoSPARC workflow IDs are 24-character hexadecimal identifiers.
        workflow["_id"] = secrets.token_hex(12)
    if update_date:
        workflow["createdAt"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if update_title:
        kinds = sorted({job.get("jobType", "unknown") for job in workflow["jobs"].values()})
        kind_label = ", ".join(kinds)
        edits = f" {' '.join(overrides)}" if overrides else ""
        workflow["title"] = f"{kind_label} repeated {repeats} times{edits}"


def filename_override_suffix(overrides: list[str]) -> str:
    """Create filesystem-safe PARAM_VALUE components for --set overrides."""
    parts = []
    for override in overrides:
        selector, value = override.split("=", 1)
        # Include a job selector, if supplied, to avoid ambiguous names.
        label = f"{selector}_{value}"
        label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("._-")
        parts.append(label or "set")
    return "_" + "_".join(parts) if parts else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, help="Input workflow JSON (default: bundled template)")
    parser.add_argument("-n", "--repeats", type=int, help="Final job count for a detected series, otherwise copies of selected jobs")
    parser.add_argument("--jobs", nargs="+", action="append", metavar="JID",
                        help="Select jobs to repeat, e.g. --jobs J1 J2 or --jobs J1,J2 (default: all)")
    parser.add_argument("--exclude-remainder-jobs", action="store_true",
                        help="Discard jobs not selected with --jobs (default: retain each once)")
    parser.add_argument("--no-series-extension", action="store_true",
                        help="Disable series detection: always make N copies of each selected job")
    parser.add_argument("-o", "--output", type=Path, help="Output JSON path (default: workflow.json or INPUT_STEM_repeated_N[_PARAM_VALUE].json)")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="PARAM=VALUE",
                        help="Override job parameter value, optionally J1:PARAM=VALUE; repeatable")
    parser.add_argument("--no-id-update", action="store_true", help="Preserve the input workflow _id")
    parser.add_argument("--no-date-update", dest="no_date_update", action="store_true",
                        help="Preserve createdAt")
    parser.add_argument("--no-title-update", action="store_true", help="Preserve the input workflow title")
    args = parser.parse_args(argv)
    try:
        workflow = json.loads(args.input.read_text(encoding="utf-8")) if args.input else template()
        if not isinstance(workflow, dict):
            raise ValueError("Top-level JSON must be an object")
        if args.repeats is not None:
            selected_jobs = ([item.strip() for group in args.jobs for token in group
                              for item in token.split(",") if item.strip()]
                             if args.jobs else None)
            workflow = repeat_workflow(workflow, args.repeats, jobs=selected_jobs,
                                       no_series_extension=args.no_series_extension,
                                       exclude_remainder_jobs=args.exclude_remainder_jobs)
        if args.repeats is None and (args.jobs or args.exclude_remainder_jobs or args.no_series_extension):
            raise ValueError("--jobs, --exclude-remainder-jobs and --no-series-extension require --repeats")
        if args.exclude_remainder_jobs and not args.jobs:
            raise ValueError("--exclude-remainder-jobs requires --jobs")
        set_parameters(workflow, args.overrides)
        if args.repeats is not None:
            update_metadata(workflow, args.repeats, args.overrides,
                            update_id=not args.no_id_update,
                            update_date=not args.no_date_update,
                            update_title=not args.no_title_update)
        stem = args.input.stem if args.input else "workflow"
        suffix = filename_override_suffix(args.overrides)
        default_name = (f"{stem}_repeated_{args.repeats}{suffix}.json"
                        if args.repeats is not None else f"{stem}{suffix}.json")
        output = args.output or Path(default_name)
        if args.input is not None and output.resolve() == args.input.resolve():
            raise ValueError("Output would overwrite the input file; select a different --output")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {output} ({len(workflow.get('jobs', {}))} jobs)")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
