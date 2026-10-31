"""Edge Fleet: Claude agents each nurse one simulated Unitree G1 up a fixed-line slope.

Each robot gets two hidden faults out of: weak balance assist, boot traction off, not clipped to
the line, or its walking policy offloaded to the cloud (actions arrive late). The robot runs its
policy locally; its agent only sees telemetry and may change one setting per round. The
simulator decides the verdict: a summit is reaching 1.5 m of ascent without falling.

Phase 1 (train): robots 0-11 work alone; robots 12-23 face the same faults and seeds but read a
fleet rulebook that a coordinator agent rewrites after every round.
Phase 2 (held-out): 24 new robots with new seeds and fault pairs. Half start with no rulebook,
half get the frozen rulebook from phase 1. Nothing is learned during this phase.

    python fleet.py --sweep                 # latency sweep only, no API calls
    python fleet.py --rounds 5 --holdout-rounds 3
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import itertools
import json
import os
import pathlib
import random
import sys
import time
from multiprocessing import Pool

import anthropic
import requests

SIM_DIR = os.environ.get("SIM_DIR", "/content/robot-incident-analyst")
POLICY = "models/ppo_fixed_line_slope/g1_fixed_line_final.zip"
MODEL = "claude-opus-5-5"
CLOUD_LAG_STEPS = 6          # measured: 0-3 steps still summits, 6+ never does
TARGET_ASCENT = 1.5
N_PAIRS = 12
FAULTS = ["assist", "traction", "line", "cloud"]
FAULT_PAIRS = list(itertools.combinations(FAULTS, 2))   # 6 pairs
TUNABLES = {
    "balance_assist_scale": "float 0..1. Torso stabilisation strength. 1.0 is nominal.",
    "boot_traction_enabled": "bool. Crampon-like boot traction on the slope.",
    "fixed_line_enabled": "bool. Rope ascender clipped to the fixed line.",
    "policy_placement": "'edge' or 'cloud'. Where the walking policy runs. Cloud adds network delay to every action.",
}

# ------------------------------------------------------------------ simulation (worker processes)

_policy = None


def _init_worker():
    global _policy
    import torch
    torch.set_num_threads(1)
    sys.path.insert(0, SIM_DIR)
    os.chdir(SIM_DIR)
    from stable_baselines3 import PPO
    _policy = PPO.load(POLICY, device="cpu")


def run_episode(job: dict) -> dict:
    """Roll out one episode under job['config'] and return the verdict plus telemetry."""
    import numpy as np
    import incident_loop as il

    cfg, seed, impulse = job["config"], job["seed"], job["impulse"]
    env = il.make_env("assist", impulse)
    env.set_balance_assist_scale(float(cfg["balance_assist_scale"]))
    env.set_traction_enabled(bool(cfg["boot_traction_enabled"]))
    env.set_line_enabled(bool(cfg["fixed_line_enabled"]))
    lag = job.get("lag", CLOUD_LAG_STEPS if cfg["policy_placement"] == "cloud" else 0)

    obs, _ = env.reset(seed=seed)
    queue: collections.deque = collections.deque()
    zero = np.zeros(env.action_space.shape, dtype=np.float32)
    log, info, steps, peak, t0 = [], {}, 0, 0.0, time.time()
    for _ in range(env.max_episode_steps):
        action, _ = _policy.predict(obs, deterministic=True)
        queue.append(action)
        obs, _r, terminated, truncated, info = env.step(queue.popleft() if len(queue) > lag else zero)
        steps += 1
        ascent = float(info.get("ascent", 0.0))
        peak = max(peak, ascent)
        reached = ascent >= TARGET_ASCENT and not info.get("failure", False)
        if steps % 25 == 0 or terminated or truncated or reached:
            row = {"step": steps, **{k: round(float(info.get(k, 0.0)), 3) for k in il.LOG_KEYS}}
            row["action_latency_steps"] = lag
            log.append(row)
        if terminated or truncated or reached:
            break
    failure = bool(info.get("failure", False))
    summit = peak >= TARGET_ASCENT and not failure
    return {
        "robot": job["robot"], "lag": lag, "steps": steps, "success": summit, "failure": failure,
        "clean": bool(info.get("success", False)),
        "outcome": "summit" if summit else ("fell" if failure else "stalled"),
        "ascent_m": round(float(info.get("ascent", 0.0)), 3), "peak_m": round(peak, 3),
        "upright": round(float(info.get("upright_score", 0.0)), 3),
        "sim_seconds": round(time.time() - t0, 1),
        "log": log[-24:],
    }

# ------------------------------------------------------------------ fleet setup


def initial_config(faults: tuple[str, ...]) -> dict:
    cfg = {"balance_assist_scale": 1.0, "boot_traction_enabled": True,
           "fixed_line_enabled": True, "policy_placement": "edge"}
    for fault in faults:
        if fault == "assist":
            cfg["balance_assist_scale"] = 0.4
        elif fault == "traction":
            cfg["boot_traction_enabled"] = False
        elif fault == "line":
            cfg["fixed_line_enabled"] = False
        elif fault == "cloud":
            cfg["policy_placement"] = "cloud"
    return cfg


def build_fleet(groups: tuple[str, str], seed_base: int, shuffle: int | None = None) -> list[dict]:
    """Two groups of N_PAIRS robots; robot i in each group gets the same faults, seed and shove."""
    pairs = [FAULT_PAIRS[i % len(FAULT_PAIRS)] for i in range(N_PAIRS)]
    if shuffle is not None:
        random.Random(shuffle).shuffle(pairs)
    fleet = []
    for g, group in enumerate(groups):
        for i in range(N_PAIRS):
            fleet.append({
                "robot": g * N_PAIRS + i, "group": group, "faults": list(pairs[i]),
                "seed": seed_base + i, "impulse": [600.0, 700.0, 800.0][i % 3],
                "config": initial_config(pairs[i]), "history": [], "solved_round": None,
                "calls": 0, "tokens": 0,
            })
    return fleet

# ------------------------------------------------------------------ agents

client = anthropic.Anthropic(max_retries=4)

AGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {"type": "string"},
        "tunable": {"type": "string", "enum": list(TUNABLES) + ["none"]},
        "value": {"type": "string"},
        "lesson": {"type": "string"},
    },
    "required": ["diagnosis", "tunable", "value", "lesson"],
    "additionalProperties": False,
}

RULEBOOK_SCHEMA = {
    "type": "object",
    "properties": {"rules": {"type": "array", "items": {"type": "string"}}},
    "required": ["rules"],
    "additionalProperties": False,
}

AGENT_SYSTEM = f"""You supervise one Unitree G1 humanoid climbing a 35-degree slope on a fixed line in simulation.
Its walking policy is a trained network. A sideways shove hits it mid-climb. The goal is reaching {TARGET_ASCENT} m of
ascent without falling ("summit"). The robot may have more than one fault.
You cannot see the robot's configuration. You see the outcome and telemetry of its last attempt, and your past changes.
Each round you may change exactly one setting:
{json.dumps(TUNABLES, indent=1)}
Read the telemetry like an engineer: pelvis height sinking, upright score falling, line load at zero, ascent stalling,
action_latency_steps. Give a one-sentence diagnosis, the one change, and a one-sentence lesson another robot could reuse
(a rule from symptom to fix). Values: numbers as decimals, booleans as true/false, placement as edge/cloud."""


AGENT_MODEL = MODEL          # robot agents; the coordinator always uses MODEL


def ask_json(system: str, prompt: str, schema: dict, effort: str, model: str = MODEL) -> tuple[dict, int]:
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"stop_reason={response.stop_reason}")
    tokens = response.usage.input_tokens + response.usage.output_tokens
    return json.loads(next(b.text for b in response.content if b.type == "text")), tokens


def robot_agent(robot: dict, result: dict, rulebook: list[str]) -> tuple[dict, int]:
    parts = [f"Last attempt: {result['outcome']} after {result['steps']} steps, ascent {result['ascent_m']} m "
             f"(peak {result['peak_m']} m), upright {result['upright']}.",
             "Telemetry (every 25 steps, last rows):\n" + "\n".join(json.dumps(r) for r in result["log"])]
    if robot["history"]:
        parts.append("Your previous changes and their outcomes:\n" + "\n".join(
            f"round {h['round']}: set {h['tunable']}={h['value']} -> {h['outcome_after']}" for h in robot["history"]))
    if rulebook:
        parts.append("Fleet rulebook, learned by other robots:\n" + "\n".join(f"- {r}" for r in rulebook))
    return ask_json(AGENT_SYSTEM, "\n\n".join(parts), AGENT_SCHEMA, "low", AGENT_MODEL)


def coordinator(rulebook: list[str], reports: list[dict]) -> list[str]:
    prompt = ("Current rulebook:\n" + ("\n".join(f"- {r}" for r in rulebook) or "(empty)") +
              "\n\nThis round's reports (robot, outcome, diagnosis, change, lesson, and whether the robot's previous change worked):\n" +
              "\n".join(json.dumps(r) for r in reports) +
              "\n\nRewrite the rulebook: at most 8 short rules mapping telemetry symptoms to the one setting that fixes them, "
              "including the order to fix several faults in. Keep rules the evidence supports, drop ones the outcomes contradicted.")
    out, _ = ask_json("You coordinate a fleet of humanoid robot agents and maintain their shared rulebook.",
                      prompt, RULEBOOK_SCHEMA, "medium")
    return out["rules"][:8]


def apply_change(cfg: dict, tunable: str, value: str) -> dict:
    cfg = dict(cfg)
    v = str(value).strip().lower()
    if tunable == "balance_assist_scale":
        try:
            cfg[tunable] = max(0.0, min(1.0, float(v)))
        except ValueError:
            pass
    elif tunable in ("boot_traction_enabled", "fixed_line_enabled"):
        cfg[tunable] = v in ("true", "1", "yes", "on", "enabled")
    elif tunable == "policy_placement" and v in ("edge", "cloud"):
        cfg[tunable] = v
    return cfg

# ------------------------------------------------------------------ live board


def publish(kind: str, payload: dict) -> None:
    site = os.environ.get("CONVEX_SITE_URL")
    if not site:
        return
    try:
        requests.post(f"{site.rstrip('/')}/ingest", timeout=5, json={"kind": kind, "payload": payload},
                      headers={"x-fleet-secret": os.environ.get("FLEET_SECRET", "")})
    except requests.RequestException as e:
        print(f"[board] publish failed: {e}")


def robot_view(robot: dict, result: dict | None, decision: dict | None) -> dict:
    return {
        "robot": robot["robot"], "group": robot["group"], "fault": " + ".join(robot["faults"]),
        "outcome": result["outcome"] if result else "pending",
        "ascent_m": result["peak_m"] if result else 0.0,
        "steps": result["steps"] if result else 0,
        "diagnosis": decision["diagnosis"] if decision else "",
        "change": f"{decision['tunable']}={decision['value']}" if decision and decision["tunable"] != "none" else "",
        "lesson": decision["lesson"] if decision else "",
        "solved_round": robot["solved_round"],
    }


def group_stats(fleet: list[dict], groups: tuple[str, str]) -> dict:
    stats = {}
    for g in groups:
        rs = [r for r in fleet if r["group"] == g]
        solved = [r for r in rs if r["solved_round"]]
        calls = sum(r["calls"] for r in rs)
        stats[g] = {
            "solved": len(solved), "of": len(rs),
            "mean_rounds": round(sum(r["solved_round"] for r in solved) / len(solved), 2) if solved else None,
            "calls": calls, "tokens": sum(r["tokens"] for r in rs),
            "calls_per_summit": round(calls / len(solved), 2) if solved else None,
        }
    return stats

# ------------------------------------------------------------------ phases


def run_phase(pool, agents, phase: str, groups: tuple[str, str], fleet: list[dict], rounds: int,
              rulebook: list[str], learn: bool, out: pathlib.Path) -> list[str]:
    """Round loop. If learn, the second group reads a rulebook the coordinator rewrites each round;
    otherwise the second group reads the frozen rulebook passed in. The first group never reads one."""
    publish("reset", {"phase": phase, "groups": list(groups), "rounds": rounds,
                      "robots": [robot_view(r, None, None) for r in fleet]})
    for rnd in range(1, rounds + 1):
        t0 = time.time()
        active = [r for r in fleet if r["solved_round"] is None]
        results = {res["robot"]: res for res in pool.map(
            run_episode, [{k: r[k] for k in ("robot", "config", "seed", "impulse")} for r in active])}
        for r in active:
            res = results[r["robot"]]
            if r["history"]:
                r["history"][-1]["outcome_after"] = res["outcome"]
            if res["success"]:
                r["solved_round"] = rnd

        todo = [r for r in active if r["solved_round"] is None] if rnd < rounds else []
        futures = {agents.submit(robot_agent, r, results[r["robot"]],
                                 rulebook if r["group"] == groups[1] else []): r for r in todo}
        decisions: dict[int, dict] = {}
        for fut in cf.as_completed(futures):
            r = futures[fut]
            r["calls"] += 1
            try:
                decisions[r["robot"]], tokens = fut.result()
                r["tokens"] += tokens
            except Exception as e:  # one agent failing must not stop the fleet
                print(f"[agent {r['robot']}] {e}")
                decisions[r["robot"]] = {"diagnosis": f"agent error: {e}", "tunable": "none",
                                         "value": "", "lesson": ""}

        reports = []
        for r in todo:
            d = decisions[r["robot"]]
            prev = r["history"][-1] if r["history"] else None
            if d["tunable"] != "none":
                r["config"] = apply_change(r["config"], d["tunable"], d["value"])
                r["history"].append({"round": rnd, "tunable": d["tunable"], "value": d["value"],
                                     "outcome_after": "pending"})
            reports.append({"robot": r["robot"], "outcome": results[r["robot"]]["outcome"],
                            "diagnosis": d["diagnosis"], "change": f"{d['tunable']}={d['value']}",
                            "lesson": d["lesson"],
                            "previous_change": prev and f"{prev['tunable']}={prev['value']}"})

        if learn and reports:
            try:
                rulebook = coordinator(rulebook, reports)
            except Exception as e:
                print(f"[coordinator] {e}")

        stats = group_stats(fleet, groups)
        summary = {"phase": phase, "groups": list(groups), "round": rnd, "rounds": rounds,
                   "solved": {g: stats[g]["solved"] for g in groups}, "stats": stats,
                   "rulebook": rulebook, "wall_s": round(time.time() - t0, 1),
                   "robots": [robot_view(r, results.get(r["robot"]), decisions.get(r["robot"])) for r in fleet]}
        publish("round", summary)
        (out / f"{phase}_round_{rnd:02d}.json").write_text(json.dumps({**summary, "fleet": fleet}, indent=1, default=str))
        print(f"[{phase}] round {rnd}: " + "  ".join(
            f"{g} {stats[g]['solved']}/{stats[g]['of']}" for g in groups) + f"  ({summary['wall_s']}s)")
        if all(r["solved_round"] for r in fleet):
            break
    for g in groups:
        print(f"[{phase}] {g}: {json.dumps(group_stats(fleet, groups)[g])}")
    return rulebook


def latency_sweep(pool, out: pathlib.Path) -> None:
    """Healthy robots, same seeds, increasing action delay. No agents involved."""
    lags = [0, 1, 2, 3, 4, 6, 8, 10]
    seeds = [4100, 4102, 4104, 4106, 4108, 4110]
    jobs = [{"robot": i, "lag": lag, "seed": seed, "impulse": 700.0, "config": initial_config(())}
            for i, (lag, seed) in enumerate(itertools.product(lags, seeds))]
    results = pool.map(run_episode, jobs)
    rows = []
    for lag in lags:
        rs = [r for r in results if r["lag"] == lag]
        summits = [r for r in rs if r["success"]]
        rows.append({"lag_steps": lag, "summits": len(summits), "runs": len(rs),
                     "mean_peak_m": round(sum(r["peak_m"] for r in rs) / len(rs), 2),
                     "mean_steps_to_summit": round(sum(r["steps"] for r in summits) / len(summits)) if summits else None})
        print(f"[sweep] lag {lag:2d} steps: {len(summits)}/{len(rs)} summit, mean peak {rows[-1]['mean_peak_m']} m, "
              f"mean steps to summit {rows[-1]['mean_steps_to_summit']}")
    publish("latency", {"rows": rows})
    (out / "latency_sweep.json").write_text(json.dumps(rows, indent=1))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rounds", type=int, default=5)
    p.add_argument("--holdout-rounds", type=int, default=3)
    p.add_argument("--workers", type=int, default=os.cpu_count() or 12)
    p.add_argument("--sweep", action="store_true", help="run only the latency sweep")
    p.add_argument("--no-sweep", action="store_true", help="skip the latency sweep")
    p.add_argument("--agent-model", default=MODEL, help="model for the robot agents (coordinator stays on Opus)")
    p.add_argument("--rulebook", help="rulebook.json from an earlier run: skip training, run only the held-out phase")
    p.add_argument("--out", default="runs")
    a = p.parse_args()
    global AGENT_MODEL
    AGENT_MODEL = a.agent_model
    print(f"robot agents: {AGENT_MODEL}")

    out = pathlib.Path(a.out) / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    with Pool(min(a.workers, 2 * N_PAIRS), initializer=_init_worker) as pool, \
            cf.ThreadPoolExecutor(max_workers=2 * N_PAIRS) as agents:
        if not a.no_sweep:
            latency_sweep(pool, out)
        if a.sweep:
            return
        if a.rulebook:
            rulebook = json.loads(pathlib.Path(a.rulebook).read_text())
            print(f"loaded {len(rulebook)} rules from {a.rulebook}")
        else:
            train_groups = ("solo", "shared")
            rulebook = run_phase(pool, agents, "train", train_groups, build_fleet(train_groups, 4100),
                                 a.rounds, [], learn=True, out=out)
        if a.holdout_rounds:
            holdout_groups = ("no rulebook", "with rulebook")
            run_phase(pool, agents, "holdout", holdout_groups, build_fleet(holdout_groups, 5100, shuffle=7),
                      a.holdout_rounds, rulebook, learn=False, out=out)
    (out / "rulebook.json").write_text(json.dumps(rulebook, indent=1))
    print(f"done -> {out}")


if __name__ == "__main__":
    main()
