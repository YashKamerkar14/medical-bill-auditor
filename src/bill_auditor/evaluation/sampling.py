"""The fixed, stratified sample for the ReAct agent (Milestone 4), chosen BEFORE any agent call.

Design (docs/decisions/0009):
- tuning: 10 generated development bills, used only to develop the agent's
  prompt; never scored;
- dev_scored: 40 development bills = all 4 injection cases + 11 other hard cases
  + 25 generated;
- heldout_scored: 75 held-out bills = all 15 hard cases + 60 generated.
Held-out matters most: the full system was tuned on development data over three
milestones, so the held-out comparison is the fair one.
Generated bills are stratified by their kind (clean, trap, cannot-check, and
each error type) with proportional allocation and at least one bill for every
error type; hard cases by their tag group. The seed and the resulting case ids
are written to eval/samples/m4_agent_sample.json and committed.
"""

import json
import random
from collections import defaultdict
from pathlib import Path

from bill_auditor.evaluation.schemas import Case

SEED = 20261011


def group_of(case: Case) -> str:
    tag = case.tags[0]
    if case.source == "hard":
        return tag.split(":")[0]
    return tag if tag.startswith("error:") else tag.split(":")[0]


def stratified(cases: list[Case], n: int, rng: random.Random, min_one_prefix: str = "error:") -> list[Case]:
    """Proportional allocation by group (largest remainder), at least one per group starting with min_one_prefix."""
    groups: dict[str, list[Case]] = defaultdict(list)
    for case in sorted(cases, key=lambda c: c.case_id):
        groups[group_of(case)].append(case)
    total = sum(len(g) for g in groups.values())
    quota = {g: max(1 if g.startswith(min_one_prefix) else 0, int(n * len(members) / total))
             for g, members in groups.items()}
    remainders = sorted(groups, key=lambda g: -(n * len(groups[g]) / total - int(n * len(groups[g]) / total)))
    i = 0
    while sum(quota.values()) < n:
        g = remainders[i % len(remainders)]
        if quota[g] < len(groups[g]):
            quota[g] += 1
        i += 1
    while sum(quota.values()) > n:  # the minimums can overshoot: trim the largest groups
        g = max(quota, key=lambda k: quota[k])
        quota[g] -= 1
    picked = []
    for g, members in groups.items():
        picked += rng.sample(members, min(quota[g], len(members)))
    return sorted(picked, key=lambda c: c.case_id)


def build_sample(cases: list[Case], seed: int = SEED) -> dict:
    rng = random.Random(seed)
    dev_gen = [c for c in cases if c.split == "dev" and c.source == "generated"]
    dev_hard = [c for c in cases if c.split == "dev" and c.source == "hard"]
    held_gen = [c for c in cases if c.split == "heldout" and c.source == "generated"]
    held_hard = [c for c in cases if c.split == "heldout" and c.source == "hard"]

    injection = [c for c in dev_hard if c.tags[0].startswith("injection:")]
    dev_hard_other = stratified([c for c in dev_hard if c not in injection], 11, rng, min_one_prefix="~")
    dev_gen_scored = stratified(dev_gen, 25, rng)
    tuning = rng.sample(sorted((c for c in dev_gen if c not in dev_gen_scored), key=lambda c: c.case_id), 10)
    held_gen_scored = stratified(held_gen, 60, rng)

    return {
        "seed": seed,
        "tuning": sorted(c.case_id for c in tuning),
        "dev_scored": sorted(c.case_id for c in injection + dev_hard_other + dev_gen_scored),
        "heldout_scored": sorted(c.case_id for c in held_hard + held_gen_scored),
    }


def write_sample(sample: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sample, indent=1), encoding="utf-8")
