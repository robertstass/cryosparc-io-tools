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


def _job_indices(job: dict[str, Any], parents: dict[str, Any],
                 selected_parents: set[str] | None = None,
                 positions: set[int] | None = None) -> set[int]:
    """Numbered indices used by a job, optionally only at the given input slots."""
    indices = set()
    for position, pair in enumerate(job.get("groups", [])):
        if positions is not None and position not in positions:
            continue
        if isinstance(pair, list) and len(pair) >= 1:
            match = _as_numbered_connection(pair[0], parents)
            if match is not None and (selected_parents is None or match[0] in selected_parents):
                indices.add(match[2])
    return indices


def _group_steps(slots: dict[str, dict[int, int]]
                 ) -> tuple[set[int], set[int], dict[int, int], dict[str, int]] | None:
    """Analyse identical jobs given their numbered inputs as {job: {slot: index}}.

    Each numbered slot counts up from its **own** start index: 0 if the slot
    uses index 0 in any job, otherwise 1. So ``particles_class_0/1`` can
    advance together with ``imported_mask_1/2`` (classes 0.., masks 1..).

    Slots whose index differs between jobs (*varying*) fix the job order by
    their rank, so all of them must give each job the same rank (offsets
    between slots are fine, e.g. class_0 with mask_1). Slots that are the same
    in every job are *constant*. Returns (all slots, varying slots, start
    index per slot, step per job), or None if the jobs do not form such a
    series.
    """
    names = list(slots)
    positions = set().union(*(slots[name] for name in names)) if names else set()
    if any(position not in slots[name] for name in names for position in positions):
        return None
    varying = {position for position in positions
               if len({slots[name][position] for name in names}) > 1}
    if not varying:
        return None
    steps: dict[str, list[int]] = {name: [] for name in names}
    starts: dict[int, int] = {}
    for position in sorted(positions):
        values = [slots[name][position] for name in names]
        starts[position] = 0 if 0 in values else 1
        if position not in varying:
            continue
        if len(set(values)) != len(values):
            return None
        ordered = sorted(values)
        for name in names:
            steps[name].append(ordered.index(slots[name][position]))
    if any(len(set(ranks)) != 1 for ranks in steps.values()):
        return None
    return positions, varying, starts, {name: ranks[0] for name, ranks in steps.items()}


def _structure(job: dict[str, Any], parents: dict[str, Any],
               selected_parents: set[str] | None) -> tuple[str, dict[int, int]]:
    """Job signature with numbered indices masked out, plus its {slot: index}."""
    job = copy.deepcopy(job)
    found: dict[int, int] = {}
    for position, pair in enumerate(job.get("groups", [])):
        if not isinstance(pair, list) or not pair or not isinstance(pair[0], str):
            continue
        numbered = _as_numbered_connection(pair[0], parents)
        if numbered:
            if selected_parents is None or numbered[0] in selected_parents:
                found[position] = numbered[2]
            pair[0] = f"{numbered[0]}.{numbered[1]}#"
    return json.dumps(job, sort_keys=True), found


def _series_order(jobs: dict[str, Any], parents: dict[str, Any],
                  selected_parents: set[str] | None = None
                  ) -> tuple[list[str], set[int], dict[int, int]] | None:
    """Find a uniform series among the selected jobs.

    The jobs must be identical apart from numbered inputs, must not depend on
    each other, and must form a series as described in :func:`_group_steps`.
    Returns ascending source job IDs, the slots that advance and each slot's
    start index. Slots that are constant across the selected jobs are pinned. Slots that are constant across the selected jobs are pinned.
    """
    if len(jobs) < 2:
        return None
    signatures = set()
    slots: dict[str, dict[int, int]] = {}
    for name, job in jobs.items():
        if any(isinstance(pair, list) and pair and isinstance(pair[0], str)
               and not _as_numbered_connection(pair[0], parents)
               and pair[0].split(".", 1)[0] in jobs for pair in job.get("groups", [])):
            return None
        signature, slots[name] = _structure(job, parents, selected_parents)
        signatures.add(signature)
    if len(signatures) != 1:
        return None
    analysed = _group_steps(slots)
    if analysed is None:
        return None
    _, varying, starts, steps = analysed
    return sorted(jobs, key=lambda n: (steps[n], int(JOB_NAME.fullmatch(n).group(1)))), varying, starts


def _varying_slots(jobs: dict[str, Any], parents: dict[str, Any],
                   selected_parents: set[str] | None = None,
                   selected: set[str] | None = None
                   ) -> dict[str, tuple[set[int], dict[int, int], int, str]]:
    """Per selected job: (advancing slots, start per slot, step, group id).

    Jobs are grouped across the *whole* workflow by their definition with
    numbered indices masked out. A group of two or more that forms a series
    (see :func:`_group_steps`) fixes each member's step and per-slot start, so
    ``--jobs J2`` numbers its series like the full run does.

    Which slots advance depends on the *selected* members of the group: with
    two or more selected, a slot that is constant across them is pinned; with
    just one selected there is no such evidence and every numbered slot
    advances. Jobs with no identical sibling, or whose group is not a
    consistent series, have no entry.
    """
    groups: dict[str, list[str]] = {}
    slots: dict[str, dict[int, int]] = {}
    for name, original in jobs.items():
        signature, slots[name] = _structure(original, parents, selected_parents)
        groups.setdefault(signature, []).append(name)
    result: dict[str, tuple[set[int], dict[int, int], int, str]] = {}
    for group_id, members in enumerate(groups.values()):
        chosen = [name for name in members if selected is None or name in selected]
        if len(members) < 2 or not chosen:
            continue
        analysed = _group_steps({name: slots[name] for name in members})
        if analysed is None:
            continue
        positions, _, starts, steps = analysed
        advancing = positions
        if len(chosen) >= 2:
            advancing = {position for position in positions
                         if len({slots[name][position] for name in chosen}) > 1} or positions
        for name in chosen:
            result[name] = (advancing, starts, steps[name], f"g{group_id}")
    return result


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
                    exclude_remainder_jobs: bool = True,
                    selected_parents: list[str] | None = None) -> dict[str, Any]:
    """Extend uniform series to N jobs, otherwise repeat selected jobs N times.

    The default series extension requires >=2 identical jobs varying only by
    their numbered parent outputs; N then means the final series size.
    Otherwise N means copies of each selected job. Unselected jobs are excluded
    by default. If selected_parents is supplied, only indexed connections from
    those parents advance; all other parent inputs remain as in the source job
    (or the first series job when extending a series).
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
    parent_filter = set(selected_parents) if selected_parents is not None else None
    if parent_filter is not None:
        if not parent_filter:
            raise ValueError("--parents must specify at least one parent")
        unknown_parents = sorted(parent_filter - set(parents))
        if unknown_parents:
            raise ValueError("Unknown parent ID(s): " + ", ".join(unknown_parents))
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
    detected_series = _series_order(selected, parents, parent_filter)
    series = None if no_series_extension else detected_series
    # Per job: (slots that advance, start index per slot, step, group). A
    # recognised series supplies these directly; otherwise identical sibling
    # jobs anywhere in the workflow (even unselected ones) are used, so
    # constant numbered inputs such as one shared mask stay fixed. No evidence
    # means every numbered input of the selected parents advances.
    if detected_series is not None:
        series_names, varying_positions, slot_starts = detected_series
        info = {name: (varying_positions, slot_starts, step, "series")
                for step, name in enumerate(series_names)}
    else:
        info = _varying_slots(original_jobs, parents, parent_filter, set(selected_names))
        if len(selected_names) == 1 and selected_names[0] not in info:
            # One job and no identical sibling: each numbered input advances
            # from its own start index.
            solo = _structure(selected[selected_names[0]], parents, parent_filter)[1]
            if solo:
                info[selected_names[0]] = (set(solo), {position: 0 if value == 0 else 1
                                                       for position, value in solo.items()}, 0, "solo")

    def active_for(name: str) -> set[int] | None:
        return info[name][0] if name in info else None

    # "Step mode": every selected job has a known step in a known series.
    step_mode = all(name in info and info[name][2] is not None for name in selected_names)
    if step_mode:
        steps_in_group: dict[str, list[int]] = {}
        for name in selected_names:
            steps_in_group.setdefault(info[name][3], []).append(info[name][2])
        step_rank = {gid: {step: rank for rank, step in enumerate(sorted(set(values)))}
                     for gid, values in steps_in_group.items()}
        group_width = {gid: len(ranks) for gid, ranks in step_rank.items()}
        indices: list[int] = []
    else:
        indices = sorted({index for name, job in selected.items()
                          for index in _job_indices(job, parents, parent_filter, active_for(name))})
        if parent_filter is not None and not indices:
            raise ValueError("No numbered parent outputs from --parents are used by the selected jobs")
    index_position = {value: position for position, value in enumerate(indices)}
    width = len(indices)
    # Value mode: zero-based if index 0 is used (or the siblings use it).
    start_index = 0 if 0 in indices else 1

    def rank(name: str) -> tuple[float, int]:
        # Sort by the parents actually being advanced, not by unrelated
        # numbered connections or by the source JSON/job-ID order.
        number = int(JOB_NAME.fullmatch(name).group(1))
        if step_mode:
            return (step_rank[info[name][3]][info[name][2]], number)
        values = sorted(_job_indices(original_jobs[name], parents, parent_filter, active_for(name)))
        return (values[0] if values else float("inf"), number)

    sorted_names = sorted(selected_names, key=rank)
    generated: dict[str, Any] = {}
    first_id_map: dict[str, str] = {}
    remainder_names = [name for name in original_names if name not in selected and not exclude_remainder_jobs]
    # Generate selected first, then unselected jobs. No original job IDs are reused.
    count = repeats if series is not None else repeats * len(sorted_names)
    remainder_map = {name: f"J{count + i + 1}" for i, name in enumerate(remainder_names)}

    def rewrite(job: dict[str, Any], index_map: dict[int, int], id_map: dict[str, str],
                active_positions: set[int] | None = None,
                slot_targets: dict[int, int] | None = None) -> dict[str, Any]:
        """Rewrite connections; slot_targets ({slot: new index}) wins over index_map."""
        rewritten = copy.deepcopy(job)
        for position, pair in enumerate(rewritten.get("groups", [])):
            if not isinstance(pair, list) or not pair or not isinstance(pair[0], str):
                continue
            source = pair[0]
            matched = _as_numbered_connection(source, parents)
            if (matched is not None and (parent_filter is None or matched[0] in parent_filter)
                    and (active_positions is None or position in active_positions)
                    and (position in slot_targets if slot_targets is not None
                         else matched[2] in index_map)):
                parent, stem, original_index = matched
                new_index = slot_targets[position] if slot_targets is not None else index_map[original_index]
                pair[0] = f"{parent}.{stem}{new_index}"
            elif "." in source:
                provider, output = source.split(".", 1)
                if provider in id_map:
                    pair[0] = f"{id_map[provider]}.{output}"
        return rewritten

    if series is not None:
        # All templates are identical except numbered parent inputs. Use the
        # lowest-step job as the prototype and extend to N total jobs; each
        # advancing slot counts up from its own start index.
        base_name = series_names[0]
        for i in range(repeats):
            generated[f"J{i + 1}"] = rewrite(
                selected[base_name], {}, remainder_map, active_positions=varying_positions,
                slot_targets={position: slot_starts[position] + i for position in varying_positions}
            )
        # Remainder references to original series jobs map to corresponding
        # indices when present, or to the final generated job if N is smaller.
        for i, name in enumerate(series_names):
            first_id_map[name] = f"J{min(i + 1, repeats)}"
    else:
        for repeat in range(repeats):
            start = repeat * len(sorted_names) + 1
            id_map = {old: f"J{start + offset}" for offset, old in enumerate(sorted_names)}
            if repeat == 0:
                first_id_map.update(id_map)
            combined_map = {**remainder_map, **id_map}
            indices_for_copy = {old: repeat * width + position + start_index
                                for old, position in index_position.items()}
            for old in sorted_names:
                targets = None
                if step_mode:
                    slots_, starts_, step_, gid = info[old]
                    new_step = repeat * group_width[gid] + step_rank[gid][step_]
                    targets = {position: starts_[position] + new_step for position in slots_}
                generated[id_map[old]] = rewrite(
                    selected[old], indices_for_copy, combined_map,
                    active_positions=active_for(old), slot_targets=targets
                )

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
    remainder_options = parser.add_mutually_exclusive_group()
    remainder_options.add_argument("--include-remainder-jobs", dest="remainder_choice",
                                   action="store_const", const=False, default=None,
                                   help="Keep jobs not selected with --jobs once (default: exclude)")
    remainder_options.add_argument("--exclude-remainder-jobs", dest="remainder_choice",
                                   action="store_const", const=True, help="Discard unselected jobs (default)")
    parser.add_argument("--parents", nargs="+", action="append", metavar="PID",
                        help="Only advance numbered outputs of these parents, e.g. P1 P2; "
                             "other parents' inputs stay fixed (default: all parents)")
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
            parent_ids = ([item.strip() for group in args.parents for token in group
                           for item in token.split(",") if item.strip()]
                          if args.parents else None)
            workflow = repeat_workflow(workflow, args.repeats, jobs=selected_jobs,
                                       no_series_extension=args.no_series_extension,
                                       exclude_remainder_jobs=(True if args.remainder_choice is None
                                                               else args.remainder_choice),
                                       selected_parents=parent_ids)
        if args.repeats is None and (args.jobs or args.parents or args.no_series_extension
                                    or args.remainder_choice is not None):
            raise ValueError("--jobs, --parents, remainder options and --no-series-extension require --repeats")
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
