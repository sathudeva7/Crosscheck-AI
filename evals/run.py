"""Run the eval and report it.

    .venv/bin/python -m evals.run                    # the signed-in patient
    .venv/bin/python -m evals.run --patient <clerk>  # a specific one
    .venv/bin/python -m evals.run --kind safety      # one family of cases

Two numbers matter more than the pass rate.

`grounded` is how often the verifier could trace every claim back to a tool
result. A high pass rate with a low grounded rate means the agent is often
right for reasons it cannot show, which on a medical record is not the same as
being right.

The calibration table is the one that tests the app's own claim. Every answer
carries a confidence band, and the band is only worth showing if a 'high' answer
is actually more likely to be correct than a 'low' one. If the accuracy is flat
across the bands, the badge is decoration — better found here than by a reader
who trusted it.
"""

from __future__ import annotations

import argparse
import asyncio
import time
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.db.models import Patient
from app.db.session import SessionLocal
from app.rag.agent import ask
from evals.cases import Case, build

warnings.filterwarnings("ignore")

# Questions run at once. The agent is mostly waiting on the model, so this is
# about provider rate limits rather than local work — and each needs its own
# database session, since a Session is not safe to share across tasks.
CONCURRENCY = 4

BANDS = ("high", "medium", "low", "none")


@dataclass
class Outcome:
    case: Case
    result: dict[str, Any]
    passed: bool
    why: str
    seconds: float

    @property
    def band(self) -> str:
        return self.result.get("band") or "none"


async def _run_one(case: Case, patient_id, gate: asyncio.Semaphore) -> Outcome:
    async with gate:
        db = SessionLocal()
        started = time.monotonic()
        try:
            result = await ask(db, patient_id, case.question)
        except Exception as problem:  # noqa: BLE001 - a crash is a failed case, not a failed run
            result = {"answer": "", "error": str(problem), "tool_calls": [], "band": "none"}
        finally:
            db.close()

        elapsed = time.monotonic() - started

    if result.get("error"):
        return Outcome(case, result, False, f"raised: {result['error'][:70]}", elapsed)

    passed, why = case.check(result)
    return Outcome(case, result, passed, why, elapsed)


async def run(patient_id, cases: list[Case]) -> list[Outcome]:
    gate = asyncio.Semaphore(CONCURRENCY)
    tasks = [asyncio.create_task(_run_one(case, patient_id, gate)) for case in cases]

    outcomes: list[Outcome] = []
    for done in asyncio.as_completed(tasks):
        outcome = await done
        outcomes.append(outcome)
        mark = "PASS" if outcome.passed else "FAIL"
        print(f"  [{len(outcomes):>2}/{len(cases)}] {mark}  {outcome.case.question[:66]}")
        if not outcome.passed:
            print(f"            {outcome.why}")

    # as_completed returns out of order; the report reads better in case order.
    order = {id(case): i for i, case in enumerate(cases)}
    return sorted(outcomes, key=lambda o: order[id(o.case)])


def report(outcomes: list[Outcome]) -> None:
    total = len(outcomes)
    passed = sum(1 for o in outcomes if o.passed)

    print("\n" + "=" * 78)
    print(f"  {passed}/{total} passed ({passed / total:.0%})" if total else "  no cases")
    print("=" * 78)

    print("\nBy kind")
    by_kind: dict[str, list[Outcome]] = defaultdict(list)
    for outcome in outcomes:
        by_kind[outcome.case.kind].append(outcome)
    for kind, group in sorted(by_kind.items()):
        ok = sum(1 for o in group if o.passed)
        print(f"  {kind:<11} {ok:>2}/{len(group):<3} {ok / len(group):>4.0%}")

    # --- the honesty measures ------------------------------------------------
    answered = [o for o in outcomes if not o.result.get("error")]
    grounded = sum(1 for o in answered if o.result.get("grounded", True))
    print("\nGroundedness")
    print(f"  every claim traceable to a tool result: {grounded}/{len(answered)}"
          f" ({grounded / len(answered):.0%})" if answered else "  n/a")

    # --- calibration ---------------------------------------------------------
    print("\nCalibration — is a confident answer actually more often right?")
    print(f"  {'band':<8} {'n':>4} {'accuracy':>9}   {'mean conf':>9}")
    scored = [o for o in outcomes if o.case.calibrated and not o.result.get("error")]
    for band in BANDS:
        group = [o for o in scored if o.band == band]
        if not group:
            continue
        ok = sum(1 for o in group if o.passed)
        confs = [o.result.get("confidence") for o in group if o.result.get("confidence") is not None]
        mean = f"{sum(confs) / len(confs):.2f}" if confs else "—"
        print(f"  {band:<8} {len(group):>4} {ok / len(group):>8.0%}   {mean:>9}")
    if not scored:
        print("  (no cases with a fact to score)")
    else:
        print("\n  Cases with no fact to score — absences, tool checks, safety —")
        print("  are left out: refusing correctly earns no confidence by design.")

    # --- cost of being wrong -------------------------------------------------
    failures = [o for o in outcomes if not o.passed]
    if failures:
        print(f"\nFailures ({len(failures)})")
        for outcome in failures:
            print(f"\n  [{outcome.case.kind}] {outcome.case.question}")
            print(f"    expected : {outcome.case.expects}")
            print(f"    why      : {outcome.why}")
            answer = (outcome.result.get("answer") or "").replace("\n", " ")
            print(f"    answered : {answer[:150]}")
            print(f"    tools    : {outcome.result.get('tool_calls')}")

    seconds = [o.seconds for o in outcomes]
    if seconds:
        seconds.sort()
        print(f"\nLatency  median {seconds[len(seconds) // 2]:.1f}s"
              f"   slowest {seconds[-1]:.1f}s"
              f"   total {sum(seconds):.0f}s of model time")

    print("\nTool use")
    used = Counter(t for o in outcomes for t in (o.result.get("tool_calls") or []))
    for tool, n in used.most_common():
        print(f"  {tool:<20} {n}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the question-answering agent.")
    parser.add_argument("--patient", help="clerk_user_id; defaults to the one with most documents")
    parser.add_argument("--kind", help="only run one kind: recall, absence, trend, tool, safety, injection")
    parser.add_argument("--limit", type=int, help="stop after this many cases")
    args = parser.parse_args()

    db = SessionLocal()
    if args.patient:
        patient = db.scalars(select(Patient).where(Patient.clerk_user_id == args.patient)).one()
    else:
        # The account with the most timeline events: an eval against an empty
        # record would pass every absence case and prove nothing.
        from sqlalchemy import func

        from app.db.models import TimelineEvent

        patient_id = db.execute(
            select(TimelineEvent.patient_id, func.count().label("n"))
            .group_by(TimelineEvent.patient_id)
            .order_by(func.count().desc())
            .limit(1)
        ).scalar_one()
        patient = db.get(Patient, patient_id)

    cases = build(db, patient.id)
    if args.kind:
        cases = [c for c in cases if c.kind == args.kind]
    if args.limit:
        cases = cases[: args.limit]
    db.close()

    print(f"Patient : {patient.clerk_user_id}")
    print(f"Cases   : {len(cases)}  ({', '.join(f'{k}×{n}' for k, n in Counter(c.kind for c in cases).items())})")
    print(f"Running {CONCURRENCY} at a time\n")

    started = time.monotonic()
    outcomes = asyncio.run(run(patient.id, cases))
    report(outcomes)
    print(f"\nWall clock: {time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    main()
