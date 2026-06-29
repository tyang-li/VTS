# Copyright (C) 2024-2026 Intel Corporation
# Verification Test Suite
# AI VLLM Test

import json
import os
import sys
import time
import shlex
import subprocess # nosec
import requests
from typing import Optional
from dotenv import dotenv_values
from common.deviceManager import DeviceManager
import re
import threading
import traceback
from datetime import datetime
import selectors

from  common.dockerManager import DockerManager
from .ai_wl_base import aiwlBase
from  common.utils import Spinner

BOOL_MAP = {"true": True, "false": False, "True":True, "False":False}
class VLLMServer(DockerManager):

    def __init__(
        self,
        _logger,
        env_vars,
        docker_image,
        service_name, 
        debug_mode=True,
        reuse_containers=False
   
    ):
        try:
            self.env_vars = env_vars
            self.logger = _logger
            self.execution_start_time = time.time()

            # Endpoints
            self.model_name = env_vars.get("MODEL", "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B")
            self.dtype = env_vars.get("DTYPE")
            self.port = env_vars.get("PORT")
            self.docker_user = env_vars.get("DOCKER_USER")
            self.docker_token = env_vars.get("DOCKER_TOKEN")
            self.hf_token = env_vars.get("HUGGING_FACE_HUB_TOKEN")     
            if not self.hf_token:
                raise EnvironmentError(
                    "HUGGING_FACE_HUB_TOKEN is not set or is empty. "
                    "Please update .env file or set as argument --hf-token"
                )
            self.port = env_vars.get("PORT")
            self.host = env_vars.get("HOST")
            self.gpu_memory_util = env_vars.get("GPU_MEMORY_UTIL")
            self.enable_prefix_caching = env_vars.get("ENABLE_PREFIX_CACHING")
            self.max_num_batched_tokens = env_vars.get("MAX_NUM_BATCHED_TOKENS")
            self.disable_log_requests = env_vars.get("DISABLE_LOG_REQUESTS")
            self.max_model_len = env_vars.get("MAX_MODEL_LEN")
            self.max_concurrency = env_vars.get("MAX_CONCURRENCY")
            self.block_size = env_vars.get("BLOCK_SIZE")
            self.quantization = env_vars.get("QUANTIZATION")
            if not isinstance(env_vars.get("TRUST_REMOTE_CODE"), bool):  
                self.trust_remote_code = BOOL_MAP[env_vars.get("TRUST_REMOTE_CODE").strip().lower()]
            else:
                self.trust_remote_code =  env_vars.get("TRUST_REMOTE_CODE")

            if not isinstance(env_vars.get("ENFORCE_EAGER"), bool):  
                self.enforce_eager = BOOL_MAP[env_vars.get("ENFORCE_EAGER").strip().lower()]
            else:
                self.enforce_eager =  env_vars.get("ENFORCE_EAGER")

            if not isinstance(env_vars.get("DISABLE_SLIDING_WINDOW"), bool):  
                self.disable_sliding_window = BOOL_MAP[env_vars.get("DISABLE_SLIDING_WINDOW").strip().lower()]
            else:
                self.disable_sliding_window =  env_vars.get("DISABLE_SLIDING_WINDOW")

            self.base_url = f"http://{self.host}:{self.port}"
            self.health_url = f"http://{self.host}:{self.port}/health"
            self.models_url = f"http://{self.host}:{self.port}/v1/models"
            self.completions_url = f"http://{self.host}:{self.port}/v1/completions"

            self.auto_start = False
            self.server_ready_timeout_secs = 90
            self.log_file_path = "/tmp/vllm-server.log"
            self.container_workdir = "/workspace/vllm"
            self.curl_timeout_secs = 15
            self.ready_patterns = [
                r"Application startup complete",
                r"Uvicorn running on",
                r"Ready to serve requests",
                r"vLLM API server started",
                r"Server is ready",
            ]

            # vLLM flags
        
            self.extra_env = [
                "CCL_ZE_IPC_EXCHANGE=pidfd",
                "VLLM_ALLOW_LONG_MAX_MODEL_LEN=1",
                "VLLM_WORKER_MULTIPROC_METHOD=spawn",
            ]

            self.device_manager = DeviceManager(self.logger)
            self.device_manager.discover_devices()
            self.tensor_parallel =  env_vars.get("TENSOR_PARALLEL_SIZE", self.device_manager.gpu_num)

            self.proxy_vars = [
                "HTTP_PROXY", "http_proxy",
                "HTTPS_PROXY", "https_proxy",
                "FTP_PROXY", "ftp_proxy",
                "NO_PROXY", "no_proxy",
                "ALL_PROXY", "all_proxy",
                "HUGGING_FACE_HUB_TOKEN", f"{self.hf_token}"
            ]
            super().__init__(_logger, self.docker_user, self.docker_token, env_vars, docker_image, service_name,  debug_mode, reuse_containers)
        except Exception as e:
            self.logger.error(f"Error in VLLMServer init: {e}")
            traceback.print_exc()
            raise
    def clean_ansi_codes(self, text):
        """Remove ANSI color codes and control characters."""
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', text)

    def _proxy_exports(self):
        exports = []
        sensitive_keys = {"HUGGING_FACE_HUB_TOKEN", "DOCKER_TOKEN"}
        for var in self.proxy_vars:
            if var in self.env_vars and self.env_vars[var]:
                exports.append(f"export {var}={shlex.quote(self.env_vars[var])}")
                if var in sensitive_keys:
                    self.logger.info(f" Setting proxy: {var}=***")
                else:
                    self.logger.info(f" Setting proxy: {var}={self.env_vars[var]}")
        return exports

    def server_launch_command(self):
        base_cmd = [
            *self.extra_env,
            "python3", "-m", "vllm.entrypoints.openai.api_server",
            "--model", self.model_name,
            f"--dtype={self.dtype}",
            "--port", str(self.port),
            "--host", self.host,
            "--gpu-memory-util", str(self.gpu_memory_util),
            "--max-num-batched-tokens", str(self.max_num_batched_tokens),
            "--max-model-len", str(self.max_model_len),
            "--block-size", str(self.block_size),
            f"--quantization={self.quantization}",
            f"-tp={self.tensor_parallel}"
        ]
        if self.trust_remote_code:
            base_cmd.append("--trust-remote-code")
        if self.enforce_eager:
            base_cmd.append("--enforce-eager")
        if self.disable_sliding_window:
            base_cmd.append("--disable-sliding-window")
        if not self.enable_prefix_caching:
            base_cmd.append("--no-enable-prefix-caching")
        if self.disable_log_requests:
            base_cmd.append("--disable-log-requests")

        self.logger.info(f"Base command:{base_cmd}")
        # Combine to shell string and tee logs
        joined = " ".join(base_cmd)
        cmd_with_logging = f"{joined} 2>&1 | tee -a {self.log_file_path}"

        proxy_exports = self._proxy_exports()
        if proxy_exports:
            return f"{' && '.join(proxy_exports)} && {cmd_with_logging}"
        return cmd_with_logging
   
    def wait_for_server_ready(self, timeout_secs=None):
        timeout = int(timeout_secs or self.server_ready_timeout_secs)
        self.logger.info(
            f"Waiting for server HTTP readiness at {self.models_url} (timeout={timeout}s) ..."
        )

        # Store the latest probe result here (JSON/dict).
        # You can also keep a list of attempts if you want (see note below).
        self.server_ready_probe_json = {}

        start_ts = time.time()
        attempt = 0

        while time.time() - start_ts < timeout:
            attempt += 1
            ts = time.time()

            # Ask curl to print BOTH the body and the HTTP status code.
            # -sS          : silent but show errors
            # --max-time 5 : don't hang forever
            # -w '\n%{http_code}' : put status code on its own line at end
            curl_cmd = (
                f"curl -sS --max-time 5 -w '\\n%{{http_code}}' {self.models_url} || true"
            )

            cmd = ["docker", "exec", self.service_name, "bash", "-lc", curl_cmd]
            result = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )

            raw = (result.stdout or "").strip()

            # Parse: last line is HTTP code, rest is body (if any).
            body, code = "", ""
            if raw:
                parts = raw.splitlines()
                code = parts[-1].strip()
                body = "\n".join(parts[:-1]).strip()
            else:
                code = ""
                body = ""

            # Try to parse body as JSON (if it is JSON). Otherwise keep as string.
            body_json = None
            if body:
                try:
                    body_json = json.loads(body)
                except Exception:
                    body_json = None

            # ---- JSON variable containing the probe result ----
            self.server_ready_probe_json = {
                "attempt": attempt,
                "timestamp": ts,
                "url": self.models_url,
                "docker_cmd": cmd,                 # what we executed
                "returncode": result.returncode,   # docker exec return code
                "http_code": code,                 # "200", "403", etc.
                "raw_output": raw,                 # full curl output (body + code)
                "body_text": body,                 # body as text
                "body_json": body_json,            # parsed JSON if possible, else None
            }

            # Optional: log a compact view
            self.logger.info(
                f"Probe attempt={attempt} http_code={code} returncode={result.returncode}"
            )
            self.logger.info(f"server_ready_probe_json : {self.server_ready_probe_json}")

            if code == "200":
                self.logger.info("Server is HTTP-ready (200).")
                return True

            self.logger.info(f"Not ready yet (HTTP {code}).")
            time.sleep(3)

        self.logger.error("Server HTTP readiness timed out.")
        return False

    def exec_in_container_interactive(self, cmd, on_ready=None):
        self.logger.info(f"Executing in container (interactive): {' '.join(cmd)}")
        full_cmd = ["docker", "exec", "-i", self.service_name] + cmd

        benchmark_thread = None
        IDLE_SECONDS = 10

        try:
            process = subprocess.Popen(
                full_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            self.logger.info("\t" + "="*50 + " VLLM SERVER STARTUP " + "="*50)
            server_ready = False
            ready_called = False

            sel = selectors.DefaultSelector()
            sel.register(process.stdout, selectors.EVENT_READ)

            last_output_time = time.time()
            eof = False

            #spinner = Spinner(prefix="Working ", frames=["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"], interval=0.1)
            spinner = Spinner(prefix=f"\t\tDownloading the '{self.model_name}' model is in progress, and may take a while due to its size\t", frames=[], interval=0.1)
           
            try:
                while True:
                    events = sel.select(timeout=1.0)

                    if events:
                        for key, _ in events:
                            line = key.fileobj.readline()
                            if line == "":
                                eof = True
                                break

                            # Stop spinner if it was running; we received new output
                            if spinner.is_running:
                                spinner.stop()

                            clean_line = self.clean_ansi_codes(line.rstrip('\n\r'))
                            if clean_line.strip():
                                self.logger.info(f"[VLLM] {clean_line}")

                                if (not server_ready) and any(
                                    re.search(p, clean_line, re.IGNORECASE) for p in self.ready_patterns
                                ):
                                    self.logger.info("Server readiness signal detected.")
                                    server_ready = True
                                    if not ready_called and callable(on_ready):
                                        ready_called = True
                                        try:
                                            self.logger.info("Invoking on_ready callback...")
                                            benchmark_thread = threading.Thread(
                                                target=on_ready, name="BenchmarkThread", daemon=False
                                            )
                                            benchmark_thread.start()
                                        except Exception as e:
                                            self.logger.error(f"on_ready callback error: {e}")

                            last_output_time = time.time()

                        if eof:
                            break
                    else:
                        # No events within select timeout; check idle threshold
                        if (time.time() - last_output_time) >= IDLE_SECONDS:
                            # Start spinner during idle (restarts only once per idle window)
                            if not spinner.is_running:
                                spinner.start()

                    # Exit when process ended and we've seen EOF
                    if process.poll() is not None and eof:
                        break

                    sys.stdout.flush()
            finally:
                # Ensure spinner is stopped before leaving
                if spinner.is_running:
                    spinner.stop()

                try:
                    sel.unregister(process.stdout)
                except Exception:
                    pass
                try:
                    if process.stdout and not process.stdout.closed:
                        process.stdout.close()
                except Exception:
                    pass

            process.wait()
            self.logger.info("\t" + "="*50 + " VLLM SERVER END " + "="*50)

            if benchmark_thread:
                self.logger.info("Waiting for benchmark thread to finish...")
                benchmark_thread.join()
                self.logger.info("Benchmark thread completed.")
                return 0

        except Exception as e:
            self.logger.error(f"Error in command execution: {e}")
            traceback.print_exc()
            return 1
    # Health check & Debug
    def verify_server_health(self):
        """Check /health endpoint inside container."""
        try:
            self.logger.info("Performing server health check...")
            health_cmd = [
                "docker", "exec", self.service_name,
                "curl", "-f", "-s", self.health_url
            ]
            result = subprocess.run(health_cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                self.logger.info("Health endpoint responding")
                self.logger.debug(f"Health response: {result.stdout}")
                return True
            else:
                self.logger.error(f"Health endpoint failed: {result.stderr}")
                return False
        except Exception as e:
            self.logger.error(f"Health check error: {e}")
            traceback.print_exc()
            return False

    def test_server_connectivity(self):
        """Check /v1/models and then perform a simple completion."""
        try:
            self.logger.info("Testing server connectivity...")
            models_cmd = [
                "docker", "exec", self.service_name,
                "curl", "-f", "-s", self.models_url
            ]
            result = subprocess.run(models_cmd, capture_output=True, text=True, timeout=self.curl_timeout_secs)
            if result.returncode == 0:
                self.logger.info("Models endpoint responding")
                self.logger.debug(f"Models response: {result.stdout}")
                return self.test_simple_completion()
            else:
                self.logger.error(f"Models endpoint failed: {result.stderr}")
                return False
        except Exception as e:
            self.logger.error(f"Connectivity test error: {e}")
            traceback.print_exc()
            return False

    def test_simple_completion(self, prompt="Hello", max_tokens=5, temperature=0.0):
        """POST /v1/completions with a minimal request."""
        try:
            self.logger.info("Testing simple completion request...")
            payload = {
                "model": self.model_name,
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature
            }
            json_str = (
                f'{{"model": "{payload["model"]}", '
                f'"prompt": "{payload["prompt"]}", '
                f'"max_tokens": {payload["max_tokens"]}, '
                f'"temperature": {payload["temperature"]}}}'
            )

            curl_cmd = [
                "docker", "exec", self.service_name,
                "curl", "-f", "-s", "-X", "POST",
                self.completions_url,
                "-H", "Content-Type: application/json",
                "-d", json_str
            ]
            result = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                self.logger.info("Simple completion test passed")
                self.logger.debug(f"Completion response: {result.stdout[:200]}...")
                return True
            else:
                self.logger.error(f"Simple completion test failed: {result.stderr}")
                self.logger.error(f"Response: {result.stdout}")

                if "forbidden" in result.stderr.lower() or "401" in result.stderr or "403" in result.stderr:
                    self.logger.error("This appears to be an authentication/authorization issue")
                    self.logger.info("Possible solutions:")
                    self.logger.info("   - Check if the server requires API keys")
                    self.logger.info("   - Verify server configuration")
                    self.logger.info("   - Check firewall/network settings")
                return False

        except Exception as e:
            self.logger.error(f"Simple completion test error: {e}")
            traceback.print_exc()
            return False

    def debug_server_status(self):
        try:
            self.logger.info("Debugging server status...")

            ps_result = subprocess.run(
                ["docker", "exec", self.service_name, "ps", "aux"],
                capture_output=True, text=True, timeout=10
            )
            self.logger.info("Container processes:")
            for line in ps_result.stdout.split('\n'):
                if 'vllm' in line.lower() or 'python' in line.lower():
                    self.logger.info(f"\t{line}")

            self.logger.info("Recent server logs:")
            logs_result = subprocess.run(
                ["docker", "logs", "--tail", "20", self.service_name],
                capture_output=True, text=True, timeout=10
            )
            for line in logs_result.stdout.split('\n')[-10:]:
                if line.strip():
                    self.logger.info(f"\t{line}")

            self.logger.info("Network connectivity:")
            netstat_result = subprocess.run(
                ["docker", "exec", self.service_name, "netstat", "-tlnp"],
                capture_output=True, text=True, timeout=10
            )
            for line in netstat_result.stdout.split('\n'):
                if str(self.port) in line:
                    self.logger.info(f"\t{line}")

        except Exception as e:
            self.logger.error(f"Debug error: {e}")
    

class BenchmarkClient(VLLMServer):

    def __init__(
        self,
        _logger,
        env_vars,
        docker_image,
        service_name, 
        debug_mode=True,
        reuse_containers=False,

    ):
        super().__init__(_logger, env_vars, docker_image, service_name, debug_mode, reuse_containers)
        self.execution_start_time = time.time()
        self.dataset_type = env_vars.get("DATASET_TYPE", "random")
        self.input_len = int(env_vars.get("INPUT_LEN", "1024"))
        self.output_len = int(env_vars.get("OUTPUT_LEN", "512"))
        self.max_concurrency = int(env_vars.get("MAX_CONCURRENCY", "16"))
        self.ignore_eos = True
        self.num_prompts = int(env_vars.get("NUM_PROMPTS", "56"))
        self.request_rate = env_vars.get("REQUEST_RATE", "inf")
        self.backend = env_vars.get("BACKEND", "vllm")
        self.ready_check_timeout_sec = 1
        self.server_ready_timeout_secs = int(env_vars.get("SERVER_READY_TIMEOUT_SECS", "90"))



    # Internal helpers
    def env_to_vllm_args(self, env: dict):

        args = []

        for key, value in env.items():
            if value is None:
                continue

            key_lower = key.lower()

            # Boolean handling
            if isinstance(value, bool) or str(value).lower() in {"true", "false"}:
                val = str(value).lower() == "true"

                # ENABLE_FOO
                if key_lower.startswith("enable_"):
                    flag = "--" + key_lower.replace("_", "-")
                    if val:
                        args.append(flag)
                    else:
                        args.append("--no-" + flag[2:])
                    continue

                # DISABLE_FOO
                if key_lower.startswith("disable_"):
                    if val:
                        args.append("--" + key_lower.replace("_", "-"))
                    continue

                # Generic boolean
                if val:
                    args.append("--" + key_lower.replace("_", "-"))
                continue

            # Non-boolean key=value flags
            flag = "--" + key_lower.replace("_", "-")
            args.append(f"{flag}={value}")
       
        return args

    def _build_benchmark_cmd(self):
        """Build CLI: python3 -m vllm.entrypoints.cli.main bench serve ..."""
        cmd = [
            "vllm", "bench", "serve",
            "--backend", self.backend,
            "--model", self.model_name,
            "--dataset-name", self.dataset_type,
            "--base-url", self.base_url,   
            "--host", self.host,
            "--port", str(self.port),
            "--num-prompts", str(self.num_prompts),
            "--max-concurrency", str(self.max_concurrency),
            "--percentile-metrics", "ttft,tpot,itl,e2el",     
            "--metric-percentiles", "90",    
        ]

        # Dataset-specific arguments
        if self.dataset_type == "sonnet":
            cmd += [
                "--dataset-path", "/workspace/vllm/benchmarks/sonnet.txt",
                "--sonnet-prefix-len", str(100),
                "--sonnet-input-len", str(self.input_len),
                "--sonnet-output-len", str(self.output_len),
            ]

        elif self.dataset_type == "random":
            cmd += [
                "--random-input-len", str(self.input_len),
                "--random-output-len", str(self.output_len),
            ]
        if self.ignore_eos:
            cmd.append("--ignore-eos")
        if self.trust_remote_code:
            cmd.append("--trust_remote_code")
        return cmd

    def _wait_for_server_ready(self):
        self.logger.info(f"Waiting for server to be ready at {self.models_url} ...")
        start_ts = time.time()
        max_wait = 90 #self.server_ready_timeout_secs
        while time.time() - start_ts < max_wait:
            health_cmd = [
                "docker", "exec", self.service_name,
                "bash", "-lc",
                f"curl -sS -o /dev/null -w '%{{http_code}}' {(self.models_url)} || true"
            ]
            try:
                result = subprocess.run(
                    health_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )
                code = (result.stdout or "").strip()
                if code == "200":
                    self.logger.info("Server is ready (HTTP 200).")
                    return True
                else:
                    self.logger.info(f"Server not ready yet (HTTP {code}). Retrying...")
            except Exception as e:
                self.logger.info(f"Health check error: {e}")
            time.sleep(3)

        self.logger.error("Server health check failed: vLLM server is not reachable.")
        diag_cmd = [
            "docker", "exec", self.service_name,
            "bash", "-lc",
            "set -x; ss -lntp | grep ':8000' || true; "
            "ps -ef | grep -E 'vllm( serve)?' | grep -v grep || true; "
            f"curl -sS {self.models_url} || true"
        ]
        subprocess.run(diag_cmd, check=False)
        return False

    def validate_envelope(self, base_url: str, input_len: int, output_len: int):
        server_max = self.server_ready_probe_json["body_json"]["data"][0].get("max_model_len")
        total = input_len + output_len
        self.logger.info(f"Server max_model_len = {server_max}")
        self.logger.info(f"Benchmark envelope    = input({input_len}) + output({output_len}) = {total}")
        if total > server_max:
            self.cleanup_and_exit(1)
            raise RuntimeError(f"Envelope {total} exceeds server max_model_len {server_max}")
        self.logger.info("OK: envelope fits within server max_model_len")

    def run(self):
        benchmark_result_dict = {}

        try:
            self.logger.info("Starting benchmark run...")
            cwd = os.getcwd()
            self.logger.info(f"Host current working directory: {cwd}")
            self.logger.info(f"Starting benchmark with model: {self.model_name}")

            # Ensure server is ready
            if not self._wait_for_server_ready():
                self.logger.error("Aborting benchmark due to server readiness failure.")
                return {"error": "server_not_ready"}, 1

            # Build command
            benchmark_cmd = self._build_benchmark_cmd()

            benchmark_full_cmd = [
                "docker", "exec",
                f"-e HUGGING_FACE_HUB_TOKEN={self.hf_token}",
                "-w", self.container_workdir,
                self.service_name
            ] + benchmark_cmd

            self.logger.info(f"Benchmark command: {' '.join(benchmark_full_cmd)}")

            benchmark_process = subprocess.Popen(
                benchmark_full_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            self.logger.info("\t" + "="*50 + " BENCHMARK RESULTS " + "="*50)

            metric_keys = [
                'requests:', 'duration (s):', 'throughput', 'latency',
                'tokens:', 'requests/s', 'completed', 'summary', 'ttft', 'tpot', 'itl'
            ]
            error_keys = ['error', 'failed', 'exception', 'forbidden', 'traceback']

            for line in benchmark_process.stdout:
                clean_line = line
                if clean_line.strip():
                    lower = clean_line.lower()
                    if any(k in lower for k in metric_keys):
                        key_value = clean_line.split(":")
                        if len(key_value) == 2:
                            benchmark_result_dict[key_value[0].strip()] = key_value[1].strip()
                        self.logger.info(f"[METRIC] {clean_line.rstrip()}")
                    elif any(k in lower for k in error_keys):
                        self.logger.info(f"[ERROR] {clean_line.rstrip()}")
                    else:
                        self.logger.info(f"[BENCHMARK] {clean_line.rstrip()}")
                sys.stdout.flush()

            benchmark_process.wait()
            self.logger.info("\t" + "="*50 + " BENCHMARK COMPLETE " + "="*50)
            self.logger.info(f"{benchmark_result_dict}")
            self.logger.info(f"Benchmark table")
            ts = datetime.now().strftime("%m%d%Y_%H%M%S")
            
            input_dict = {"model_name" : f"{self.model_name}", 
                          "max_concurrency" : f"{self.max_concurrency}",
                          "input_len": f"{self.input_len}",
                          "output_len" : f"{self.output_len}",
                          "tensor_parallel" : f"{self.tensor_parallel}"                          
                          } 
            
            benchmark_result_dict = input_dict | benchmark_result_dict

            model_repo_name = self.model_name.split("/", 1)[1]
            perf_result_file = (
                model_repo_name
                + "_concur" + str(self.max_concurrency)
                + "_inlen" + str(self.input_len)
                + "_outlen" + str(self.output_len)
                + "_tp" + str(self.tensor_parallel)
                + "_" + str(ts)
            )
            perf_txt_file = "logs/" + self.to_safe_filename(perf_result_file) + ".txt"
            perf_csv_file = "logs/" + self.to_safe_filename(perf_result_file) + ".csv"
            self.print_dict_table(benchmark_result_dict, sep= " | ", perf_file=perf_txt_file)
            self.print_dict_table(benchmark_result_dict, sep= " , ", perf_file=perf_csv_file)

            return benchmark_result_dict, benchmark_process.returncode

        except Exception as e:
            self.logger.error(f"Error running benchmark: {e}")
            traceback.print_exc()
            return {"error": str(e)}, 1
    
    def to_safe_filename(self, file_name ):
        file_name = re.sub(r'[^a-zA-Z0-9._-]+', '_', file_name)
        file_name = re.sub(r'_+', '_', file_name)
        file_name = file_name.strip('._')
        return f"{file_name}"

    def print_dict_table(self, d, show_header=True, sep=" | ", border=True,
                     perf_file=None, mode="w", encoding="utf-8", also_console=False):
        
        if not d:
            lines = ["(empty)"]
        else:
            headers = [str(k) for k in d.keys()]
            values  = [str(v) for v in d.values()]
            widths = [max(len(h), len(v)) for h, v in zip(headers, values)]

            def fmt_row(cells):
                return sep.join(cell.ljust(w) for cell, w in zip(cells, widths))

            header_line = fmt_row(headers)
            value_line  = fmt_row(values)

            lines = []
            if show_header:
                lines.append(header_line)
                if border:
                    lines.append("-" * len(header_line))
            lines.append(value_line)

        text = "\n".join(lines) + "\n"

        if perf_file is None:
            print(text, end="")
            return

        # If out is a path, open and write
        if isinstance(perf_file, str):
            with open(perf_file, mode, encoding=encoding) as f:
                f.write(text)
            if also_console:
                print(text, end="")
            return

        perf_file.write(text)
        if also_console:
            print(text, end="")

class VLLMOrchestrator():

    def __init__(self, _logger, env_vars, force_pull=False, debug_mode=True, reuse_containers=True):
       
        self.force_pull = force_pull
        self.execution_start_time = None
        self.logger = _logger
        self.env_vars = env_vars
        self.test_status = 'FAIL'
        self.docker_image = env_vars.get("DOCKER_IMAGE", "intel/vllm:latest")


    
    def run_pipeline(self):
        self.execution_start_time = time.time()

        try:
            self.logger.info("Starting vLLM benchmark pipeline with automatic cleanup...")

            benchObj = BenchmarkClient(
                _logger = self.logger,         
                env_vars = self.env_vars,
                #docker_image = "intel/vllm:latest",
                docker_image = self.docker_image,
                service_name  = "vllm_service_xpu",
                debug_mode=True,
                reuse_containers=True,       
            )

            if not benchObj.configure_docker_proxy():
                self.logger.warning("Docker proxy configuration failed, continuing anyway...")

            benchObj.login()

            benchObj.pull_image(force=self.force_pull)

            benchObj.start_container()

            is_vllm_server_ready = threading.Event()

            def start_benchmark_and_cleanup():
                """Runs benchmark and cleans up after completion."""
                is_vllm_server_ready.set()

                # Wait for HTTP readiness
                if not benchObj.wait_for_server_ready(timeout_secs=benchObj.server_ready_timeout_secs):
                    self.logger.error("HTTP readiness failed; benchmark aborted.")
                    benchObj.cleanup_and_exit(1)
                    return
                
                benchObj.validate_envelope(benchObj.base_url, benchObj.input_len, benchObj.output_len)

                # Run benchmark
                metrics, rc = benchObj.run()
                self.logger.info(f"[BENCH] Completed rc={rc}, metrics={metrics}")

                # Stop service and cleanup
                if rc == 0:
                    self.test_status = 'PASS'
                benchObj.cleanup_and_exit(rc)

            def on_ready_callback_benchmark_test():
                thread = threading.Thread(
                    target=start_benchmark_and_cleanup,
                    name="BenchmarkThread",
                    daemon=False
                )
                thread.start()
                return 0

            rc = benchObj.exec_in_container_interactive(
                ["bash", "-lc", benchObj.server_launch_command()],
                on_ready=on_ready_callback_benchmark_test
            )
            self.logger.info(f"exec_in_container_interactive code: {rc}")

            if not is_vllm_server_ready.is_set():
                self.logger.warning("Server ended before readiness ; performing fallback cleanup.")
                benchObj.cleanup_and_exit(1)
            return 0
        except (ValueError, RuntimeError) as e:
            self.logger.error(f"Configuration error: {e}")
            return 1
        except Exception as e:
            self.logger.error(f"Pipeline error: {e}")
            traceback.print_exc()
            try:
                if 'benchObj' in locals():
                    benchObj.cleanup_and_exit(1)
                else:
                    import sys
                    sys.exit(1)
            except Exception as ce:
                self.logger.error(f"Cleanup error after pipeline exception: {ce}")

   

 

class testClass(aiwlBase):
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)
        self.testName = 'AI VLLM Test'
        self.SENSITIVE_KEYS = {"DOCKER_TOKEN", "HUGGING_FACE_HUB_TOKEN"}

    
    def add_arguments(self):
        super().add_arguments()
        self.add_parser_argument("--model", arg_type=str, help_text="Model to serve", default_value=None, dest_name="model")
        self.add_parser_argument("--enforce-eager", arg_type=bool, help_text="Force eager execution", default_value=None, dest_name="enforce_eager")
        self.add_parser_argument("--host", arg_type=str, help_text="Host address to bind", default_value=None, dest_name="host")
        self.add_parser_argument("--port", arg_type=int, help_text="Port to serve on", default_value=None, dest_name="port")
        self.add_parser_argument("--trust-remote-code", arg_type=bool, help_text="Trust remote model code", default_value=None, dest_name="trust_remote_code")
        self.add_parser_argument("--gpu-memory-util", arg_type=float, help_text="GPU memory utilization ratio", default_value=None, dest_name="gpu_memory_util")
        self.add_parser_argument("--enable-prefix-caching", arg_type=bool, help_text="Enable prefix caching", default_value=None, dest_name="enable_prefix_caching")
        self.add_parser_argument("--disable-log-requests", arg_type=bool, help_text="Disable request logging", default_value=None, dest_name="disable_log_requests")
        self.add_parser_argument("--dataset-type", arg_type=str, help_text="Dataset Type", default_value=None, dest_name="dataset_type")
        self.add_parser_argument("--max-model-len", arg_type=int, help_text="Maximum model context length", default_value=None, dest_name="max_model_len")
        self.add_parser_argument("--max-concurrency", arg_type=int, help_text="Maximum concurrency", default_value=None, dest_name="max_concurrency")
        self.add_parser_argument("--input-len", arg_type=int, help_text="input length", default_value=None, dest_name="input_len")
        self.add_parser_argument("--output-len", arg_type=int, help_text="output length", default_value=None, dest_name="output_len")
        self.add_parser_argument("--max-num-batched-tokens", arg_type=int, help_text="Maximum number of batched tokens", default_value=None, dest_name="max_num_batched_tokens")
        self.add_parser_argument("--block-size", arg_type=int, help_text="KV cache block size", default_value=None, dest_name="block_size")
        self.add_parser_argument("--dtype", arg_type=str, help_text="Model data type", default_value=None, dest_name="dtype")
        self.add_parser_argument("--tp", arg_type=int, help_text="Tensor parallel size", default_value=None, dest_name="tp")
        self.add_parser_argument("--data-parallel-size", arg_type=int, help_text="Data parallel size", default_value=None, dest_name="data_parallel_size")
        self.add_parser_argument("--enable-expert-parallel", arg_type=bool, help_text="Enable expert parallelism", default_value=None, dest_name="enable_expert_parallel")
        self.add_parser_argument("--docker-user", arg_type=str, help_text="Docker user name", default_value="", dest_name="docker_user")
        self.add_parser_argument("--docker-token", arg_type=str, help_text="Docker token", default_value="", dest_name="docker_token")
        self.add_parser_argument("--docker-image", arg_type=str, help_text="Docker Image", default_value="", dest_name="docker_image")
        self.add_parser_argument("--http-proxy", arg_type=str, help_text="Enter HTTP proxy server detais", default_value=None, dest_name="http_proxy")
        self.add_parser_argument("--https-proxy", arg_type=str, help_text="Enter HTTPS proxy server detais", default_value=None, dest_name="https_proxy")
        self.add_parser_argument("--no-proxy", arg_type=str, help_text="Enter no proxy server detais", default_value=None, dest_name="no_proxy")
        self.add_parser_argument("--hf-token", arg_type=str, help_text="Enter Huggingface uer token", default_value=None, dest_name="hf_token")
        self.add_parser_argument("--disable-sliding-window", arg_type=bool, help_text="Disable sliding window feature", default_value=None, dest_name="disable_sliding_window")
        self.add_parser_argument("--quantization", arg_type=str, help_text="Quantization type", default_value=None, dest_name="quantization")


    
    def prepareGpuCommands(self):
        self.gpuCommands = []
        self.logger.info('From vLLM test class')
        self.execution_dir = '.'

    
    def verify_model_load(self, model_name, gpu_num):
        match = re.search(r"(\d+)(?=[Bb])", model_name)
        if not match:
            raise ValueError(f"Could not determine parameter size from model name '{model_name}'. ")

        params_b = float(match.group(1))  
        BYTES_PER_PARAM = 2
        OVERHEAD_FRAC = 0.25
        GPU_VRAM_GB = 24.0

        weights_bytes = params_b * 1e9 * BYTES_PER_PARAM

        overhead_bytes = weights_bytes * OVERHEAD_FRAC
        total_bytes = weights_bytes + overhead_bytes
        total_gb = total_bytes / (1024 ** 3)

        available_gb = gpu_num * GPU_VRAM_GB

        can_load = total_gb <= available_gb
        if can_load:
            return True
        else:
            self.logger.error(f"Model: {model_name}, Params: {params_b}B, memory required: {total_gb:.2f} GB, but available only: {available_gb:.2f} GB")
            return False
        
    def _safe_log_value(self, key, value):
        if key in self.SENSITIVE_KEYS and value is not None:
            return "***REDACTED***"
        return value


    def load_config(self, args, env_file):
        """
        Load configuration from .env file and command line args
        without modifying the .env file.
        """

        try:
            base_config = dotenv_values(env_file) or {}
        except Exception as e:
            self.logger.error(f"Failed to load .env file '{env_file}': {e}",)
            base_config = {}

        runtime_config = dict(base_config)

        # rule:
        #   - "truthy"   → override only if bool(value) is True
        #   - "not_none" → override if value is not None
        overrides = [
            ("model", "MODEL", "truthy"),
            ("enforce_eager", "ENFORCE_EAGER", "not_none"),
            ("host", "HOST", "truthy"),
            ("port", "PORT", "not_none"),
            ("trust_remote_code", "TRUST_REMOTE_CODE", "not_none"),
            ("gpu_memory_util", "GPU_MEMORY_UTIL", "truthy"),
            ("enable_prefix_caching", "ENABLE_PREFIX_CACHING", "not_none"),
            ("disable_log_requests", "DISABLE_LOG_REQUESTS", "not_none"),
            ("dataset_type", "DATASET_TYPE", "truthy"),
            ("max_model_len", "MAX_MODEL_LEN", "not_none"),
            ("max_num_batched_tokens", "MAX_NUM_BATCHED_TOKENS", "not_none"),
            ("block_size", "BLOCK_SIZE", "not_none"),
            ("dtype", "DTYPE", "truthy"),
            ("tp", "TENSOR_PARALLEL_SIZE", "not_none"),
            ("data_parallel_size", "DATA_PARALLEL_SIZE", "not_none"),
            ("max_concurrency", "MAX_CONCURRENCY", "not_none"),
            ("input_len", "INPUT_LEN", "not_none"),
            ("output_len", "OUTPUT_LEN", "not_none"),
            ("enable_expert_parallel", "ENABLE_EXPERT_PARALLEL", "not_none"),
            ("docker_user", "DOCKER_USER", "truthy"),
            ("docker_token", "DOCKER_TOKEN", "truthy"),
            ("docker_image", "DOCKER_IMAGE", "truthy"),
            ("hf_token", "HUGGING_FACE_HUB_TOKEN", "truthy"),
            ("http_proxy", "HTTP_PROXY", "not_none"),
            ("https_proxy", "HTTPS_PROXY", "not_none"),
            ("no_proxy", "NO_PROXY", "not_none"),
            ("disable_sliding_window", "DISABLE_SLIDING_WINDOW", "not_none"),
            ("quantization", "QUANTIZATION", "not_none"),
        ]

        for arg_attr, env_key, rule in overrides:
            try:
                value = getattr(args, arg_attr, None)
                should_override = (value is not None if rule == "not_none" else bool(value))
                if not should_override:
                    continue
                runtime_config[env_key] = value
                self.logger.info(f"Override: {env_key} from command line input = {self._safe_log_value(env_key, value)}")

            except Exception as e:
                self.logger.info(f"Override for '{arg_attr}' → '{env_key}' : '{value}'")

        self.logger.info(f"Configuration loaded: {len(runtime_config)} variables")
        self.logger.info("Original .env file preserved")

        return runtime_config

    
    def runTest(self):
        env_path = "gpu/bmg/tests/ai_wl/.env"
        config = self.load_config(self.parsed_args, env_path)

        # Validate required credentials before launching the pipeline
        if not config.get("HUGGING_FACE_HUB_TOKEN"):
            self.logger.error("HUGGING_FACE_HUB_TOKEN is not set or is empty.")
            self.logger.error("Please update the .env file or provide --hf-token on the command line.")
            self.overall_test_result = 'FAIL'
            return

        model_name = os.getenv("MODEL", config.get("MODEL"))
        self.device_manager = DeviceManager(self.logger)
        self.device_manager.discover_devices()
         
        if not self.verify_model_load(model_name, self.device_manager.gpu_num):
             self.logger.error("Model can not be loaded")
             self.overall_test_result = 'FAIL'
             return
        
        orchestrator = VLLMOrchestrator(
            self.logger,
            config,
            force_pull=False,
            debug_mode=True,
            reuse_containers=False
        )
        # Run pipeline
        orchestrator.run_pipeline()
        self.overall_test_result = orchestrator.test_status
