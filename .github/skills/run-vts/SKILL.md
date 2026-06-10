---
name: run-vts
description: "Run Intel VTS test workflows end-to-end from this repository, validate CLI arguments, execute targeted or full bandwidth tests, and summarize PASS/FAIL with the exact log path. Use when user asks to run VTS, reproduce a test failure, validate a command, or compare serial vs parallel GPU bandwidth behavior. Keywords: start_vts.py, VTS, test 3, PCIe bandwidth, ze_bandwidth, memory_benchmark_l0, -tn, -inst, -mode, -dir, -engine, -tool."
---

# Run VTS

## Goal
Execute VTS safely and reproducibly, then return a concise diagnosis with command used, outcome, and log artifacts.

## Repository Context
- Entry point: `start_vts.py`
- Main bandwidth test: `gpu/bmg/tests/bandwidth/pcie_bw_test.py`
- Runtime helper script: `tools/runBwTest.sh`
- Logs folder: `logs/`

## Inputs To Confirm
Before running, confirm or infer:
- Test number (`-tn`, commonly `3` for PCIe bandwidth)
- GPU selection (`-inst`, supports `-1`, single ID, range, or list)
- Mode (`-mode`): `serial`, `parallel`, or `all`
- Direction (`-dir`): `h2d`, `d2h`, `bidirectional`, or `all`
- Engine (`-engine`): `copy`, `compute`, or `all`
- Tool (`-tool`): `ze_bandwidth`, `memory_benchmark_l0`, or `all`

Also confirm privilege mode:
- VTS runs should use root privilege (`sudo`) for stable hardware access.
- If already root, run without `sudo` prefix.
- If `sudo` is unavailable or requires interactive input, stop and report the blocker.

If user does not specify values, start with a low-risk smoke command before large/full runs.

## Standard Workflow
1. Validate repository state and required files exist.
2. Validate privilege path:
	- If non-root, prefer `sudo -n` to avoid hanging for password prompts.
	- If `sudo -n` fails, report that root privilege is required and request user action.
3. Use explicit spaced args (for example `-tn 3`, not compact forms like `-tn3`).
4. Run one small smoke command first.
5. If smoke passes, run requested full command.
6. Parse latest log in `logs/` and summarize root cause if failed.
7. Report exact next command that should be run to verify the fix.

## Command Templates
Use from repository root.

### Smoke Test (Single GPU, Quick)
```bash
sudo python3 start_vts.py -tn 3 -inst 0 -mode serial -dir h2d -engine copy -tool ze_bandwidth
```

### Parallel Example (Selected GPUs)
```bash
sudo python3 start_vts.py -tn 3 -inst 0-3 -mode parallel -dir d2h -engine compute -tool ze_bandwidth
```

### Full Test 3 Defaults
```bash
sudo python3 start_vts.py -tn 3
```

## Diagnostics Rules
- Do not modify expected bandwidth threshold logic unless user explicitly requests it.
- If parsing fails, inspect argument parsing in `common/inputParser.py` and test parser in `gpu/bmg/tests/bandwidth/pcie_bw_test.py`.
- If performance differs by GPU/socket, inspect NUMA mapping behavior in `tools/runBwTest.sh` and include evidence from run output.
- Always include the newest failure log path and the failing row(s) in the summary.
- If run was not executed with root privilege, call that out as a confidence risk in conclusions.

## Output Contract
Return:
1. Command executed
2. PASS/FAIL result
3. Log file path
4. If FAIL: most likely root cause and one concrete next command

Keep summaries short and actionable.
