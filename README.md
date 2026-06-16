# Intel® GPU Verification Test Suite

## Overview
This test suite provides comprehensive validation tests for Intel GPUs, focusing on functionality, performance, and stability testing. 

## Usage

### Interactive Mode
```bash
sudo python3 start_vts.py
```
Launches an interactive menu with all available tests.

### Command Line Mode
```bash
sudo python3 start_vts.py -tn TESTNUMBER [OPTIONS]
```

### Configuration File Mode
```bash
sudo python3 start_vts.py -tc CONFIG_FILE
```

## Global Arguments
- `-h, --help` : Show help message
- `-tn` : Test Number or special mode:
  - `INT` : Run specific test number
  - `a` : Run all tests (1-14) sequentially
  - `c` : Collect logs and generate HTML summary report
  - `d` : Run debug script to collect system debug information
- `-inst` selectors are zero-based across tests. On a 16-GPU system, valid GPU IDs are `0-15`.
- `-tc` : JSON file test config path
- `-rep` : Number of repetitions to run the test (default: 1)
- `-rep_reset {None,flr}` : Reset type to perform on all GPU cards between each repetition cycle (default: None). Requires `-rep` > 1 to have effect.
- `-mt {xpum,dgdiag,None}` : Monitor type (default: xpum)
- `-cs {None,stress-ng,ptat}` : CPU stress tool to run in parallel (default: None)
- `-pcie_downgrade {True,False}` : Disable PCIe downgrade before test execution (default: False, True for LMT and Reset tests)
- `-d {True,False}` : Collect debug logs after each test execution (default: False)
- `-stop_on_error {True,False}` : Stop execution on first test failure and wait for user debug (default: False)
- `-live_mon {True,False}` : Enable the GPU usage and temperature live monitor dialog (default: False)
- `-y {True,False}` : Auto-confirm all interactive prompts for unattended/automated execution (default: False)
- `--skip-system-checks` : Skip pre-flight system validation checks

## GPU Presence Validation
VTS automatically verifies that all expected GPU devices are present before each test execution and between repetitions. The check compares the live GPU count (via `lspci`) against the count discovered at VTS startup.

- **Pre-test**: If GPUs are missing before a test starts, the test is immediately marked as FAIL
- **Per-repetition**: On multi-repetition runs (`-rep` > 1), GPU presence is re-verified before each subsequent repetition. If GPUs are missing, that repetition is marked FAIL and execution continues (or stops if `-stop_on_error True`)

This is especially important for automated executions, reset tests, and multi-repetition runs where a device may go down between iterations.

---

### Test 1: GPU Health Check
**Description:** Performs basic GPU health validation using XPUM health checks.

**Parameters:** None

**Example:**
```bash
sudo python3 start_vts.py -tn 1
```

---

### Test 2: GPU Environment and Device Check  
**Description:** Comprehensive GPU diagnostics using XPUM diagnostic tools.

**Parameters:**
- `-l STR` : Diagnostic level: quick, medium, long (default: long)

**Example:**
```bash
sudo python3 start_vts.py -tn 2 -l medium
```

---

### Test 3: PCIe Bandwidth Test
**Description:** Advanced PCIe bandwidth testing with multiple tools and modes.

**Parameters:**
- `-inst STR` : GPU device selector. Supported forms: single (`0`), range (`0-3`), list (`0,1,2,3`), mixed (`0-3,5,7-8`), `-1` for all devices (default: -1)
- `-mode STR` : Execution mode: serial, parallel, all (default: all)  
- `-dir STR` : Traffic direction: h2d, d2h, bidirectional, all (default: all)
- `-engine STR` : Traffic engine: copy, compute, all (default: all)
- `-tool STR` : Bandwidth test tool: ze_bandwidth, memory_benchmark_l0, all (default: all)
- `-iterations INT` : Number of test iterations (default: 500)
- `-size INT` : Buffer size in bytes (default: 268435456)

**Example:**
```bash
sudo python3 start_vts.py -tn 3 -inst 0 -mode parallel -tool ze_bandwidth
```

---

### Test 4: PCIe Lane Margin Test (LMT)
**Description:** PCIe lane margin testing with automatic EOM detection and PCIe downgrade disable functionality.

**Parameters:**
- `-inst STR` : GPU device selector. Supported forms: single (`0`), range (`0-3`), list (`0,1,2,3`), `-1` for all devices (default: -1)
- `-n INT` : Number of repeats (default: 1)
- `-rn INT` : Receiver Number(s) to be tested on (1-6). Multiple space-separated values are accepted. (default: 6)

**Notes:**
- This test supports Python 3.10, 3.12, and 3.13

**Example:**
```bash
sudo python3 start_vts.py -tn 4 -n 3 -rn 1 2 4
```

---

### Test 5: Reset Test
**Description:** Various PCIe reset mechanisms validation.

**Parameters:**
- `-rt STR` : Reset type: flr, warm, cold, soft, sbr, custom, linkdisable, linkchange, retrain, testonly, clean (default: testonly)
- `-iterations INT` : Number of iterations (default: 1)
- `-custom-script STR` : Custom script path for custom reset type

**Note:**
  -mt (monitor type) is overriding to None for Reset Tests.
  
**Example:**
```bash
sudo python3 start_vts.py -tn 5 -rt retrain -iterations 5
```

---

### Test 6: Memory Bandwidth Test  
**Description:** Memory bandwidth validation using XPUM tools.

**Parameters:**
- `-inst STR` : GPU device selector. Supported forms: single (`0`), range (`0-3`), list (`0,1,2,3`), mixed (`0-3,5,7-8`), `-1` for all devices (default: -1)

**Example:**
```bash
sudo python3 start_vts.py -tn 6 -inst 1
```

---

### Test 7: Memory Stress Test
**Description:** Memory stress testing using DGDiag and XPU-SMI.

**Parameters:**
- `-inst STR` : GPU device selector. Supported forms: single (`0`), range (`0-3`), list (`0,1,2,3`), mixed (`0-3,5,7-8`), `-1` for all devices (default: -1)
- `-testtime INT` : Total monitoring time in seconds (default: 60)
- `-stime INT` : Sampling time interval in seconds (default: 0)

**Note:** DGDiag-backed tests (7/8/9/10) accept both 0-based card IDs (`0-2`) and DGDiag instance IDs (`1-3`).

**Example:**
```bash
sudo python3 start_vts.py -tn 7 -inst 0 -testtime 120
```

---

### Test 8: Power and Thermal Stress Test
**Description:** Enhanced power and thermal validation under stress conditions using DGDiag tools with timestamp-based filtering for accurate measurement windows, realistic value validation (1-2000W power, 10-200°C temperatures), and thermal throttling detection using 'Throttle reason' analysis.

**Parameters:**
- `-inst STR` : GPU device selector. Supported forms: single (`0`), range (`0-3`), list (`0,1,2,3`), mixed (`0-3,5,7-8`), `-1` for all devices (default: -1)
- `-testtime INT` : Test duration in seconds (default: 300)
- `-cs STR` : CPU stress tool: None, stress-ng, ptat (default: stress-ng)

**Example:**
```bash  
sudo python3 start_vts.py -tn 8 -testtime 600 -cs stress-ng
```

---

### Test 9: Excursion Design Power Stress Test
**Description:** Enhanced EDP pulse stress testing using DGDiag PulseStress tool with adaptive pulse detection algorithms, 90% tolerance validation, and dual baseline strategies for varying active/idle ratios.

**Parameters:**
- `-inst STR` : GPU device selector. Supported forms: single (`0`), range (`0-3`), list (`0,1,2,3`), mixed (`0-3,5,7-8`), `-1` for all devices (default: -1)
- `-testtime INT` : Test duration in seconds (default: 300)
- `-at INT` : Active time for stress cycles in seconds (default: 3)
- `-it INT` : Idle time between stress cycles in seconds (default: 3)
- `-cs STR` : CPU stress tool: None, stress-ng, ptat (default: stress-ng)

**Example:**
```bash
sudo python3 start_vts.py -tn 9 -testtime 600 -at 5 -it 2
```

---

### Test 10: Functional Test
**Description:** Core GPU functionality testing with comprehensive hardware validation.

**Parameters:**
- `-inst STR` : GPU device selector. Supported forms: single (`0`), range (`0-3`), list (`0,1,2,3`), mixed (`0-3,5,7-8`), `-1` for all devices (default: -1)

**Status:** Not yet available

**Example:** 
```bash
sudo python3 start_vts.py -tn 10 -inst 0
```

---
### Test 11: vLLM Test
**Description:** Large Language Model inference testing with vLLM using Docker containers. Runs AI workload benchmarks to validate GPU performance with machine learning inference tasks.

**Parameters:**
- `--model STR` : Model to serve (default: None; interactive mode defaults to meta-llama/Meta-Llama-3-8B)
- `--tp INT` : Tensor parallel size (default: 1)
- `--max-concurrency INT` : Maximum concurrency (default: 16)
- `--input-len INT` : Input length (default: 128)
- `--output-len INT` : Output length (default: 128)
- `--enforce-eager BOOL` : Force eager execution (default: None)
- `--host STR` : Host address to bind (default: None)
- `--port INT` : Port to serve on (default: None)
- `--trust-remote-code BOOL` : Trust remote model code (default: None)
- `--gpu-memory-util FLOAT` : GPU memory utilization ratio (default: None)
- `--enable-expert-parallel BOOL` : Enable expert parallelism (default: None)
- `--docker-user STR` : Docker user name (default: '')
- `--docker-token STR` : Docker token (default: '')
- `--docker-image STR` : Docker Image (default: '')
- `--http-proxy STR` : HTTP proxy server details (default: None)
- `--https-proxy STR` : HTTPS proxy server details (default: None)
- `--no-proxy STR` : No proxy server details (default: None)
- `--hf-token STR` : Huggingface user token for gated model access (default: None)
- `--disable-sliding-window BOOL` : Disable sliding window feature (default: None)
- `--quantization STR` : Quantization type (default: None)
- `--enable-prefix-caching BOOL` : Enable prefix caching (default: None)
- `--enable-log-requests BOOL` : Enable request logging (default: None)
- `--dataset-type STR` : Dataset type for benchmarking (default: None)
- `--max-model-len INT` : Maximum model length (default: None)
- `--max-num-batched-tokens INT` : Maximum number of batched tokens (default: None)
- `--block-size INT` : KV cache block size (default: None)
- `--dtype STR` : Model data type (default: None)
- `--data-parallel-size INT` : Data parallel size (default: None)

**Configuration Note:** 
Ensure that DOCKER, HUGGINGFACE, and PROXY settings are properly updated in the `.env` file at the workspace root before proceeding.

**Example:**
```bash
sudo python3 start_vts.py -tn 11 --model meta-llama/Meta-Llama-3-8B --tp 2 --max-concurrency 32 --input-len 128 --output-len 128 --hf-token <YOUR_TOKEN>
```

---

### Test 12: ML Perf Test
**Description:** MLPerf benchmark testing for machine learning inference performance validation using standardized MLCommons benchmarks. This test runs LLM inference workloads (Llama 3.1-8B) inside a Docker container with Intel-optimized PyTorch on Intel GPUs. It automates the full pipeline including Docker image validation, dataset downloading and integrity verification (SHA-256), model downloading from HuggingFace, INT4 weight calibration (AutoRound), and benchmark execution with structured result parsing.

**Download-Only Mode:**
When `--download_model` is specified, the test operates in download-only mode: it downloads the specified gated HuggingFace model using an isolated Python venv and then exits. The benchmark pipeline below is **not** executed. This is useful for pre-staging models before running the benchmark in a separate invocation. Downloads are resumable; re-running the command will skip already-downloaded files.

**Execution Flow (Benchmark Mode):**
Without `--download_model`, the test executes the full benchmark pipeline in the following sequential phases:
1. **Docker Image Validation**: Verifies the required Docker image is available locally; auto-pulls if missing (proxy-aware).
2. **Dataset & Model Validation**: Validates dataset files (with SHA-256 hash verification) and model files. Auto-downloads missing datasets via `mlc-r2-downloader` for Llama 3.1-8B for instance.
3. **Docker Container Launch**: Launches a privileged Docker container with GPU device passthrough (`/dev/dri`), volume mounts for data/model/logs, and proxy environment variables.
4. **Model Calibration Check**: Verifies calibrated model files exist in the container. If missing, automatically runs `scripts/run_calibration.sh` (INT4 quantization via AutoRound, ~30+ minutes).
5. **Container Patches**: Generates scaled `user.conf` files for 1x/2x GPU configurations from the 4x GPU baseline, and applies compatibility patches.
6. **Benchmark Execution**: Runs the MLPerf inference benchmark (`run_mlperf.sh`) with the configured scenario and mode.
7. **Results Parsing**: Parses MLPerf output for samples/s, tokens/s, latency statistics, and VALID/INVALID result determination.

**Parameters:**
- `-y {True,False}` : (Global) Auto-confirm all interactive prompts, including configuration confirmation and post-test prompts. Required for unattended/automated execution (default: `False`)
- `--download_model STR` : Download a HuggingFace model before running the test. Accepts model aliases (e.g. `llama3.1-8b` resolves to `meta-llama/Llama-3.1-8B-Instruct`) or a full HuggingFace repo ID. Leave empty to skip. When specified, only the download is performed and the benchmark is skipped.
- `--hf_token STR` : HuggingFace access token, required for downloading gated models. Can also be set via the `HF_TOKEN` environment variable.
- `--docker_image STR` : Docker image to use for MLPerf testing (default: `intel/intel-optimized-pytorch:mlperf-inference-6.0-llama_xpu`)
- `--data_dir STR` : Path to dataset directory on the host, mounted as `/data` inside the container (default: `/home/{user}/data`)
- `--model_dir STR` : Path to model directory on the host, mounted as `/model` inside the container (default: `/home/{user}/model/.llama/checkpoints`)
- `--model_type STR` : Model type to benchmark (default: `llama3_1-8b`)
  - Options: `llama3_1-8b`, `llama2-70b`, `gpt-oss`, `whisper`
- `--scenario STR` : MLPerf benchmark scenario (default: `Offline`)
  - Options: `Offline`, `Server`, `SingleStream`
- `--mode STR` : Benchmark mode (default: `Performance`)
  - Options: `Performance`, `Accuracy`

**Configuration File:**
Default values for Docker image, paths, proxy settings, calibration scripts, and dataset URLs can be customized in `gpu/bmg/tests/ai_wl/config.env` without modifying Python code. Key settings include:
- `DOCKER_IMAGE` — Default Docker image
- `CONTAINER_NAME` — Docker container name (default: `mlperf_benchmark_container`)
- `DATA_DIR`, `MODEL_DIR`, `LOG_DIR` — Default host paths (`{user}` is auto-expanded)
- `HTTP_PROXY`, `HTTPS_PROXY` — Corporate proxy settings (passed to Docker and subprocesses)
- `CALIBRATION_SCRIPT` — Calibration command run inside the container
- `BENCHMARK_SCRIPT` — Default benchmark command template

**Prerequisites:**
- Docker installed and running (`docker --version`)
- Intel GPU with `/dev/dri` device accessible
- Sufficient disk space for model and dataset files (~16 GB for Llama 3.1-8B model + datasets)
- Network access to pull Docker images and download datasets/models (proxy settings in `config.env` if behind a corporate firewall)
- For gated models: a valid HuggingFace token with model access permissions

**Results & Pass/Fail Criteria:**
- **PASS**: Benchmark returns exit code 0 and MLPerf output contains `Result is : VALID`
- **FAIL**: Non-zero exit code, `Result is : INVALID`, or setup/calibration failure
- Output includes: samples/s, tokens/s, latency statistics (min/max/mean/percentiles), and MLPerf validation checks (min duration, min queries, early stopping)
- Benchmark logs are saved to `{log_dir}/mlperf_benchmark_{timestamp}.log`

**Examples:**

Run Offline Performance benchmark with Llama 3.1-8B (model and dataset already downloaded):
```bash
sudo python3 start_vts.py -tn 12 --scenario Offline --mode Performance --model_type llama3_1-8b
```

Run in fully automated mode (no interactive prompts):
```bash
sudo python3 start_vts.py -tn 12 -y True --scenario Offline --mode Performance --model_type llama3_1-8b
```

Download model first, then run benchmark in a separate invocation:
```bash
# Step 1: Download the model (benchmark is skipped)
sudo python3 start_vts.py -tn 12 --download_model llama3.1-8b --hf_token <YOUR_TOKEN>

# Step 2: Run the benchmark
sudo python3 start_vts.py -tn 12 --scenario Offline --mode Performance --model_type llama3_1-8b
```

Run Accuracy mode with Llama 2-70B:
```bash
sudo python3 start_vts.py -tn 12 --scenario Offline --mode Accuracy --model_type llama2-70b
```

Run with custom data and model directories:
```bash
sudo python3 start_vts.py -tn 12 --scenario Server --mode Performance --data_dir /mnt/datasets/mlperf --model_dir /mnt/models/llama
```

**Notes:**
- The Docker container remains running after the test completes. Access it using the container name configured in `config.env` (the value of `CONTAINER_NAME`), for example: `docker exec -it $CONTAINER_NAME /bin/bash` (default: `mlperf_benchmark_container`).
- Model calibration is a one-time operation; subsequent runs will skip it if calibrated files are detected.
- The `--download_model` flag creates an isolated Python venv at `/tmp/vts_hf_venv` for HuggingFace CLI — the system Python environment is not modified.
- Downloads (model and dataset) are resumable; re-running the command will skip already-downloaded files.
- Monitor type (`-mt`) is supported for this test (default: `xpum`).
- Use `-y True` for unattended execution in automation pipelines or JSON config files (`"y": true`). This skips the configuration confirmation prompt and the post-test "Run another test?" prompt.

---

### Test 13: Collective Communication Test
**Description:** OneCCL collective communications testing using Docker containers with torchrun for multi-GPU distributed operations. Supports standard collective operations (allreduce, reduce, allgather, reduce_scatter) with configurable element counts and data types.

**Parameters:**
- `-l, --coll STR` : Collective operation(s): allreduce, reduce, allgather, reduce_scatter, all (default: all)
- `-g, --gpus INT` : Number of GPUs/processes for torchrun (0 = auto-detect) (default: 0)
- `-i, --iters INT` : Number of measured benchmark iterations (default: 50)
- `-w, --warmup-iters INT` : Number of warmup iterations before measurements (default: 20)
- `-f, --min-elem-count INT` : Minimum element count to benchmark (default: 16)
- `-t, --max-elem-count INT` : Maximum element count to benchmark (default: 1024000000)
- `-d, --dtype STR` : Data type(s): float16, float32, float64, all (default: float16)
- `-o, --csv-filepath STR` : CSV output path for benchmark results (default: '')
- `-j, --json-filepath STR` : JSON output path for benchmark results (default: off)
- `-v, --verbosity INT` : Benchmark verbosity level (default: 0)
- `-u, --docker-user STR` : Docker user name (default: '')
- `-k, --docker-token STR` : Docker token (default: '')
- `-m, --docker-image STR` : Docker image override (default: '')
- `--http-proxy STR` : HTTP proxy server details (default: None)
- `--https-proxy STR` : HTTPS proxy server details (default: None)
- `--no-proxy STR` : No-proxy server details (default: None)

**Note:** The `-d` flag in this test overrides the global debug collection flag and instead specifies the data type.

**Example:**
```bash
sudo python3 start_vts.py -tn 13 -l allreduce -d float32 -g 4
```

---

### Test 14: P2P Bandwidth Test (ze_peer)
**Description:** Peer-to-peer bandwidth and latency testing between GPUs using the Level Zero ze_peer tool. Supports topology-aware pass/fail thresholds with automatic PCIe configuration discovery and link derating based on hop type (NODE, SYS, MDF).

**Parameters:**
- `-t STR` : Type of measurement: transfer_bw, latency, all (default: transfer_bw)
- `-o STR` : P2P operation: read, write, all (default: all)
- `-b STR` : Bidirectional mode: true, false, all (default: false)
- `-i INT` : Number of test iterations (default: 50)
- `-z INT` : Transfer size in bytes (default: 268435456)
- `-s STR` : Comma-separated source device indices, -1 for all detected GPUs (default: -1)
- `-d STR` : Comma-separated destination device indices, -1 for all detected GPUs (default: -1)
- `-u STR` : Engine index to use, empty for default (default: '')
- `-parallel_mode STR` : Parallel test mode: none, parallel_single_target, parallel_multiple_targets (default: none)

**Note:** The `-d` flag in this test overrides the global debug collection flag and instead specifies destination device indices.

**Example:**
```bash
sudo python3 start_vts.py -tn 14 -t transfer_bw -o read -s 0 -d 1
```

---

## Configuration File Format

JSON configuration files allow batch test execution with predefined parameters.

**Example configuration:**
```json
[
    {
        "comment": "GPU Health Check",
        "tn": 1
    },
    {
        "comment": "GPU Environment Check - quick diagnostic level",
        "tn": 2,
        "l": "quick"
    },
    {
        "comment": "PCIe bandwidth test - all tools, parallel mode",
        "tn": 3,
        "mode": "parallel",
        "tool": "all",
        "iterations": 100,
        "d": true
    },
    {
        "comment": "PCIe Lane Margin Test - multiple receivers",
        "tn": 4,
        "n": 2,
        "rn": [1, 2, 4]
    },
    {
        "comment": "Reset test - link retrain with multiple iterations", 
        "tn": 5,
        "rt": "retrain",
        "iterations": 10,
        "d": true
    },
    {
        "comment": "Enhanced power and thermal stress test with timestamp filtering",
        "tn": 8, 
        "testtime": 600,
        "cs": "stress-ng",
        "d": true
    },
    {
        "comment": "EDP stress test with adaptive pulse detection and custom timing",
        "tn": 9,
        "testtime": 300,
        "at": 5,
        "it": 2,
        "d": true
    }
]
```

**Usage:**
```bash
sudo python3 start_vts.py -tc config.json
```

### Configuration Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `comment` | string | No | Human-readable description of the test step |
| `tn` | int or `"c"` | **Yes** | Test number to run, or `"c"` to collect logs |
| `cs` | string | No | CPU stress tool: `"None"`, `"stress-ng"`, `"ptat"` |
| `mt` | string | No | Monitor type: `"xpum"`, `"dgdiag"`, `"None"` |
| `rep` | int | No | Number of times to repeat the test (default: 1) |
| `d` | bool | No | Collect debug logs after test execution |
| `level` | string | No | Diagnostic level for test 2: `"quick"`, `"medium"`, `"long"` |
| `rt` | string | No | Reset type for test 5: `"flr"`, `"sbr"`, `"retrain"`, `"warm"`, etc. |
| `iterations` | int | No | Iteration count (tests 3, 5) |
| `testtime` | int | No | Test duration in seconds (tests 7, 8, 9) |
| `rn` | int or list | No | Receiver number(s) for PCIe lane margin test (test 4) |
| `n` | int | No | Number of receivers for lane margin test (test 4) |
| `mode` | string | No | Execution mode for bandwidth test: `"parallel"`, `"serial"` |
| `tool` | string | No | Tool selection for bandwidth test: `"all"`, specific tool name |
| `at` | int | No | Adaptive timing parameter for EDP stress test (test 9) |
| `it` | int | No | Interval timing parameter for EDP stress test (test 9) |
| `test_type` | string | No | P2P measurement type: `"transfer_bw"`, `"latency"`, `"all"` (test 14) |
| `operation` | string | No | P2P operation: `"read"`, `"write"`, `"all"` (test 14) |
| `bidir` | string | No | Bidirectional mode: `"true"`, `"false"`, `"all"` (test 14) |
| `src` | string | No | Source device indices, comma-separated or `"-1"` for all (test 14) |
| `dst` | string | No | Destination device indices, comma-separated or `"-1"` for all (test 14) |
| `pcie_downgrade` | bool | No | Disable PCIe downgrade before test execution (tests 4, 5) |
| `stop_on_error` | bool | No | Stop execution on first test failure |
| `y` | bool | No | Auto-confirm all interactive prompts (bypasses EOM warnings, system check warnings, etc.) |

---

## Reboot Resume

VTS automatically saves and resumes execution state across system reboots, which is required for reset tests that involve warm, cold, or soft resets.

**How it works:**
1. Before a reboot-type reset, VTS installs a systemd service (`vts-resume.service`) and saves execution state to `/var/lib/vts/resume_state.json`
2. After reboot, the service runs in the background to finalize the reset test result
3. On the next root login, a login hook displays the result and resumes VTS in the original execution mode

**Supported modes:**
- **Menu mode**: Re-launches the interactive test menu after reboot
- **Config file / Run All**: Continues with remaining tests from the sequence
- **CLI mode**: Displays the test result summary

All artifacts (state file, systemd service, login hook) are automatically cleaned up after resumption. No manual intervention is required.
