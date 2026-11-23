"""Repeat CryoSPARC workflow jobs and reconcile parent connections.

No third-party dependencies are required.
"""

from __future__ import annotations

import argparse
import copy
import itertools
import json
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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
                    selected_parents: list[str] | None = None,
                    report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extend uniform series to N jobs, otherwise repeat selected jobs N times.

    The default series extension requires >=2 identical jobs varying only by
    their numbered parent outputs; N then means the final series size.
    Otherwise N means copies of each selected job. Unselected jobs are excluded
    by default. If selected_parents is supplied, only indexed connections from
    those parents advance; all other parent inputs remain as in the source job
    (or the first series job when extending a series). An empty selected_parents
    list means *no* numbered connection advances (pure replication).

    If a dict is passed as ``report`` it is filled with ``origins`` (generated
    job ID -> set of source job IDs it came from) and ``replicated`` (the
    generated, non-remainder job IDs in order), which :func:`apply_sets` uses.
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
    if parent_filter:
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
        if parent_filter and not indices:
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
    origins: dict[str, set[str]] = {}
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
            # Series members are identical apart from their indices, so any of
            # them identifies the whole generated series.
            origins[f"J{i + 1}"] = set(series_names)
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
                origins[id_map[old]] = {old}

    replicated = list(generated)
    if remainder_names:
        all_maps = {**first_id_map, **remainder_map}
        for old in remainder_names:
            generated[remainder_map[old]] = rewrite(original_jobs[old], {}, all_maps)
            origins[remainder_map[old]] = {old}
    result["jobs"] = generated
    _reconcile_parents(generated, parents)
    if report is not None:
        report["origins"] = origins
        report["replicated"] = replicated
    return result


@dataclass(frozen=True)
class Delimiters:
    """Characters used to parse --set values; override them if a value needs them."""
    job: str = ":"      # JOB:PARAM=VALUE
    range: str = ":"    # start:stop[:step]
    list: str = ","     # a,b,c  (items may themselves be ranges)

    def validate(self) -> None:
        for label, value in (("job", self.job), ("range", self.range), ("list", self.list)):
            if not value or "=" in value:
                raise ValueError(f"--{label}-sep must be a non-empty string without '='")
        if self.range == self.list:
            raise ValueError("--range-sep and --list-sep must differ")


@dataclass(frozen=True)
class SetSpec:
    """One parsed --set. ``sweep`` is True for ranges and lists (an iteration axis)."""
    text: str
    job: str
    param: str
    values: tuple
    sweep: bool


MAX_AXIS_VALUES = 100_000
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _split_top_level(text: str, sep: str) -> list[str]:
    """Split on ``sep`` outside of JSON brackets, braces and double quotes."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quoted = escaped = False
    i = 0
    while i < len(text):
        ch = text[i]
        if quoted:
            quoted = not (ch == '"' and not escaped)
            escaped = ch == "\\" and not escaped
        elif ch == '"':
            quoted = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth = max(0, depth - 1)
        elif depth == 0 and text.startswith(sep, i):
            parts.append("".join(current))
            current = []
            i += len(sep)
            continue
        current.append(ch)
        i += 1
    parts.append("".join(current))
    return parts


def _number(text: str) -> tuple[Decimal, bool] | None:
    """Parse a plain number into (exact value, written-as-float), else None."""
    text = text.strip()
    if _NUMBER.fullmatch(text) is None:
        return None
    try:
        return Decimal(text), any(ch in text for ch in ".eE")
    except InvalidOperation:  # pragma: no cover - guarded by the regex
        return None


def expand_range(item: str, range_sep: str = ":") -> list[int | float] | None:
    """Expand ``start:stop[:step]`` (stop **inclusive**), or None if not a numeric range.

    The step defaults to 1 (or -1 when stop < start). Integers stay integers;
    any float in the text gives floats computed exactly with Decimal, so
    ``0.1:0.5:0.1`` is 0.1, 0.2, 0.3, 0.4, 0.5.
    """
    parts = item.split(range_sep)
    if len(parts) not in (2, 3):
        return None
    numbers = [_number(part) for part in parts]
    if any(number is None for number in numbers):
        return None
    start, stop = numbers[0][0], numbers[1][0]
    as_float = any(number[1] for number in numbers)
    if len(numbers) == 3:
        step = numbers[2][0]
    else:
        step = Decimal(1) if stop >= start else Decimal(-1)
    if step == 0:
        raise ValueError(f"Invalid range {item!r}: step must not be zero")
    if (stop - start) * step < 0:
        raise ValueError(f"Invalid range {item!r}: step has the wrong sign to reach stop")
    count = int((stop - start) // step) + 1
    if count > MAX_AXIS_VALUES:
        raise ValueError(f"Range {item!r} expands to {count} values (limit {MAX_AXIS_VALUES})")
    values = [start + step * i for i in range(count)]
    return [float(v) if as_float else int(v) for v in values]


def _literal(text: str) -> Any:
    """JSON if it parses, otherwise the text itself."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def parse_set(override: str, delims: Delimiters | None = None) -> SetSpec:
    """Parse ``[JOB:]PARAM=VALUE`` where VALUE is a scalar, range or list.

    * ``5``, ``12.5``, ``true``, ``'"text"'``, ``[1,2]`` - one literal value
    * ``2:10:2`` or ``2:10`` - numeric range, stop inclusive (a sweep)
    * ``2,4,8`` - curated list; items may be ranges: ``1:3,8,10:20:5`` (a sweep)

    A colon-separated value is only a range when every piece is a number, so
    text such as ``C:/data`` stays literal. All-numeric text that is not meant
    as a range (``12:30:00``) is rejected as a bad range; JSON-quote it
    (``'"12:30:00"'``) to force it literal. JSON lists (``[1,2]``) are literal
    list-valued parameters, never sweeps.
    """
    delims = delims or Delimiters()
    delims.validate()
    if "=" not in override:
        raise ValueError(f"Invalid --set {override!r}: expected PARAM=VALUE")
    selector, raw = override.split("=", 1)
    job, sep, param = selector.partition(delims.job)
    if not sep:
        job, param = "", selector
    job, param = job.strip(), param.strip()
    if not param:
        raise ValueError(f"Invalid --set {override!r}: parameter is missing")
    items = _split_top_level(raw, delims.list)
    if len(items) == 1:
        item = raw.strip()
        try:
            return SetSpec(override, job, param, (json.loads(item),), False)
        except json.JSONDecodeError:
            pass
        expanded = expand_range(item, delims.range)
        if expanded is None:
            return SetSpec(override, job, param, (raw,), False)
        return SetSpec(override, job, param, tuple(expanded), True)
    values: list[Any] = []
    for item in items:
        item = item.strip()
        if not item:
            raise ValueError(f"Invalid --set {override!r}: empty item in list")
        try:
            values.append(json.loads(item))
            continue
        except json.JSONDecodeError:
            pass
        expanded = expand_range(item, delims.range)
        values.extend(expanded if expanded is not None else [item])
    if len(values) > MAX_AXIS_VALUES:
        raise ValueError(f"--set {override!r} expands to {len(values)} values (limit {MAX_AXIS_VALUES})")
    return SetSpec(override, job, param, tuple(values), True)


def _has_value(job: dict[str, Any], param: str) -> bool:
    record = job.get("parameters", {}).get(param)
    return isinstance(record, dict) and "value" in record


def _providers(job: dict[str, Any]) -> set[str]:
    """IDs of the jobs (J-names) whose outputs this job uses."""
    found: set[str] = set()
    for pair in job.get("groups", []):
        if isinstance(pair, list) and pair and isinstance(pair[0], str) and "." in pair[0]:
            provider = pair[0].split(".", 1)[0]
            if JOB_NAME.fullmatch(provider):
                found.add(provider)
    return found


def _remap_job_references(job: dict[str, Any], id_map: dict[str, str]) -> dict[str, Any]:
    rewritten = copy.deepcopy(job)
    for pair in rewritten.get("groups", []):
        if isinstance(pair, list) and pair and isinstance(pair[0], str) and "." in pair[0]:
            provider, output = pair[0].split(".", 1)
            if provider in id_map:
                pair[0] = f"{id_map[provider]}.{output}"
    return rewritten


def apply_sets(workflow: dict[str, Any], overrides: list[str], *,
               origins: dict[str, set[str]] | None = None,
               replicated: list[str] | None = None,
               combine: str = "product",
               delims: Delimiters | None = None,
               max_jobs: int = 1000,
               keep_unswept: bool = False,
               report: dict[str, Any] | None = None) -> tuple[dict[str, Any], int | None]:
    """Apply --set overrides, expanding ranges and lists into extra jobs.

    Returns ``(workflow, combinations)``; ``combinations`` is None when nothing
    was swept. A scalar ``--set`` just changes a value. Each range or list is an
    **axis**; the replicated jobs are copied once per combination of axis values
    (``combine="product"``: every combination, first ``--set`` varying slowest;
    ``"zip"``: axes advance together and must be the same length).

    ``JOB:PARAM=...`` names a job of the **source** workflow (``origins`` maps
    generated IDs to source IDs; without it the IDs are used as given). An
    untargeted ``PARAM=...`` applies to every job that has the parameter, and
    targeted values are applied after untargeted ones.

    Only the jobs a sweep changes, plus every job downstream of them, are
    copied per combination. Other jobs (including those outside
    ``replicated``) are left out, or with ``keep_unswept`` kept once after the
    copies; they only receive scalar values. A copied job that uses a left-out
    job is an error. ``report["dropped"]`` lists the source IDs left out.
    """
    if combine not in ("product", "zip"):
        raise ValueError("combine must be 'product' or 'zip'")
    delims = delims or Delimiters()
    specs = [parse_set(override, delims) for override in overrides]
    jobs = workflow.get("jobs", {})
    if origins is None:
        origins = {name: {name} for name in jobs}
    if replicated is None:
        replicated = list(jobs)
    replicated = sorted(replicated, key=lambda n: int(JOB_NAME.fullmatch(n).group(1))
                        if JOB_NAME.fullmatch(n) else 0)
    seen: set[tuple[str, str]] = set()
    for spec in specs:
        if (spec.job, spec.param) in seen:
            raise ValueError(f"--set for {spec.param!r}"
                             + (f" on {spec.job}" if spec.job else "") + " given more than once")
        seen.add((spec.job, spec.param))

    def reach(spec: SetSpec, pool: list[str]) -> list[str]:
        return [name for name in pool
                if (not spec.job or spec.job in origins.get(name, {name}))
                and _has_value(jobs[name], spec.param)]

    axes = [index for index, spec in enumerate(specs) if spec.sweep]
    result = copy.deepcopy(workflow)
    ordered = ([i for i, s in enumerate(specs) if not s.job]
               + [i for i, s in enumerate(specs) if s.job])
    if not axes:
        for spec in specs:
            if not reach(spec, list(jobs)):
                where = f"source job {spec.job}" if spec.job else "any job"
                raise ValueError(f"Parameter {spec.param!r} not found in {where}")
        for index in ordered:
            for name in reach(specs[index], list(jobs)):
                result["jobs"][name]["parameters"][specs[index].param]["value"] = specs[index].values[0]
        return result, None

    # Copy the jobs a sweep changes and everything downstream of them.
    targets: dict[int, list[str]] = {}
    swept: set[str] = set()
    for index in axes:
        spec = specs[index]
        targets[index] = reach(spec, replicated)
        if not targets[index]:
            where = f"source job {spec.job}" if spec.job else "any repeated job"
            raise ValueError(f"Parameter {spec.param!r} not found in {where}")
        swept.update(targets[index])
    grown = True
    while grown:
        grown = False
        for name in replicated:
            if name not in swept and _providers(jobs[name]) & swept:
                swept.add(name)
                grown = True
    block = [name for name in replicated if name in swept]
    rest = [name for name in jobs if name not in swept] if keep_unswept else []
    dropped = [name for name in jobs if name not in swept and name not in rest]
    for name in block + rest:
        missing = _providers(jobs[name]) & set(dropped)
        if missing:
            raise ValueError(f"{name} uses {', '.join(sorted(missing))}, which no sweep changes; "
                             "add --include-remainder-jobs to keep such jobs once")
    for index in range(len(specs)):
        if index not in targets:
            targets[index] = reach(specs[index], block + rest)
            if not targets[index]:
                where = f"source job {specs[index].job}" if specs[index].job else "any kept job"
                raise ValueError(f"Parameter {specs[index].param!r} not found in {where}"
                                 + (" (it is not swept; add --include-remainder-jobs to keep it)"
                                    if dropped else ""))
    if report is not None:
        report["dropped"] = sorted({src for name in dropped for src in origins.get(name, {name})})

    lengths = [len(specs[i].values) for i in axes]
    if combine == "zip":
        if len(set(lengths)) != 1:
            detail = ", ".join(f"{specs[i].text} ({n})" for i, n in zip(axes, lengths))
            raise ValueError("--combine zip needs equal-length sweeps: " + detail)
        combos = list(zip(*(specs[i].values for i in axes)))
        count = lengths[0]
    else:
        count = 1
        for n in lengths:
            count *= n
        combos = None
    total = count * len(block) + len(rest)
    if total > max_jobs:
        raise ValueError(f"This would generate {total} jobs (limit {max_jobs}); "
                         "raise --max-jobs if that is intended")
    if combos is None:
        combos = list(itertools.product(*(specs[i].values for i in axes)))
    axis_slot = {spec_index: slot for slot, spec_index in enumerate(axes)}

    width = len(block)
    rest_map = {old: f"J{count * width + i + 1}" for i, old in enumerate(rest)}
    first_map = {old: f"J{i + 1}" for i, old in enumerate(block)}
    expanded: dict[str, Any] = {}
    for c in range(count):
        id_map = {old: f"J{c * width + i + 1}" for i, old in enumerate(block)}
        for old in block:
            expanded[id_map[old]] = _remap_job_references(jobs[old], {**rest_map, **id_map})
    for old in rest:
        expanded[rest_map[old]] = _remap_job_references(jobs[old], {**first_map, **rest_map})
    result["jobs"] = expanded

    def new_id(c: int, old: str) -> str:
        return rest_map[old] if old in rest_map else f"J{c * width + block.index(old) + 1}"

    for c, combo in enumerate(combos):
        for index in ordered:
            spec = specs[index]
            value = combo[axis_slot[index]] if spec.sweep else spec.values[0]
            for old in targets[index]:
                expanded[new_id(c, old)]["parameters"][spec.param]["value"] = value
    return result, count


def update_metadata(workflow: dict[str, Any], repeats: int | None, overrides: list[str], *,
                    update_id: bool = True, update_date: bool = True,
                    update_title: bool = True, combinations: int | None = None) -> None:
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
        repeated = f" repeated {repeats} times" if repeats is not None else ""
        swept = f" x {combinations} parameter combinations" if combinations is not None else ""
        workflow["title"] = f"{kind_label}{repeated}{swept}{edits}"


def filename_override_suffix(overrides: list[str]) -> str:
    """Create filesystem-safe PARAM_VALUE components for --set overrides."""
    parts = []
    for override in overrides:
        selector, value = override.split("=", 1)
        # Include a job selector, if supplied, to avoid ambiguous names.
        label = f"{selector}_{value}"
        label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("._-")[:48].rstrip("._-")
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
                             "other parents' inputs stay fixed (default: all parents). "
                             "Use '--parents none' so no connection advances (plain replication)")
    parser.add_argument("--no-series-extension", action="store_true",
                        help="Disable series detection: always make N copies of each selected job")
    parser.add_argument("-o", "--output", type=Path, help="Output JSON path (default: workflow.json or INPUT_STEM_repeated_N[_PARAM_VALUE].json)")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="PARAM=VALUE",
                        help="Override a parameter. VALUE may be a scalar, a range START:STOP[:STEP] "
                             "(stop inclusive), or a list A,B,C (items may be ranges) which expands "
                             "into extra jobs. Optionally JOB:PARAM=VALUE, where JOB is a job ID in "
                             "the source workflow; repeatable")
    parser.add_argument("--combine", choices=("product", "zip"), default="product",
                        help="How several ranged/listed --set values combine: every combination "
                             "(product, default) or in lockstep (zip, equal lengths required)")
    parser.add_argument("--job-sep", default=Delimiters.job, metavar="STR",
                        help="Separator between JOB and PARAM in --set (default ':')")
    parser.add_argument("--range-sep", default=Delimiters.range, metavar="STR",
                        help="Separator inside START:STOP:STEP ranges (default ':')")
    parser.add_argument("--list-sep", default=Delimiters.list, metavar="STR",
                        help="Separator between list items in --set values (default ',')")
    parser.add_argument("--max-jobs", type=int, default=1000, metavar="N",
                        help="Refuse to write more than N jobs from a sweep (default 1000)")
    parser.add_argument("--no-id-update", action="store_true", help="Preserve the input workflow _id")
    parser.add_argument("--no-date-update", dest="no_date_update", action="store_true",
                        help="Preserve createdAt")
    parser.add_argument("--no-title-update", action="store_true", help="Preserve the input workflow title")
    args = parser.parse_args(argv)
    try:
        workflow = json.loads(args.input.read_text(encoding="utf-8")) if args.input else template()
        if not isinstance(workflow, dict):
            raise ValueError("Top-level JSON must be an object")
        delims = Delimiters(job=args.job_sep, range=args.range_sep, list=args.list_sep)
        delims.validate()
        report: dict[str, Any] = {}
        if args.repeats is not None:
            selected_jobs = ([item.strip() for group in args.jobs for token in group
                              for item in token.split(",") if item.strip()]
                             if args.jobs else None)
            parent_ids = ([item.strip() for group in args.parents for token in group
                           for item in token.split(",") if item.strip()]
                          if args.parents else None)
            if parent_ids is not None and any(item.lower() == "none" for item in parent_ids):
                if len(parent_ids) > 1:
                    raise ValueError("--parents none cannot be combined with parent IDs")
                parent_ids = []
            workflow = repeat_workflow(workflow, args.repeats, jobs=selected_jobs,
                                       no_series_extension=args.no_series_extension,
                                       exclude_remainder_jobs=(True if args.remainder_choice is None
                                                               else args.remainder_choice),
                                       selected_parents=parent_ids, report=report)
        if args.repeats is None and (args.jobs or args.parents or args.no_series_extension):
            raise ValueError("--jobs, --parents and --no-series-extension require --repeats (-n)")
        workflow, combinations = apply_sets(
            workflow, args.overrides, origins=report.get("origins"),
            replicated=report.get("replicated"), combine=args.combine,
            delims=delims, max_jobs=args.max_jobs,
            keep_unswept=args.remainder_choice is False, report=report)
        if report.get("dropped"):
            print(f"note: left out {', '.join(report['dropped'])}: no sweep changes "
                  "them or anything downstream (--include-remainder-jobs keeps them once)",
                  file=sys.stderr)
        if args.repeats is not None or combinations is not None:
            update_metadata(workflow, args.repeats, args.overrides,
                            update_id=not args.no_id_update,
                            update_date=not args.no_date_update,
                            update_title=not args.no_title_update,
                            combinations=combinations)
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
