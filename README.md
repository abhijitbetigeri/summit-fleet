# Summit Fleet

**Claude agents fix a humanoid fleet from telemetry, with reflexes on the edge and judgment in the cloud.**

Live board: **https://hip-crab-119.convex.site**

24 simulated Unitree G1 humanoids climb a 35° fixed-line slope in MuJoCo. Each robot has two hidden faults
and its own Claude agent:

| Fault | What breaks |
|---|---|
| weak balance assist | pelvis sags, the robot falls |
| boot traction off | the robot slips and stalls |
| not clipped to the line | no rope load, it slides back |
| policy offloaded to the cloud | every action arrives 6 control steps late |

The walking policy runs on the robot. Agents never see the configuration. They read telemetry and change
one setting per round. A coordinator agent turns the robots' lessons into a shared fleet rulebook.
**The simulator is the only judge**: a summit is 1.5 m of ascent without falling.

## Results (30 Oct 2026)

**Latency sweep:** 48 runs, healthy robots, same seeds.

| Action delay (steps) | 0 | 1 | 2 | 3 | 4 | 6 | 8 | 10 |
|---|---|---|---|---|---|---|---|---|
| Summits | 6/6 | 6/6 | 6/6 | 6/6 | 4/6 | 0/6 | 6/6 | 4/6 |
| Mean steps to summit | 636 | 655 | 602 | 963 | 1270 | – | 1283 | 1177 |

With 0–2 steps of delay the robot is fast and reliable. Past that it is about 2× slower and erratic.
Failures are not monotonic in delay, because the robot becomes unpredictable rather than steadily worse.

**Fleet, Claude Opus 5.5 agents, two faults per robot:**

| Phase | No shared rulebook | Shared rulebook |
|---|---|---|
| Train (same faults and seeds) | 12/12 summited, 3.25 mean rounds | 12/12 summited, 3.17 mean rounds |
| Held-out (new seeds and fault pairs, 3 rounds) | 9/12, 2.89 mean rounds | 9/12, 2.89 mean rounds |

Two faults with one fix per round means three rounds is the minimum. Telemetry-only agents ran close to that
minimum. The shared rulebook gave Opus no measurable gain (a ceiling effect) and cost 11–29% more tokens.

Mid-run, the agents flagged that our original success check marked robots at 2.7–3.2 m as "stalled". The
verdict is now "reached 1.5 m without falling", and the stricter check is kept as a separate `clean` flag.

## Layout

- `fleet.py`: the fleet runner, with the simulation worker pool, robot agents, coordinator, latency sweep and both phases.
- `convex/`: the ingest endpoint, event store and live page, all served from Convex HTTP actions.

## Run it

`fleet.py` drives the G1 environment and PPO walking policy from
[robot-incident-analyst](https://github.com/abhijitbetigeri/robot-incident-analyst), built before this event.

```bash
git clone https://github.com/abhijitbetigeri/robot-incident-analyst
cd robot-incident-analyst && uv venv .venv && uv pip install -r requirements.txt requests anthropic
export ANTHROPIC_API_KEY=...                            # required
export CONVEX_SITE_URL=https://<deployment>.convex.site  # optional live board
export FLEET_SECRET=...                                 # must match `npx convex env set FLEET_SECRET`
SIM_DIR=$PWD OMP_NUM_THREADS=1 .venv/bin/python ../summit-fleet/fleet.py --rounds 5 --holdout-rounds 3
```

`--sweep` runs only the latency sweep, with no API calls. `--agent-model` picks the robot agents' model.
`--rulebook runs/<run>/rulebook.json` skips training and runs only the held-out phase.

## Prior art

- RoboEarth (Waibel et al., 2011): robots sharing knowledge through the cloud.
- Fleet-DAgger (Hoque et al., CoRL 2022) and Sirius-Fleet (Liu et al., CoRL 2024): fleet learning with human supervisors.
- REFLECT (Liu, Bahety, Song, CoRL 2023): LLMs explaining robot failures from logs.
- Real-Time Execution of Action Chunking Flow Policies (NeurIPS 2025): policies under inference latency.
