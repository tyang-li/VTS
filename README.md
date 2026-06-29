# Intel® GPU Verification Test Suite

## Overview
This test suite provides comprehensive validation tests for Intel GPUs, focusing on functionality, performance, and stability testing. 

## Usage
The validated usage mode is running the test suite as user with sudo privileges. Highly recommended to run with sudo -E for keeping environment available during tests execution.

### Interactive Mode
```bash
sudo -E python3 start_vts.py
```
Launches an interactive menu with all available tests.

### Command Line Mode
```bash
sudo -E python3 start_vts.py -tn TESTNUMBER [OPTIONS]
```

### Configuration File Mode
```bash
sudo -E python3 start_vts.py -tc CONFIG_FILE
```

## Global Arguments
- `-h, --help` : Show help message
- `-tn` : Test Number (int or 'c' or 'd' or 'a')
- `-tc` : JSON file test config path
- `-rep` : Number of repetitions to run the test (default: 1)
- `-mt {xpum,dgdiag,None}` : Monitor type (default: xpum)
- `-cs {None,stress-ng,ptat}` : CPU stress tool to run in parallel (default: None)
- `-pcie_downgrade {True,False}` : Disable PCIe downgrade before test execution (default: False, True for LMT and Reset tests)
- `-d {True,False}` : Collect debug logs after each test execution (default: False)
- `-stop_on_error {True,False}` : Stop execution on first test failure and wait for user debug (default: False)
- `-live_mon {True,False}` : Enable the GPU usage and temperature live monitor dialog (default: False)

---

### Test 1: GPU Health Check
**Description:** Performs basic GPU health validation using XPUM health checks.

**Parameters:** None

**Example:**
```bash
sudo -E python3 start_vts.py -tn 1
```

---

### Test 2: GPU Environment and Device Check  
**Description:** Comprehensive GPU diagnostics using XPUM diagnostic tools.

**Parameters:**
- `-l STR` : Diagnostic level: quick, medium, long (default: long)

**Example:**
```bash
sudo -E python3 start_vts.py -tn 2 -l medium
```

---

### Test 3: PCIe Bandwidth Test
**Description:** Advanced PCIe bandwidth testing with multiple tools and modes.

**Parameters:**
- `-inst INT` : GPU device instance, -1 for all devices (default: -1)
- `-mode STR` : Execution mode: serial, parallel, all (default: all)  
- `-dir STR` : Traffic direction: h2d, d2h, bidirectional, all (default: all)
- `-engine STR` : Traffic engine: copy, compute, all (default: all)
- `-tool STR` : Bandwidth test tool: ze_bandwidth, memory_benchmark_l0, all (default: all)
- `-iterations INT` : Number of test iterations (default: 500)
- `-size INT` : Buffer size in bytes (default: 268435456)

**Example:**
```bash
sudo -E python3 start_vts.py -tn 3 -inst 0 -mode parallel -tool ze_bandwidth
```

---

### Test 4: PCIe Lane Margin Test (LMT)
**Description:** PCIe lane margin testing with automatic EOM detection and PCIe downgrade disable functionality.

**Parameters:**
- `-n INT` : Number of repeats (default: 1)
- `-rn INT` : Receiver Number(s) to be tested on (1-6). Multiple space-separated values are accepted. (default: 6)

**Notes:**
- This test requires Python 3.12 to run properly

**Example:**
```bash
sudo -E python3.12 start_vts.py -tn 4 -n 3 -rn 1 2 4
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
sudo -E python3 start_vts.py -tn 5 -rt retrain -iterations 5
```

---

### Test 6: Memory Bandwidth Test  
**Description:** Memory bandwidth validation using XPUM tools.

**Parameters:**
- `-inst INT` : GPU device instance, -1 for all devices (default: -1)

**Example:**
```bash
sudo -E python3 start_vts.py -tn 6 -inst 1
```

---

### Test 7: Memory Stress Test
**Description:** Memory stress testing using DGDiag and XPU-SMI.

**Parameters:**
- `-inst INT` : GPU device instance, -1 for all devices (default: -1) 
- `-testtime INT` : Total monitoring time in seconds (default: 60)
- `-stime INT` : Sampling time interval in seconds (default: 0)

**Example:**
```bash
sudo -E python3 start_vts.py -tn 7 -inst 0 -testtime 120
```

---

### Test 8: Power and Thermal Stress Test
**Description:** Enhanced power and thermal validation under stress conditions using DGDiag tools with timestamp-based filtering for accurate measurement windows, realistic value validation (1-2000W power, 10-200°C temperatures), and thermal throttling detection using 'Throttle reason' analysis.

**Parameters:**
- `-inst INT` : GPU device instance, -1 for all devices (default: -1)
- `-testtime INT` : Test duration in seconds (default: 300)
- `-cs STR` : CPU stress tool: None, stress-ng, ptat (default: stress-ng)

**Example:**
```bash  
sudo -E python3 start_vts.py -tn 8 -testtime 600 -cs stress-ng
```

---

### Test 9: Excursion Design Power Stress Test
**Description:** Enhanced EDP pulse stress testing using DGDiag PulseStress tool with adaptive pulse detection algorithms, 90% tolerance validation, and dual baseline strategies for varying active/idle ratios.

**Parameters:**
- `-inst INT` : GPU device instance, -1 for all devices (default: -1)
- `-testtime INT` : Test duration in seconds (default: 300)
- `-at INT` : Active time for stress cycles in seconds (default: 3)
- `-it INT` : Idle time between stress cycles in seconds (default: 3)
- `-cs STR` : CPU stress tool: None, stress-ng, ptat (default: stress-ng)

**Example:**
```bash
sudo -E python3 start_vts.py -tn 9 -testtime 600 -at 5 -it 2
```

---

### Test 10: Functional Test
**Description:** Core GPU functionality testing with comprehensive hardware validation.

**Parameters:**
- `-inst INT` : GPU device instance, -1 for all devices (default: -1)

**Status:** Implementation in progress

**Example:** 
```bash
sudo -E python3 start_vts.py -tn 10 -inst 0
```

---
### Test 11: vLLM Test
**Description:** Large Language Model inference testing with vLLM using Docker containers. Runs AI workload benchmarks to validate GPU performance with machine learning inference tasks.

**Parameters:**
- `--model STR` : Model to serve (default: None)
- `--tp INT` : Tensor parallel size (default: None)
- `--max-concurrency INT` : Maximum concurrency (default: None)
- `--input-len INT` : Input length (default: None)
- `--output-len INT` : Output length (default: None)
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
- `--disable-log-requests BOOL` : Disable request logging (default: None)
- `--dataset-type STR` : Dataset type for benchmarking (default: None)
- `--max-model-len INT` : Maximum model length (default: None)
- `--max-num-batched-tokens INT` : Maximum number of batched tokens (default: None)
- `--block-size INT` : KV cache block size (default: None)
- `--dtype STR` : Model data type (default: None)
- `--data-parallel-size INT` : Data parallel size (default: None)

**Configuration Note:** 
Ensure that DOCKER, HUGGINGFACE, and PROXY settings are properly updated in the `gpu/bmg/tests/ai_wl/.env` file before proceeding.

**Example:**
```bash
sudo -E python3 start_vts.py -tn 11 --model meta-llama/Meta-Llama-3-8B --tp 2 --max-concurrency 32 --input-len 128 --output-len 128 --hf-token <YOUR_TOKEN>
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
- `-y {True,False}` : Auto-confirm all interactive prompts, including configuration confirmation and post-test prompts. Required for unattended/automated execution (default: `False`)
- `--download_model STR` : Download a HuggingFace model before running the test. Accepts model aliases (e.g. `llama3.1-8b` resolves to `meta-llama/Llama-3.1-8B-Instruct`) or a full HuggingFace repo ID. Leave empty to skip. When specified, only the download is performed and the benchmark is skipped.
- `--hf_token STR` : HuggingFace access token, required for downloading gated models. Can also be set via the `HF_TOKEN` environment variable.
- `--docker_image STR` : Docker image to use for MLPerf testing (default: `intel/intel-optimized-pytorch:mlperf-inference-6.0-llama_xpu`)
- `--data_dir STR` : Path to dataset directory on the host, mounted as `/data` inside the container (default: `/home/{user}/data`)
- `--model_dir STR` : Path to model directory on the host, mounted as `/model` inside the container (default: `/home/{user}/model/.llama/checkpoints`)
- `--model_type STR` : Model type to benchmark (default: `llama3_1-8b`)
  - Options: `llama3_1-8b`, `llama2-70b`
- `--scenario STR` : MLPerf benchmark scenario (default: `Offline`)
  - Options: `Offline`, `Server`
- `--mode STR` : Benchmark mode (default: `Performance`)
  - Options: `Performance`, `Accuracy`, `Compliance`

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
sudo -E python3 start_vts.py -tn 12 --scenario Offline --mode Performance --model_type llama3_1-8b
```

Run in fully automated mode (no interactive prompts):
```bash
sudo -E python3 start_vts.py -tn 12 -y True --scenario Offline --mode Performance --model_type llama3_1-8b
```

Download model first, then run benchmark in a separate invocation:
```bash
# Step 1: Download the model (benchmark is skipped)
sudo -E python3 start_vts.py -tn 12 --download_model llama3.1-8b --hf_token <YOUR_TOKEN>

# Step 2: Run the benchmark
sudo -E python3 start_vts.py -tn 12 --scenario Offline --mode Performance --model_type llama3_1-8b
```

Run Accuracy mode with Llama 2-70B:
```bash
sudo -E python3 start_vts.py -tn 12 --scenario Offline --mode Accuracy --model_type llama2-70b
```

Run with custom data and model directories:
```bash
sudo -E python3 start_vts.py -tn 12 --scenario Server --mode Performance --data_dir /mnt/datasets/mlperf --model_dir /mnt/models/llama
```

**Notes:**
- The Docker container remains running after the test completes. Access it using the container name configured in `config.env` (the value of `CONTAINER_NAME`), for example: `docker exec -it $CONTAINER_NAME /bin/bash` (default: `mlperf_benchmark_container`).
- Model calibration is a one-time operation; subsequent runs will skip it if calibrated files are detected.
- The `--download_model` flag creates an isolated Python venv at `/tmp/vts_hf_venv` for HuggingFace CLI — the system Python environment is not modified.
- Downloads (model and dataset) are resumable; re-running the command will skip already-downloaded files.
- Monitor type (`-mt`) is supported for this test (default: `xpum`).
- Use `-y True` for unattended execution in automation pipelines or JSON config files (`"y": true`). This skips the configuration confirmation prompt and the post-test "Run another test?" prompt.

---

### Test 13: OneCCL Collective Test (Not Available Yet)  
**Description:** OneCCL collective communications testing.

**Status:** Implementation in progress

---

### Test 14: OneCCL Point to Point Test (Not Available Yet)
**Description:** OneCCL point-to-point communications testing.

**Status:** Implementation in progress

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
sudo -E python3 start_vts.py -tc config.json
```

### Configuration Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `comment` | string | No | Human-readable description of the test step |
| `tn` | int or `"c"` | **Yes** | Test number to run, or `"c"` to collect logs |
| `cs` | string | No | CPU stress tool: `"None"`, `"stress-ng"`, `"ptat"` |
| `mt` | string | No | Monitor type: `"xpum"`, `"dgdiag"`, `"None"` |
| `repetitions` | int | No | Number of times to repeat the test (default: 1) |
| `debug_collection` | bool | No | Collect debug logs after test execution |
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
| `d` | bool | No | Alias for `debug_collection` |

