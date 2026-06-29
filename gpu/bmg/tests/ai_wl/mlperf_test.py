# Copyright (C) 2024-2026 Intel Corporation
# Verification Test Suite
# MLPERF Test

import os
import re
import pwd
import subprocess  # nosec
import time
import hashlib
import getpass
from typing import Dict, List, Optional, Tuple

from .ai_wl_base import aiwlBase


def load_config_env(config_file='config.env'):
    """Load configuration from config.env file (or config.env.template as fallback)"""
    config = {}
    config_dir = os.path.dirname(__file__)
    config_path = os.path.join(config_dir, config_file)
    
    # Fall back to template if user-specific config.env doesn't exist
    if not os.path.exists(config_path):
        template_path = os.path.join(config_dir, 'config.env.template')
        if os.path.exists(template_path):
            config_path = template_path
        else:
            return config
    
    try:
        with open(config_path, 'r') as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith('#'):
                    continue
                # Parse KEY=VALUE
                if '=' in line:
                    key, value = line.split('=', 1)
                    config[key.strip()] = value.strip()
    except Exception as e:
        print(f"Warning: Could not load config.env: {e}")
    
    return config


class testClass(aiwlBase):
    """
    Interactive MLPerf test with Docker container management and validation.
    
    Features:
    - Interactive CLI UI for test configuration
    - Docker image validation and pull instructions
    - Dataset and model file validation
    - Automated Docker container launch
    - Benchmark execution and result collection (scripts are in Docker image)
    - Persistent container session with host-side result viewing
    """
    
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)
        self.testName = 'MLPERF Test'
        self.gpu_process_name = 'MLPerfBenchmarkProcess'
        
        # Load configuration from config.env
        self.env_config = load_config_env()
        
        # Get the real user even when running with sudo
        current_user = os.environ.get('SUDO_USER') or getpass.getuser()

        # SECURITY: Validate user via OS lookup and derive home directory from system records.
        # Using pwd.getpwnam() avoids silently mutating usernames (e.g. john.doe -> johndoe)
        # which would point constructed paths to the wrong home directory.
        try:
            pw_entry = pwd.getpwnam(current_user)
            home_dir = pw_entry.pw_dir
            self.logger.info(f"SECURITY: Validated user '{current_user}', home='{home_dir}'")
        except KeyError:
            self.logger.warning(
                f"SECURITY: User '{current_user}' not found in system, falling back to current process home"
            )
            home_dir = os.path.expanduser('~')
            current_user = getpass.getuser()
            self.logger.info(f"SECURITY: Using fallback user '{current_user}', home='{home_dir}'")

        # Store for use in add_arguments() and expand_user_placeholders_in_args()
        self._current_user = current_user
        self._home_dir = home_dir

        # Container configuration
        self.container_name = self.env_config.get('CONTAINER_NAME', 'mlperf_benchmark_container')
        self.container_id = None
        self.is_container_running = False

        # Default paths - home directory is derived from OS (pwd.getpwnam), not constructed from
        # the username string, so usernames with dots or other valid characters work correctly.
        self.data_dir = self.env_config.get('DATA_DIR', os.path.join(home_dir, 'data')).replace('{user}', current_user)
        self.model_dir = self.env_config.get('MODEL_DIR', os.path.join(home_dir, 'model', '.llama', 'checkpoints')).replace('{user}', current_user)
        self.log_dir = self.env_config.get('LOG_DIR', os.path.join(home_dir, 'logs')).replace('{user}', current_user)
        
        # Docker image configuration
        self.docker_image = self.env_config.get('DOCKER_IMAGE', 'intel/intel-optimized-pytorch:mlperf-inference-6.0-llama_xpu')
        
        # Proxy configuration
        self.http_proxy = self.env_config.get('HTTP_PROXY', '')
        self.https_proxy = self.env_config.get('HTTPS_PROXY', '')
        
        # Scripts inside Docker image (paths in container)
        self.calibration_script = self.env_config.get('CALIBRATION_SCRIPT', 'bash scripts/run_calibration.sh')
        self.benchmark_script = self.env_config.get('BENCHMARK_SCRIPT', 'SCENARIO=Offline MODE=Performance bash /workspace/run_mlperf.sh')
        
        # Benchmark script names
        self.benchmark_script_llama2_70b = self.env_config.get('BENCHMARK_SCRIPT_LLAMA2_70B', 'run_mlperf.sh')
        self.benchmark_script_llama3_1_8b = self.env_config.get('BENCHMARK_SCRIPT_LLAMA3_1_8B', 'run_mlperf.sh')
        
        # Default benchmark configuration
        self.default_model_name = self.env_config.get('DEFAULT_MODEL_NAME', 'TechxGenus/Meta-Llama-3-8B-GPTQ')
        self.default_max_tokens = self.env_config.get('DEFAULT_MAX_TOKENS', '128')
        self.default_input_length = self.env_config.get('DEFAULT_INPUT_LENGTH', '64')
        self.default_concurrency = self.env_config.get('DEFAULT_CONCURRENCY', '2')
        
        # Test configuration from user input
        self.test_config = {}
        
    def add_arguments(self):
        """Add test-specific arguments from platform_defs parameter definitions"""
        super().add_arguments()
        
        # Auto-confirm flag: skip interactive confirmations (e.g. "Press ENTER to proceed")
        # Usage: -y True or -y False (default: False)
        self.add_parser_argument('-y', 'Auto-confirm all prompts (skip interactive confirmations)', lambda x: x.lower() == 'true' if isinstance(x, str) else bool(x), False, 'yes')
        
        # Get parameter definitions from platform_defs
        param_defs = self.platform_defs_instance.test_parameter_definitions.get(12, {})  # Test #12 is MLPerf
        
        if param_defs and 'parameters' in param_defs:
            # Use definitions from platform_defs.py
            for param in param_defs['parameters']:
                param_name = param['name']
                param_help = param.get('help', param.get('prompt', f'{param_name} parameter'))
                param_type = param['type']
                param_default = param['default']
                
                # Expand {user} placeholder in default values using OS-validated user
                if isinstance(param_default, str) and '{user}' in param_default:
                    param_default = param_default.replace('{user}', self._current_user)
                
                # Add the argument (use -- prefix for multi-char names to avoid
                # collisions with short options like -h, -d, -m registered globally)
                self.add_parser_argument(
                    f'--{param_name}',
                    param_help,
                    param_type,
                    param_default,
                    param_name
                )

            # CLI-only arguments (not prompted interactively)
            self.add_parser_argument('--download_model', 'Download HuggingFace model before test (e.g. llama3.1-8b)', str, '', 'download_model')
            self.add_parser_argument('--hf_token', 'HuggingFace access token (required for gated models)', str, '', 'hf_token')
        else:
            # Fallback to hardcoded defaults if platform_defs not available
            self.logger.warning("Using fallback parameter definitions (platform_defs not loaded)")
            
            # Docker image override
            self.add_parser_argument(
                '--docker_image',
                'Docker image to use for MLPerf testing',
                str,
                self.docker_image,
                'docker_image'
            )
            
            # Path overrides
            self.add_parser_argument(
                '--data_dir',
                'Path to dataset directory',
                str,
                self.data_dir,
                'data_dir'
            )
            
            self.add_parser_argument(
                '--model_dir',
                'Path to model directory',
                str,
                self.model_dir,
                'model_dir'
            )
            
            # Model & Benchmark Configuration
            self.add_parser_argument(
                '--model_type',
                'Model type (llama3_1-8b or llama2-70b)',
                str,
                'llama3_1-8b',
                'model_type'
            )
            
            self.add_parser_argument(
                '--scenario',
                'Benchmark scenario (Offline, Server)',
                str,
                'Offline',
                'scenario'
            )
            
            self.add_parser_argument(
                '--mode',
                'Benchmark mode (Performance, Accuracy, Compliance)',
                str,
                'Performance',
                'mode'
            )
            

    
    def printArguments(self):
        """Override to expand {user} placeholders before printing arguments"""
        # Expand {user} placeholders first
        self.expand_user_placeholders_in_args()
        # Call parent class method to print
        super().printArguments()
    
    def expand_user_placeholders_in_args(self):
        """
        Expand {user} placeholders in parsed_args after parsing.
        This ensures the actual username is shown in logs and used throughout.
        """
        # List of attributes that may contain {user} placeholder
        path_attrs = ['data_dir', 'model_dir', 'log_dir', 'docker_image']

        for attr in path_attrs:
            if hasattr(self.parsed_args, attr):
                value = getattr(self.parsed_args, attr)
                if isinstance(value, str) and '{user}' in value:
                    expanded_value = value.replace('{user}', self._current_user)
                    setattr(self.parsed_args, attr, expanded_value)
    
    # ──────────────────────────────────────────────────────────────────────
    #  HuggingFace model download via isolated venv
    # ──────────────────────────────────────────────────────────────────────
    # Supported --download_model aliases → HuggingFace repo IDs
    HF_MODEL_MAP = {
        'llama3.1-8b':  'meta-llama/Llama-3.1-8B-Instruct',
        'llama3_1-8b':  'meta-llama/Llama-3.1-8B-Instruct',
        'llama2-70b':   'meta-llama/Llama-2-70b-chat-hf',
        'llama2_70b':   'meta-llama/Llama-2-70b-chat-hf',
    }

    def _resolve_hf_model(self, alias: str) -> str:
        """Resolve a user-friendly model alias to a HuggingFace repo ID.
        Falls back to treating the alias as a literal repo ID."""
        return self.HF_MODEL_MAP.get(alias.lower(),
               self.env_config.get('HF_MODEL_LLAMA3_1_8B', alias))

    def download_hf_model(self) -> bool:
        """
        Download a gated HuggingFace model using huggingface-cli inside an
        isolated Python venv.

        Workflow
        -------
        1. Create a temporary venv (``/tmp/vts_hf_venv`` by default).
        2. Install ``huggingface_hub[cli]`` inside the venv.
        3. Authenticate with the user-supplied HF token.
        4. Run ``huggingface-cli download`` (supports resume on re-run).
        5. Exit / deactivate the venv so the system Python is unaffected.

        Returns ``True`` on success, ``False`` on any error.
        """
        model_alias = getattr(self.parsed_args, 'download_model', None)
        if not model_alias:
            return True  # nothing to download

        token = getattr(self.parsed_args, 'hf_token', None) or os.environ.get('HF_TOKEN')
        if not token:
            self.logger.error("HuggingFace token is required for gated model download.")
            self.logger.error("Use --hf_token <token> or set the HF_TOKEN environment variable.")
            return False

        repo_id = self._resolve_hf_model(model_alias)
        venv_dir = self.env_config.get('HF_VENV_DIR', '/tmp/vts_hf_venv')
        base_model_dir = getattr(self.parsed_args, 'model_dir', self.model_dir)
        # Append model name as subdirectory: e.g. .../checkpoints/Llama-3.1-8B-Instruct/
        model_name = repo_id.split('/')[-1]  # "meta-llama/Llama-3.1-8B-Instruct" → "Llama-3.1-8B-Instruct"
        model_dir = os.path.join(base_model_dir, model_name)

        self.logger.subheader("STEP 0: HuggingFace Model Download (venv-isolated)")
        self.logger.info(f"  Model repo    : {repo_id}")
        self.logger.info(f"  Destination   : {model_dir}")
        self.logger.info(f"  Venv path     : {venv_dir}")

        venv_python = os.path.join(venv_dir, 'bin', 'python3')
        venv_pip    = os.path.join(venv_dir, 'bin', 'pip')

        # Proxy env for all subprocess calls
        env = os.environ.copy()

        # Sanitize inherited proxy env vars — strip stray quotes that break
        # httpx hostname parsing (e.g. no_proxy='\u201d\u201d' → InvalidURL error).
        # Covers ASCII quotes and Unicode smart/curly quotes (U+201C/201D/2018/2019)
        # which appear when proxy config is copy-pasted from Word/email/web pages.
        _proxy_keys = ['HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy',
                       'NO_PROXY', 'no_proxy', 'ALL_PROXY', 'all_proxy',
                       'FTP_PROXY', 'ftp_proxy']
        _quote_chars = '"\'\u201c\u201d\u2018\u2019'
        for _pk in _proxy_keys:
            if _pk in env:
                env[_pk] = env[_pk].strip(_quote_chars)

        if self.http_proxy:
            env['HTTP_PROXY']  = self.http_proxy
            env['http_proxy']  = self.http_proxy
        if self.https_proxy:
            env['HTTPS_PROXY'] = self.https_proxy
            env['https_proxy'] = self.https_proxy
        env['HF_TOKEN'] = token  # make token available to huggingface-cli

        try:
            # ── Step 0a: Create or validate venv ───────────────────────────
            venv_healthy = (os.path.isfile(venv_python) and
                            os.path.isfile(venv_pip))

            if venv_healthy:
                self.logger.info("  [VENV] Reusing existing venv (validated)")
            else:
                # Remove broken leftover venv if it exists
                if os.path.exists(venv_dir):
                    import shutil
                    self.logger.warning(f"  [VENV] Removing broken venv at {venv_dir}")
                    shutil.rmtree(venv_dir, ignore_errors=True)

                # Ensure python3-venv package is installed
                self.logger.info("  [VENV] Checking python3-venv package ...")
                check_venv = subprocess.run(
                    ['dpkg', '-s', 'python3-venv'],
                    capture_output=True, text=True, timeout=30
                )
                if check_venv.returncode != 0:
                    self.logger.info("  [VENV] python3-venv not found, installing ...")
                    install_result = subprocess.run(
                        ['apt-get', 'install', '-y', 'python3-venv'],
                        capture_output=True, text=True, timeout=120, env=env
                    )
                    if install_result.returncode != 0:
                        self.logger.error(f"  [VENV] Failed to install python3-venv: {install_result.stderr or install_result.stdout}")
                        self.logger.error("  Please install manually: sudo apt-get install -y python3-venv")
                        return False
                    self.logger.pass_msg("  [VENV] ✓ python3-venv installed")

                self.logger.info("  [VENV] Creating isolated Python venv ...")
                result = subprocess.run(
                    ['python3', '-m', 'venv', venv_dir],
                    capture_output=True, text=True, timeout=120, env=env
                )
                if result.returncode != 0:
                    err_msg = result.stderr or result.stdout or '(no output)'
                    self.logger.error(f"  [VENV] Failed to create venv: {err_msg}")
                    return False
                self.logger.pass_msg("  [VENV] ✓ Venv created")

            # ── Step 0b: Install / upgrade huggingface_hub[cli] ───────────
            self.logger.info("  [VENV] Installing huggingface_hub[cli] ...")
            result = subprocess.run(
                [venv_pip, 'install', '-U', 'huggingface_hub[cli]'],
                capture_output=True, text=True, timeout=300, env=env
            )
            if result.returncode != 0:
                self.logger.error(f"  [VENV] pip install failed: {result.stderr}")
                return False
            self.logger.pass_msg("  [VENV] ✓ huggingface_hub[cli] installed")

            # ── Step 0c: Verify token via Python API ─────────────────────
            self.logger.info("  [VENV] Authenticating with HuggingFace ...")
            result = subprocess.run(
                [venv_python, '-c',
                 'from huggingface_hub import HfApi; '
                 'api = HfApi(); '
                 'info = api.whoami(); '
                 'print(info.get("name", info.get("fullname", "OK")))'],
                capture_output=True, text=True, timeout=30, env=env
            )
            if result.returncode == 0:
                self.logger.pass_msg(f"  [VENV] ✓ Authenticated as: {result.stdout.strip()}")
            else:
                self.logger.warning(f"  [VENV] Could not verify identity (will rely on HF_TOKEN env): {result.stderr.strip()}")

            # ── Step 0d: Download model via Python API (resumable) ────────
            os.makedirs(model_dir, exist_ok=True)
            self.logger.info(f"  [DOWNLOAD] Starting download of {repo_id} ...")
            self.logger.info(f"  [DOWNLOAD] This may take a long time (~16 GB). Download is resumable.")

            # Inline Python script that downloads files one-by-one with
            # per-file and overall progress printed as plain text lines.
            # NOTE: Uses regular string + .replace() to inject repo_id/model_dir
            # to avoid f-string escaping issues with inner Python code.
            download_script = '''
import os, sys, time
from huggingface_hub import HfApi, hf_hub_download

token = os.environ.get("HF_TOKEN")
api = HfApi()
repo_id = "REPO_ID_PLACEHOLDER"
local_dir = "MODEL_DIR_PLACEHOLDER"

# List all files in the repo with sizes
print("[INFO] Fetching file list from repository ...", flush=True)
repo_files = list(api.list_repo_tree(repo_id, recursive=True, token=token))
files = [f for f in repo_files if hasattr(f, "size") and f.size is not None]

total_size = sum(f.size for f in files)
total_files = len(files)
pad = len(str(total_files))

def fmt_size(b):
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"

print(f"[INFO] Repository: {repo_id}", flush=True)
print(f"[INFO] Total files: {total_files}, Total size: {fmt_size(total_size)}", flush=True)
print(f"[INFO] Destination: {local_dir}", flush=True)
print("-" * 70, flush=True)

downloaded_size = 0
skipped = 0
t0 = time.time()

for idx, f in enumerate(files, 1):
    dest_path = os.path.join(local_dir, f.rfilename)

    # Check if file already exists with correct size (resume support)
    if os.path.isfile(dest_path) and os.path.getsize(dest_path) == f.size:
        downloaded_size += f.size
        skipped += 1
        pct = downloaded_size / total_size * 100 if total_size else 100
        print(f"[{idx:>{pad}}/{total_files}] SKIP  {f.rfilename} ({fmt_size(f.size)}) -- already exists  [{pct:.1f}%]", flush=True)
        continue

    print(f"[{idx:>{pad}}/{total_files}] GET   {f.rfilename} ({fmt_size(f.size)}) ...", end="", flush=True)
    t1 = time.time()

    try:
        hf_hub_download(
            repo_id=repo_id,
            filename=f.rfilename,
            local_dir=local_dir,
            token=token,
        )
    except Exception as e:
        print(f" FAILED: {e}", flush=True)
        sys.exit(1)

    elapsed = time.time() - t1
    speed = f.size / elapsed if elapsed > 0 else 0
    downloaded_size += f.size
    pct = downloaded_size / total_size * 100 if total_size else 100
    print(f" OK ({fmt_size(speed)}/s)  [{pct:.1f}%]", flush=True)

elapsed_total = time.time() - t0
avg_speed = downloaded_size / elapsed_total if elapsed_total > 0 else 0
print("-" * 70, flush=True)
print(f"[DONE] {total_files} files, {fmt_size(total_size)} total", flush=True)
print(f"[DONE] {skipped} skipped (already existed), {total_files - skipped} downloaded", flush=True)
print(f"[DONE] Elapsed: {elapsed_total:.1f}s, Avg speed: {fmt_size(avg_speed)}/s", flush=True)
print(f"[DONE] Downloaded to: {local_dir}", flush=True)
'''.replace('REPO_ID_PLACEHOLDER', repo_id.replace('\\', '\\\\').replace("'", "\\'")).replace('MODEL_DIR_PLACEHOLDER', model_dir.replace('\\', '\\\\').replace("'", "\\'"))

            process = subprocess.Popen(
                [venv_python, '-u', '-c', download_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )

            for line in process.stdout:
                self.logger.info(f"  [DOWNLOAD] {line.rstrip()}")

            # Timeout: 4 hours upper bound for large model downloads (~16 GB)
            process.wait(timeout=14400)

            if process.returncode != 0:
                self.logger.fail_msg(f"  [DOWNLOAD] ✗ huggingface-cli download failed (rc={process.returncode})")
                return False

            self.logger.pass_msg(f"  [DOWNLOAD] ✓ Model downloaded to {model_dir}")

            # ── Step 0e: Venv exit — nothing to do ────────────────────────
            # The venv was only used inside subprocess calls; the parent
            # Python process was never "activated" into the venv, so there
            # is nothing to deactivate.  The venv stays on disk for future
            # resume runs and is harmless.
            self.logger.info("  [VENV] Exiting isolated venv scope (system Python unaffected)")
            self.logger.pass_msg("✓ HuggingFace model download complete — proceeding with test")
            return True

        except subprocess.TimeoutExpired:
            self.logger.error("  [DOWNLOAD] Timeout expired during download. Re-run to resume.")
            return False
        except FileNotFoundError as e:
            self.logger.error(f"  [VENV] python3 or venv module not found: {e}")
            self.logger.error("  Ensure python3 and python3-venv are installed: sudo apt-get install -y python3 python3-venv")
            return False
        except Exception as e:
            self.logger.error(f"  [DOWNLOAD] Unexpected error: {e}")
            return False

    def _build_test_config_from_args(self) -> Dict:
        """
        Build test configuration from command-line arguments
        
        Returns:
            Dict containing test configuration
        """
        config = {}
        
        # Get configuration from parsed arguments
        config['docker_image'] = getattr(self.parsed_args, 'docker_image', self.docker_image)
        config['data_dir'] = getattr(self.parsed_args, 'data_dir', self.data_dir)
        config['model_dir'] = getattr(self.parsed_args, 'model_dir', self.model_dir)
        config['log_dir'] = self.log_dir
        
        # Get model configuration from parsed arguments
        config['model_type'] = getattr(self.parsed_args, 'model_type', 'llama3_1-8b')
        config['scenario'] = getattr(self.parsed_args, 'scenario', 'Offline')
        config['mode'] = getattr(self.parsed_args, 'mode', 'Performance')
        
        # Set benchmark script based on model type
        if config['model_type'] == 'llama2-70b':
            config['benchmark_script_name'] = self.benchmark_script_llama2_70b
        else:
            config['benchmark_script_name'] = self.benchmark_script_llama3_1_8b
        
        # Construct benchmark command
        config['benchmark_script'] = f"SCENARIO={config['scenario']} MODE={config['mode']} bash /workspace/{config['benchmark_script_name']}"
        
        config['model_name'] = self.default_model_name
        config['max_tokens'] = self.default_max_tokens
        config['input_length'] = self.default_input_length
        config['concurrency'] = self.default_concurrency
        
        # Scripts are in Docker image - use defaults
        config['calibration_script'] = self.calibration_script
        
        # Proxy configuration
        config['http_proxy'] = self.http_proxy
        config['https_proxy'] = self.https_proxy
        
        self.test_config = config
        return config
    
    def _display_test_configuration(self):
        """
        Display test configuration summary table with all parameters
        Shows default values and user-specified values
        """
        config = self.test_config
        
        # Create configuration summary
        self.logger.info("\n" + "="*80)
        self.logger.info(" "*25 + "MLPerf Test Configuration")
        self.logger.info("="*80)
        
        # Docker Configuration
        self.logger.info("\n[Docker Configuration]")
        self.logger.info(f"  Docker Image:        {config['docker_image']}")
        self.logger.info(f"  Container Name:      {self.container_name}")
        
        # Path Configuration
        self.logger.info("\n[Path Configuration]")
        self.logger.info(f"  Data Directory:      {config['data_dir']}")
        self.logger.info(f"  Model Directory:     {config['model_dir']}")
        self.logger.info(f"  Log Directory:       {config['log_dir']}")
        
        # Model & Benchmark Configuration
        self.logger.info("\n[Model & Benchmark Configuration]")
        self.logger.info(f"  Model Type:          {config['model_type']}")
        self.logger.info(f"  Model Name:          {config['model_name']}")
        self.logger.info(f"  Scenario:            {config['scenario']}")
        self.logger.info(f"  Mode:                {config['mode']}")
        self.logger.info(f"  Max Tokens:          {config['max_tokens']}")
        self.logger.info(f"  Input Length:        {config['input_length']}")
        self.logger.info(f"  Concurrency:         {config['concurrency']}")
        
        # Scripts Configuration
        self.logger.info("\n[Scripts Configuration]")
        self.logger.info(f"  Benchmark Script:    {config['benchmark_script_name']}")
        self.logger.info(f"  Calibration Script:  {config['calibration_script']}")
        
        # Proxy Configuration
        if config['http_proxy'] or config['https_proxy']:
            self.logger.info("\n[Proxy Configuration]")
            if config['http_proxy']:
                self.logger.info(f"  HTTP Proxy:          {config['http_proxy']}")
            if config['https_proxy']:
                self.logger.info(f"  HTTPS Proxy:         {config['https_proxy']}")
        
        self.logger.info("\n" + "="*80)
        
        # Skip confirmation if -y flag is set
        if getattr(self.parsed_args, 'yes', False):
            self.logger.info("\nAuto-confirmed (-y flag). Proceeding with test execution...\n")
            return
        
        self.logger.info("\nPress ENTER to proceed with the above configuration, or Ctrl+C to cancel...")
        
        try:
            input()
            self.logger.info("\nProceeding with test execution...\n")
        except KeyboardInterrupt:
            self.logger.info("\n\nTest cancelled by user.")
            raise
    
    def check_docker_image(self) -> bool:
        """
        Step 1: Check if required Docker image is already pulled
        
        Returns:
            True if image exists, False otherwise
        """
        self.logger.subheader("STEP 1: Docker Image Validation")
        
        docker_image = self.test_config.get('docker_image', self.docker_image)
        
        # First check if docker is installed
        try:
            result = subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                self.logger.error("Docker is not installed or not accessible")
                self._show_docker_install_tutorial()
                return False
        except FileNotFoundError:
            self.logger.error("Docker is not installed or not in PATH")
            self._show_docker_install_tutorial()
            return False
        except Exception as e:
            self.logger.error(f"Error checking Docker installation: {e}")
            return False
        
        # Check if image exists
        try:
            result = subprocess.run(
                ["docker", "images", "-q", docker_image],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            image_id = result.stdout.strip()
            
            if image_id:
                self.logger.pass_msg(f"✓ Docker image '{docker_image}' found (ID: {image_id[:12]})")
                return True
            else:
                self.logger.warning(f"✗ Docker image '{docker_image}' not found locally")
                return self._pull_docker_image(docker_image)
                
        except Exception as e:
            self.logger.error(f"Error checking Docker image: {e}")
            return False
    
    def _pull_docker_image(self, image_name: str) -> bool:
        """Automatically pull Docker image"""
        self.logger.info(f"Attempting to pull Docker image: {image_name}")
        self.logger.info("This may take several minutes depending on image size and network speed...")
        
        try:
            # Get proxy settings
            http_proxy = self.test_config.get('http_proxy', self.http_proxy)
            https_proxy = self.test_config.get('https_proxy', self.https_proxy)
            
            # Set up environment with proxy if configured
            env = os.environ.copy()
            if http_proxy:
                env['HTTP_PROXY'] = http_proxy
                env['http_proxy'] = http_proxy
            if https_proxy:
                env['HTTPS_PROXY'] = https_proxy
                env['https_proxy'] = https_proxy
            
            # Pull the image
            process = subprocess.Popen(
                ["docker", "pull", image_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env
            )
            
            # Stream output
            for line in process.stdout:
                self.logger.info(f"  [PULL] {line.rstrip()}")
            
            process.wait()
            
            if process.returncode == 0:
                self.logger.pass_msg(f"✓ Docker image '{image_name}' pulled successfully")
                return True
            else:
                self.logger.fail_msg(f"✗ Failed to pull Docker image '{image_name}'")
                self._show_docker_pull_tutorial(image_name)
                return False
                
        except Exception as e:
            self.logger.error(f"Error pulling Docker image: {e}")
            self._show_docker_pull_tutorial(image_name)
            return False
    
    def _show_docker_install_tutorial(self):
        """Show tutorial for installing Docker"""
        tutorial = """
╔══════════════════════════════════════════════════════════════╗
║                 Docker Installation Required                 ║
╚══════════════════════════════════════════════════════════════╝

Docker is not installed or not accessible on this system.

To install Docker on Ubuntu:

    1. Update package index:
       sudo apt-get update

    2. Install Docker:
       sudo apt-get install -y docker.io

    3. Start Docker service:
       sudo systemctl start docker
       sudo systemctl enable docker

    4. Add user to docker group (optional, to run without sudo):
       sudo usermod -aG docker $USER
       newgrp docker

    5. Verify installation:
       docker --version

For other Linux distributions, visit: https://docs.docker.com/engine/install/

After installing Docker, re-run the test.
        """
        print(tutorial)
    
    def _show_docker_pull_tutorial(self, image_name: str):
        """Show tutorial for manually pulling Docker image and configuring
        proxy settings for the Docker daemon when behind a corporate proxy."""
        tutorial = f"""
╔══════════════════════════════════════════════════════════════╗
║              Manual Docker Image Pull Instructions           ║
╚══════════════════════════════════════════════════════════════╝

Automatic pull failed. To manually pull the Docker image:

    docker pull {image_name}

If you need authentication:

    1. Login to Docker Hub:
       docker login

    2. Enter your Docker Hub credentials

    3. Pull the image:
       docker pull {image_name}

If your system is behind a proxy server:

    1. Create/edit the Docker daemon proxy configuration:

       sudo mkdir -p /etc/systemd/system/docker.service.d
       sudo vi /etc/systemd/system/docker.service.d/http-proxy.conf

       Add the following (replace with your proxy URL and port):

       [Service]
       Environment="HTTP_PROXY=http://<your-proxy-host>:<port>"
       Environment="HTTPS_PROXY=http://<your-proxy-host>:<port>"
       Environment="NO_PROXY=localhost,127.0.0.1"

    2. Reload and restart the Docker daemon:
       sudo systemctl daemon-reload
       sudo systemctl restart docker

    3. Pull the image:
       docker pull {image_name}

Please pull the image and re-run the test.
        """
        print(tutorial)
    
    def check_dataset_and_model_files(self) -> bool:
        """
        Step 2: Check if dataset and model directories contain required files
        Automatically downloads datasets if missing or corrupted
        
        Returns:
            True if all required files exist and valid, False otherwise
        """
        self.logger.subheader("STEP 2: Dataset and Model Validation")
        
        data_dir = self.test_config.get('data_dir', self.data_dir)
        model_dir = self.test_config.get('model_dir', self.model_dir)
        model_type = self.test_config.get('model_type', 'llama3_1-8b')
        
        all_valid = True
        
        # Check and download dataset based on model type
        self.logger.info(f"Checking dataset for model: {model_type}")
        
        if model_type == 'llama2-70b':
            if not self._validate_and_download_llama2_dataset(data_dir):
                all_valid = False
        else:  # llama3_1-8b
            if not self._validate_and_download_llama3_dataset(data_dir):
                all_valid = False
        
        # Check model directory
        self.logger.info(f"Checking model directory: {model_dir}")
        if not self._validate_model_files(model_dir, model_type):
            all_valid = False
        
        return all_valid
    
    def _validate_model_files(self, model_dir: str, model_type: str) -> bool:
        """
        Validate model directory contains all expected files with correct hashes.
        
        Args:
            model_dir: Base model directory path (e.g. /home/user/model/.llama/checkpoints)
            model_type: Model type string ('llama3_1-8b' or 'llama2-70b')
        
        Returns:
            True if all required model files exist and pass hash verification, False otherwise
        """
        if model_type == 'llama2-70b':
            self.logger.warning("⚠ Llama2-70b model validation not yet configured - skipping")
            self.logger.info(f"  Expected model path: {model_dir}")
            return True
        
        # Llama3.1-8B model validation
        self.logger.info("Validating Llama-3.1-8B-Instruct model files...")
        
        model_subdir = self.env_config.get('LLAMA3_MODEL_SUBDIR', 'Llama-3.1-8B-Instruct')
        model_path = os.path.join(model_dir, model_subdir)
        
        # Build expected files dict from config.env (root-level files)
        expected_files = {}
        for i in range(1, 11):
            name = self.env_config.get(f'LLAMA3_MODEL_FILE{i}_NAME')
            hash_val = self.env_config.get(f'LLAMA3_MODEL_FILE{i}_HASH')
            if name and hash_val:
                expected_files[name] = hash_val
        
        # Build expected files for original/ subfolder
        expected_orig_files = {}
        for i in range(1, 10):  # up to 9 original files
            name = self.env_config.get(f'LLAMA3_MODEL_ORIG_FILE{i}_NAME')
            hash_val = self.env_config.get(f'LLAMA3_MODEL_ORIG_FILE{i}_HASH')
            if name and hash_val:
                expected_orig_files[name] = hash_val
        
        if not expected_files:
            self.logger.warning("⚠ No model file entries found in config.env - skipping validation")
            return True
        
        # Check if model directory exists
        if not os.path.exists(model_path):
            self.logger.fail_msg(f"✗ Model directory not found: {model_path}")
            self.logger.info("")
            self.logger.info("  To download the model, re-run with:")
            self.logger.info(f"    --download_model llama3.1-8b --hf_token <YOUR_HF_TOKEN>")
            self.logger.info("")
            self.logger.info("  Or download manually:")
            self.logger.info(f"    huggingface-cli download meta-llama/Llama-3.1-8B-Instruct --local-dir {model_path}")
            return False
        
        # Validate each file and its hash
        all_valid = True
        missing_files = []
        corrupt_files = []
        
        for filename, expected_hash in expected_files.items():
            filepath = os.path.join(model_path, filename)
            
            if not os.path.exists(filepath):
                self.logger.fail_msg(f"  ✗ Missing: {filename}")
                all_valid = False
                missing_files.append(filename)
                continue
            
            # Check file is not empty
            file_size = os.path.getsize(filepath)
            if file_size == 0:
                self.logger.fail_msg(f"  ✗ Empty file: {filename}")
                all_valid = False
                corrupt_files.append(filename)
                continue
            
            # Verify SHA256 hash
            self.logger.info(f"  Verifying: {filename} ({self._fmt_file_size(file_size)})...")
            actual_hash = self._calculate_sha256(filepath)
            if actual_hash != expected_hash:
                self.logger.fail_msg(f"  ✗ Hash mismatch: {filename}")
                self.logger.debug(f"    Expected: {expected_hash}")
                self.logger.debug(f"    Actual:   {actual_hash}")
                all_valid = False
                corrupt_files.append(filename)
            else:
                self.logger.pass_msg(f"  ✓ Validated: {filename}")
        
        # Validate original/ subfolder
        if expected_orig_files:
            orig_path = os.path.join(model_path, 'original')
            if not os.path.exists(orig_path):
                self.logger.fail_msg(f"  ✗ Missing subfolder: original/")
                all_valid = False
                missing_files.append('original/ (entire folder)')
            else:
                self.logger.info("  Validating original/ subfolder...")
                for filename, expected_hash in expected_orig_files.items():
                    filepath = os.path.join(orig_path, filename)
                    display_name = f"original/{filename}"
                    
                    if not os.path.exists(filepath):
                        self.logger.fail_msg(f"  ✗ Missing: {display_name}")
                        all_valid = False
                        missing_files.append(display_name)
                        continue
                    
                    file_size = os.path.getsize(filepath)
                    if file_size == 0:
                        self.logger.fail_msg(f"  ✗ Empty file: {display_name}")
                        all_valid = False
                        corrupt_files.append(display_name)
                        continue
                    
                    self.logger.info(f"  Verifying: {display_name} ({self._fmt_file_size(file_size)})...")
                    actual_hash = self._calculate_sha256(filepath)
                    if actual_hash != expected_hash:
                        self.logger.fail_msg(f"  ✗ Hash mismatch: {display_name}")
                        self.logger.debug(f"    Expected: {expected_hash}")
                        self.logger.debug(f"    Actual:   {actual_hash}")
                        all_valid = False
                        corrupt_files.append(display_name)
                    else:
                        self.logger.pass_msg(f"  ✓ Validated: {display_name}")
        
        if not all_valid:
            self.logger.fail_msg("✗ Model validation failed")
            if missing_files:
                self.logger.info(f"  Missing files ({len(missing_files)}): {', '.join(missing_files)}")
            if corrupt_files:
                self.logger.info(f"  Corrupt files ({len(corrupt_files)}): {', '.join(corrupt_files)}")
            self.logger.info("")
            self.logger.info("  To re-download the model, run with:")
            self.logger.info(f"    --download_model llama3.1-8b --hf_token <YOUR_HF_TOKEN>")
            return False
        
        self.logger.pass_msg("✓ Llama-3.1-8B-Instruct model validated successfully")
        return True
    
    @staticmethod
    def _fmt_file_size(size_bytes: int) -> str:
        """Format file size in human-readable units"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} PB"
    
    def _calculate_sha256(self, filepath: str) -> str:
        """Calculate SHA256 hash of a file"""
        sha256_hash = hashlib.sha256()
        try:
            with open(filepath, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            return sha256_hash.hexdigest()
        except Exception as e:
            self.logger.error(f"Error calculating SHA256 for {filepath}: {e}")
            return ""
    
    def _validate_and_download_llama2_dataset(self, data_dir: str) -> bool:
        """Validate and download Llama2-70b preprocessed dataset"""
        self.logger.info("Validating Llama2-70b dataset...")
        
        dataset_subdir = self.env_config.get('LLAMA2_DATASET_DIR', 'open_orca')
        open_orca_dir = os.path.join(data_dir, dataset_subdir)
        
        # Expected files with their SHA256 hashes from config
        expected_files = {
            self.env_config.get('LLAMA2_FILE1_NAME', 'mlperf_log_accuracy.json'): 
                self.env_config.get('LLAMA2_FILE1_HASH', 'ac6430953633492a24dfc7fbdd85d06d26f6668251e9318d0c765796a435be2a'),
            self.env_config.get('LLAMA2_FILE2_NAME', 'open_orca_gpt4_tokenized_llama.calibration_1000.pkl'): 
                self.env_config.get('LLAMA2_FILE2_HASH', 'ebaa728b30337c1e9a78ec1f6f729012138dc6f056d66cabd4af749c8c089db5'),
            self.env_config.get('LLAMA2_FILE3_NAME', 'open_orca_gpt4_tokenized_llama.sampled_24576.pkl'): 
                self.env_config.get('LLAMA2_FILE3_HASH', 'b64e66e54b6267f79eb4f9ccec52d466bab3ac94747ed258c3b0f337ed166fab'),
            self.env_config.get('LLAMA2_FILE4_NAME', 'reference_impl_gpu_bs32_fp32_output.pkl'): 
                self.env_config.get('LLAMA2_FILE4_HASH', 'a1505468cccd1facc785a800ee3ff4525c087091ccaeab54d25cb9478b48ec31')
        }
        
        # Check if open_orca folder exists
        if not os.path.exists(open_orca_dir):
            self.logger.warning(f"✗ Dataset directory not found: {open_orca_dir}")
            return self._download_llama2_dataset(data_dir)
        
        # Check for .gz files and decompress if needed
        gz_files = [f for f in os.listdir(open_orca_dir) if f.endswith('.gz')]
        if gz_files:
            self.logger.info(f"Found {len(gz_files)} compressed files, decompressing...")
            for gz_file in gz_files:
                gz_path = os.path.join(open_orca_dir, gz_file)
                try:
                    result = subprocess.run(
                        ['gunzip', gz_path],
                        capture_output=True,
                        text=True,
                        timeout=300
                    )
                    if result.returncode == 0:
                        self.logger.info(f"  ✓ Decompressed: {gz_file}")
                    else:
                        self.logger.warning(f"  ✗ Failed to decompress {gz_file}: {result.stderr}")
                except Exception as e:
                    self.logger.error(f"  Error decompressing {gz_file}: {e}")
        
        # Validate files and their hashes
        all_valid = True
        for filename, expected_hash in expected_files.items():
            filepath = os.path.join(open_orca_dir, filename)
            
            if not os.path.exists(filepath):
                self.logger.fail_msg(f"  ✗ Missing file: {filename}")
                all_valid = False
                continue
            
            # Verify hash
            actual_hash = self._calculate_sha256(filepath)
            if actual_hash != expected_hash:
                self.logger.fail_msg(f"  ✗ Hash mismatch for {filename}")
                self.logger.debug(f"    Expected: {expected_hash}")
                self.logger.debug(f"    Actual:   {actual_hash}")
                all_valid = False
            else:
                self.logger.pass_msg(f"  ✓ Validated: {filename}")
        
        if not all_valid:
            self.logger.warning("Dataset validation failed, re-downloading...")
            # Remove corrupted data
            import shutil
            if os.path.exists(open_orca_dir):
                shutil.rmtree(open_orca_dir)
            return self._download_llama2_dataset(data_dir)
        
        self.logger.pass_msg("✓ Llama2-70b dataset validated successfully")
        return True
    
    def _download_llama2_dataset(self, data_dir: str) -> bool:
        """Download Llama2-70b preprocessed dataset using rclone"""
        self.logger.info("Downloading Llama2-70b dataset...")
        self.logger.info("This may take a while depending on network speed...")
        
        os.makedirs(data_dir, exist_ok=True)
        
        # Get configuration values
        rclone_install_url = self.env_config.get('LLAMA2_RCLONE_INSTALL_URL', 'https://rclone.org/install.sh')
        remote_name = self.env_config.get('LLAMA2_RCLONE_REMOTE_NAME', 'mlc-inference')
        provider = self.env_config.get('LLAMA2_RCLONE_PROVIDER', 'Cloudflare')
        access_key = self.env_config.get('LLAMA2_RCLONE_ACCESS_KEY', 'f65ba5eef400db161ea49967de89f47b')
        secret_key = self.env_config.get('LLAMA2_RCLONE_SECRET_KEY', 'fbea333914c292b854f14d3fe232bad6c5407bf0ab1bebf78833c2b359bdfd2b')
        endpoint = self.env_config.get('LLAMA2_RCLONE_ENDPOINT', 'https://c2686074cb2caf5cbaf6d134bdba8b47.r2.cloudflarestorage.com')
        source = self.env_config.get('LLAMA2_RCLONE_SOURCE', 'mlc-inference:mlcommons-inference-wg-public/open_orca')
        dataset_subdir = self.env_config.get('LLAMA2_DATASET_DIR', 'open_orca')
        
        try:
            # Step 1: Check if rclone is already installed
            self.logger.info("Step 1/3: Checking/Installing rclone...")
            check_rclone = subprocess.run(
                ['which', 'rclone'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if check_rclone.returncode == 0:
                self.logger.pass_msg("  ✓ Rclone already installed")
            else:
                # Install rclone
                self.logger.info("  Installing rclone...")
                # Download install script first
                curl_process = subprocess.Popen(
                    ['curl', rclone_install_url],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                
                # Pipe to sudo bash
                bash_process = subprocess.Popen(
                    ['sudo', 'bash'],
                    stdin=curl_process.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                
                curl_process.stdout.close()  # Allow curl_process to receive a SIGPIPE if bash_process exits
                stdout, stderr = bash_process.communicate(timeout=300)
                
                if bash_process.returncode != 0:
                    self.logger.error(f"Failed to install rclone. Stdout: {stdout}")
                    self.logger.error(f"Stderr: {stderr}")
                    return False
                self.logger.pass_msg("  ✓ Rclone installed")
            
            # Step 2: Configure rclone
            self.logger.info("Step 2/3: Configuring rclone...")
            config_cmd = [
                'rclone', 'config', 'create', remote_name, 's3',
                f'provider={provider}',
                f'access_key_id={access_key}',
                f'secret_access_key={secret_key}',
                f'endpoint={endpoint}'
            ]
            result = subprocess.run(
                config_cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            # Ignore errors if config already exists
            self.logger.pass_msg("  ✓ Rclone configured")
            
            # Step 3: Download dataset
            self.logger.info("Step 3/3: Downloading dataset (this will take several minutes)...")
            download_cmd = [
                'rclone', 'copy',
                source,
                os.path.join(data_dir, dataset_subdir),
                '-P'
            ]
            
            process = subprocess.Popen(
                download_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            # Stream output
            for line in process.stdout:
                self.logger.info(f"  [DOWNLOAD] {line.rstrip()}")
            
            process.wait()
            
            if process.returncode == 0:
                self.logger.pass_msg("✓ Llama2-70b dataset downloaded successfully")
                # Validate the downloaded dataset
                return self._validate_and_download_llama2_dataset(data_dir)
            else:
                self.logger.fail_msg("✗ Failed to download Llama2-70b dataset")
                return False
                
        except Exception as e:
            self.logger.error(f"Error downloading Llama2-70b dataset: {e}")
            return False
    
    def _validate_and_download_llama3_dataset(self, data_dir: str) -> bool:
        """Validate and download Llama3.1-8b preprocessed dataset"""
        self.logger.info("Validating Llama3.1-8b dataset...")
        
        # Expected files with their SHA256 hashes from config
        expected_files = {
            self.env_config.get('LLAMA3_FILE1_NAME', 'cnn_eval.json'): 
                self.env_config.get('LLAMA3_FILE1_HASH', '6d066a21bd615909466885d53b664b73c5aabc32d60c1cbd03f4293de6430026'),
            self.env_config.get('LLAMA3_FILE2_NAME', 'llama3-1-8b-cnn-eval.md5'): 
                self.env_config.get('LLAMA3_FILE2_HASH', '1df6da22e31d833ea30a2f77df129aedcabf3b6142c130ea9935c0becf22742d'),
            self.env_config.get('LLAMA3_FILE3_NAME', 'llama3-1-8b-sample-cnn-eval-5000.md5'): 
                self.env_config.get('LLAMA3_FILE3_HASH', '7756dcec0a2aa352b5a469304336c477e3b22902b84c6bdee816b4bdf8c668cd'),
            self.env_config.get('LLAMA3_FILE4_NAME', 'sample_cnn_eval_5000.json'): 
                self.env_config.get('LLAMA3_FILE4_HASH', '2d1a21eadd8ef7547d2f5fba939a9e1886a22ee926fe3781cb8934d3a01445db'),
            self.env_config.get('LLAMA3_FILE5_NAME', 'cnn_dailymail_calibration.json'): 
                self.env_config.get('LLAMA3_FILE5_HASH', 'a2ca44ea25bbb149beb9fec01dc54078f93f2228218c6e40049829f8fb3250a0'),
            self.env_config.get('LLAMA3_FILE6_NAME', 'llama3-1-8b-cnn-dailymail-calibration.md5'): 
                self.env_config.get('LLAMA3_FILE6_HASH', '72404551a374e988b64a31d3a3aaf4f30a294a6228bd7e4a4c6a25e3268c0252')
        }
        
        # Check if data directory exists
        if not os.path.exists(data_dir):
            self.logger.warning(f"✗ Data directory not found: {data_dir}")
            return self._download_llama3_dataset(data_dir)
        
        # Validate files and their hashes
        all_valid = True
        missing_files = []
        
        for filename, expected_hash in expected_files.items():
            filepath = os.path.join(data_dir, filename)
            
            if not os.path.exists(filepath):
                self.logger.fail_msg(f"  ✗ Missing file: {filename}")
                all_valid = False
                missing_files.append(filename)
                continue
            
            # Verify hash
            actual_hash = self._calculate_sha256(filepath)
            if actual_hash != expected_hash:
                self.logger.fail_msg(f"  ✗ Hash mismatch for {filename}")
                self.logger.debug(f"    Expected: {expected_hash}")
                self.logger.debug(f"    Actual:   {actual_hash}")
                all_valid = False
                # Delete corrupted file
                try:
                    os.remove(filepath)
                    self.logger.info(f"  Removed corrupted file: {filename}")
                    missing_files.append(filename)
                except Exception as e:
                    self.logger.error(f"  Error removing {filename}: {e}")
            else:
                self.logger.pass_msg(f"  ✓ Validated: {filename}")
        
        if not all_valid:
            self.logger.warning("Dataset validation failed, downloading missing/corrupted files...")
            return self._download_llama3_dataset(data_dir)
        
        self.logger.pass_msg("✓ Llama3.1-8b dataset validated successfully")
        return True
    
    def _download_llama3_dataset(self, data_dir: str) -> bool:
        """Download Llama3.1-8b preprocessed dataset"""
        self.logger.info("Downloading Llama3.1-8b dataset...")
        self.logger.info("This may take a while depending on network speed...")
        
        os.makedirs(data_dir, exist_ok=True)
        
        # Get configuration values
        downloader_url = self.env_config.get('LLAMA3_DOWNLOADER_URL', 'https://raw.githubusercontent.com/mlcommons/r2-downloader/refs/heads/main/mlc-r2-downloader.sh')
        downloader_script_name = self.env_config.get('LLAMA3_DOWNLOADER_SCRIPT', 'mlc-r2-downloader.sh')
        dataset1_uri = self.env_config.get('LLAMA3_DATASET1_URI', 'https://inference.mlcommons-storage.org/metadata/llama3-1-8b-cnn-eval.uri')
        dataset2_uri = self.env_config.get('LLAMA3_DATASET2_URI', 'https://inference.mlcommons-storage.org/metadata/llama3-1-8b-sample-cnn-eval-5000.uri')
        dataset3_uri = self.env_config.get('LLAMA3_DATASET3_URI', 'https://inference.mlcommons-storage.org/metadata/llama3-1-8b-cnn-dailymail-calibration.uri')
        
        # Proxy env for all subprocess calls
        env = os.environ.copy()
        if self.http_proxy:
            env['HTTP_PROXY']  = self.http_proxy
            env['http_proxy']  = self.http_proxy
        if self.https_proxy:
            env['HTTPS_PROXY'] = self.https_proxy
            env['https_proxy'] = self.https_proxy
        
        try:
            # Step 1: Download the downloader script
            self.logger.info("Step 1/4: Downloading mlc-r2-downloader script...")
            downloader_script = os.path.join(data_dir, downloader_script_name)
            wget_cmd = [
                'wget',
                downloader_url,
                '-O', downloader_script
            ]
            result = subprocess.run(
                wget_cmd,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=data_dir,
                env=env
            )
            if result.returncode != 0:
                self.logger.error(f"Failed to download downloader script: {result.stderr}")
                return False
            self.logger.pass_msg("  ✓ Downloader script downloaded")
            
            # Step 2: Download cnn_eval dataset (full datacenter)
            self.logger.info("Step 2/4: Downloading cnn_eval dataset (full datacenter)...")
            download_cmd1 = [
                'bash', downloader_script,
                dataset1_uri
            ]
            
            process = subprocess.Popen(
                download_cmd1,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=data_dir,
                env=env
            )
            
            # Stream output
            for line in process.stdout:
                self.logger.info(f"  [DOWNLOAD] {line.rstrip()}")
            
            process.wait()
            
            if process.returncode != 0:
                self.logger.fail_msg("✗ Failed to download cnn_eval dataset")
                return False
            self.logger.pass_msg("  ✓ cnn_eval dataset downloaded")
            
            # Step 3: Download sample_cnn_eval_5000 dataset (edge)
            self.logger.info("Step 3/4: Downloading sample_cnn_eval_5000 dataset (edge)...")
            download_cmd2 = [
                'bash', downloader_script,
                dataset2_uri
            ]
            
            process = subprocess.Popen(
                download_cmd2,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=data_dir,
                env=env
            )
            
            # Stream output
            for line in process.stdout:
                self.logger.info(f"  [DOWNLOAD] {line.rstrip()}")
            
            process.wait()
            
            if process.returncode != 0:
                self.logger.fail_msg("✗ Failed to download sample_cnn_eval_5000 dataset")
                return False
            self.logger.pass_msg("  ✓ sample_cnn_eval_5000 dataset downloaded")
            
            # Step 4: Download cnn_dailymail_calibration dataset (calibration)
            self.logger.info("Step 4/4: Downloading cnn_dailymail_calibration dataset (calibration)...")
            download_cmd3 = [
                'bash', downloader_script,
                dataset3_uri
            ]
            
            process = subprocess.Popen(
                download_cmd3,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=data_dir,
                env=env
            )
            
            # Stream output
            for line in process.stdout:
                self.logger.info(f"  [DOWNLOAD] {line.rstrip()}")
            
            process.wait()
            
            if process.returncode != 0:
                self.logger.fail_msg("✗ Failed to download cnn_dailymail_calibration dataset")
                return False
            self.logger.pass_msg("  ✓ cnn_dailymail_calibration dataset downloaded")
            
            # Validate the downloaded dataset
            self.logger.info("Validating downloaded files...")
            return self._validate_and_download_llama3_dataset(data_dir)
                
        except Exception as e:
            self.logger.error(f"Error downloading Llama3.1-8b dataset: {e}")
            return False
    
    def _show_dataset_model_tutorial(self, data_dir: str, model_dir: str):
        """Show tutorial for downloading dataset and model files"""
        tutorial = f"""
╔══════════════════════════════════════════════════════════════╗
║            Dataset and Model Download Tutorial               ║
╚══════════════════════════════════════════════════════════════╝

Required files are missing from the dataset or model directories.

Dataset Directory: {data_dir}
Model Directory: {model_dir}

To download the required files:

1. Download MLPerf Dataset:
   
   # Create dataset directory
   mkdir -p {data_dir}
   
   # Download dataset (example for LLAMA)
   cd {data_dir}
   wget <dataset_url>
   # Or use provided download script

2. Download Model Files:
   
   # Create model directory
   mkdir -p {model_dir}
   
   # Option A: Use Hugging Face CLI
   pip install huggingface-hub
   huggingface-cli download <model_name> --local-dir {model_dir}
   
   # Option B: Use git-lfs
   git lfs install
   git clone https://huggingface.co/<model_name> {model_dir}
   
   # Option C: Manual download from Hugging Face
   # Visit: https://huggingface.co/<model_name>
   # Download all files to {model_dir}

3. Verify Files:
   
   # Check dataset files
   ls -lh {data_dir}
   
   # Check model files
   ls -lh {model_dir}

Common Model Files Required:
  - config.json
  - pytorch_model.bin (or model.safetensors)
  - tokenizer.json
  - tokenizer_config.json
  - special_tokens_map.json

Please download the required files and re-run the test.
        """
        print(tutorial)
    
    def launch_docker_container(self) -> bool:
        """
        Step 3: Export environment variables and launch Docker container
        
        Returns:
            True if container launched successfully, False otherwise
        """
        self.logger.subheader("STEP 3: Docker Container Launch")
        
        # Get configuration
        docker_image = self.test_config.get('docker_image', self.docker_image)
        data_dir = os.path.abspath(self.test_config.get('data_dir', self.data_dir))
        model_dir = os.path.abspath(self.test_config.get('model_dir', self.model_dir))
        log_dir = os.path.abspath(self.test_config.get('log_dir', self.log_dir))
        http_proxy = self.test_config.get('http_proxy', self.http_proxy)
        https_proxy = self.test_config.get('https_proxy', self.https_proxy)
        
        # Create directories if they don't exist
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(model_dir, exist_ok=True)
        os.makedirs(log_dir, exist_ok=True)
        # SECURITY: Set restrictive permissions on log directory (user only)
        os.chmod(log_dir, 0o700)
        
        # Build Docker run command
        cmd = [
            "docker", "run",
            "--privileged", "-it", "-d",
            "--name", self.container_name,
            "--ipc=host", "--net=host",
            "--device", "/dev/dri:/dev/dri",
            "-v", "/dev/dri/by-path:/dev/dri/by-path",
        ]
        
        # Add proxy environment variables
        if http_proxy:
            cmd.extend(["-e", f"http_proxy={http_proxy}"])
        if https_proxy:
            cmd.extend(["-e", f"https_proxy={https_proxy}"])
        
        # Add volume mounts
        cmd.extend([
            "-v", f"{data_dir}:/data",
            "-v", f"{model_dir}:/model",
            "-v", f"{log_dir}:/logs",
        ])
        
        # Add benchmark configuration as environment variables
        cmd.extend([
            "-e", f"MODEL_NAME={self.test_config.get('model_name', '')}",
            "-e", f"MAX_OUTPUT_TOKENS={self.test_config.get('max_tokens', '128')}",
            "-e", f"INPUT_LENGTH={self.test_config.get('input_length', '64')}",
            "-e", f"CONCURRENCY={self.test_config.get('concurrency', '2')}",
        ])
        
        # Set workdir and image
        cmd.extend([
            "--workdir", "/workspace",
            docker_image,
            "/bin/bash"
        ])
        
        self.logger.info("Launching Docker container...")
        self.logger.debug(f"Command: {' '.join(cmd)}")
        
        try:
            # First, check if container already exists and remove it
            self._cleanup_existing_container()
            
            # Launch container
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                self.logger.error(f"Failed to launch container: {result.stderr}")
                return False
            
            self.container_id = result.stdout.strip()
            self.is_container_running = True
            
            self.logger.pass_msg(f"✓ Container launched successfully (ID: {self.container_id[:12]})")
            
            # Wait for container to be ready
            time.sleep(3)
            
            # Verify container is running
            if self._verify_container_running():
                return True
            else:
                return False
                
        except Exception as e:
            self.logger.error(f"Error launching container: {e}")
            return False
    
    def _cleanup_existing_container(self):
        """Remove existing container with the same name"""
        try:
            result = subprocess.run(
                ["docker", "rm", "-f", self.container_name],
                capture_output=True,
                timeout=10
            )
        except Exception as e:
            stderr = None
            if 'result' in locals() and hasattr(result, 'stderr'):
                stderr = result.stderr.decode() if isinstance(result.stderr, bytes) else result.stderr
            self.logger.debug(f"Error cleaning up container '{self.container_name}': {e}. Stderr: {stderr}")
    def _verify_container_running(self) -> bool:
        """Verify container is running"""
        try:
            result = subprocess.run(
                ["docker", "ps", "-q", "-f", f"name={self.container_name}"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.stdout.strip():
                self.logger.pass_msg("✓ Container is running")
                return True
            else:
                self.logger.fail_msg("✗ Container is not running")
                return False
                
        except Exception as e:
            self.logger.error(f"Error verifying container: {e}")
            return False
    
    def check_and_run_calibration(self) -> bool:
        """
        Step 4: Check if model has been calibrated by verifying the calibrated
        model directory and expected file names inside the Docker container.
        If not calibrated, run the calibration script automatically.
        
        Checks for /model/<LLAMA3_CALIBRATED_SUBDIR>/ and verifies that all
        expected files (from config.env) exist and are non-empty.
        Calibration is a one-time effort; once the files are present,
        subsequent container launches skip re-calibration.
        
        Returns:
            True if calibration completed or already done, False on error
        """
        self.logger.subheader("STEP 4: Model Calibration Check")
        
        model_type = self.test_config.get('model_type', 'llama3_1-8b')
        
        if model_type == 'llama2-70b':
            self.logger.warning("⚠ Llama2-70b calibration validation not yet configured - skipping")
            return True
        
        # Build expected calibrated file list from config.env
        calibrated_subdir = self.env_config.get('LLAMA3_CALIBRATED_SUBDIR', 'Llama-3.1-8B-Instruct-autoround-w4g-1-iters512-xpu')
        calibrated_path = f"/model/{calibrated_subdir}"
        
        expected_files = []
        for i in range(1, 15):  # up to 14 calibrated files
            name = self.env_config.get(f'LLAMA3_CALIB_FILE{i}_NAME')
            if name:
                expected_files.append(name)
        
        if not expected_files:
            self.logger.warning("⚠ No calibrated model file entries found in config.env - skipping validation")
            return True
        
        self.logger.info(f"Validating calibrated model: {calibrated_path}")
        self.logger.info(f"  Expected files: {len(expected_files)}")
        
        # Check if calibrated model directory exists in container
        try:
            result = subprocess.run(
                ["docker", "exec", self.container_name, "test", "-d", calibrated_path],
                capture_output=True,
                timeout=10
            )
            
            if result.returncode != 0:
                self.logger.warning(f"✗ Calibrated model directory not found: {calibrated_path}")
                self.logger.info("Model needs calibration, running calibration script...")
                return self._run_calibration_and_validate(calibrated_path, expected_files)
        except Exception as e:
            self.logger.error(f"Error checking calibrated model directory: {e}")
            return False
        
        # Validate each calibrated file exists and is non-empty
        all_valid = True
        missing_files = []
        
        for filename in expected_files:
            filepath = f"{calibrated_path}/{filename}"
            
            # Check file existence and non-empty
            check_cmd = [
                "docker", "exec", self.container_name, "bash", "-c",
                f'test -f "{filepath}" && test -s "{filepath}"'
            ]
            
            try:
                result = subprocess.run(
                    check_cmd,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if result.returncode != 0:
                    self.logger.fail_msg(f"  ✗ Missing or empty: {filename}")
                    all_valid = False
                    missing_files.append(filename)
                else:
                    self.logger.pass_msg(f"  ✓ Found: {filename}")
                    
            except subprocess.TimeoutExpired:
                self.logger.warning(f"  ⚠ Timeout checking {filename}")
            except Exception as e:
                self.logger.error(f"  Error checking {filename}: {e}")
                all_valid = False
        
        # Validate safetensors shard files dynamically
        # Accept any model-NNNNN-of-NNNNN.safetensors pattern and count
        if not self._validate_safetensors_shards(calibrated_path):
            all_valid = False
        
        if all_valid:
            self.logger.pass_msg(f"✓ Calibrated model validated: {calibrated_subdir}")
            return True
        
        # Calibration invalid - report and re-calibrate
        if missing_files:
            self.logger.info(f"  Missing files ({len(missing_files)}): {', '.join(missing_files)}")
        
        self.logger.info("Model needs (re-)calibration, running calibration script...")
        return self._run_calibration_and_validate(calibrated_path, expected_files)
    
    def _validate_safetensors_shards(self, calibrated_path: str) -> bool:
        """
        Validate that safetensors model shard files exist in the calibrated
        model directory inside the container.
        
        Accepts any number of shards matching the pattern
        model-NNNNN-of-NNNNN.safetensors (e.g. 2 shards or 7 shards).
        Verifies that all expected shards are present and non-empty based
        on the total count declared in the filenames.
        
        Args:
            calibrated_path: Path to calibrated model directory in container
        
        Returns:
            True if shard files are valid, False otherwise
        """
        
        # List all model-*-of-*.safetensors files in the directory
        list_cmd = [
            "docker", "exec", self.container_name, "bash", "-c",
            f'ls -1 "{calibrated_path}/"model-*-of-*.safetensors 2>/dev/null'
        ]
        
        try:
            result = subprocess.run(
                list_cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0 or not result.stdout.strip():
                self.logger.fail_msg("  ✗ No safetensors shard files found (model-NNNNN-of-NNNNN.safetensors)")
                return False
            
            found_files = [os.path.basename(f) for f in result.stdout.strip().splitlines()]
            
            # Parse shard numbers: model-00001-of-00007.safetensors
            shard_pattern = re.compile(r'^model-(\d+)-of-(\d+)\.safetensors$')
            shard_indices = []
            total_shards = None
            
            for fname in found_files:
                m = shard_pattern.match(fname)
                if not m:
                    continue
                idx, total = int(m.group(1)), int(m.group(2))
                shard_indices.append(idx)
                if total_shards is None:
                    total_shards = total
                elif total != total_shards:
                    self.logger.fail_msg(f"  ✗ Inconsistent shard totals: {fname} declares {total}, expected {total_shards}")
                    return False
            
            if total_shards is None or not shard_indices:
                self.logger.fail_msg("  ✗ No valid safetensors shard files found")
                return False
            
            # Check all shards 1..total_shards are present
            expected_indices = set(range(1, total_shards + 1))
            found_indices = set(shard_indices)
            missing = sorted(expected_indices - found_indices)
            
            if missing:
                missing_names = [f"model-{i:05d}-of-{total_shards:05d}.safetensors" for i in missing]
                self.logger.fail_msg(f"  ✗ Missing shard files: {', '.join(missing_names)}")
                return False
            
            # Verify all shard files are non-empty
            for fname in found_files:
                filepath = f"{calibrated_path}/{fname}"
                check_cmd = [
                    "docker", "exec", self.container_name, "bash", "-c",
                    f'test -s "{filepath}"'
                ]
                chk = subprocess.run(check_cmd, capture_output=True, timeout=30)
                if chk.returncode != 0:
                    self.logger.fail_msg(f"  ✗ Empty shard file: {fname}")
                    return False
            
            self.logger.pass_msg(f"  ✓ Found {len(found_indices)}/{total_shards} safetensors shards")
            return True
            
        except subprocess.TimeoutExpired:
            self.logger.warning("  ⚠ Timeout listing safetensors shard files")
            return False
        except Exception as e:
            self.logger.error(f"  Error validating safetensors shards: {e}")
            return False
    
    def _run_calibration_and_validate(self, calibrated_path: str, expected_files: list) -> bool:
        """
        Run calibration script inside container and validate the result.
        
        Args:
            calibrated_path: Path to calibrated model directory in container
            expected_files: List of expected filenames in the calibrated directory
        
        Returns:
            True if calibration succeeded and files are present, False otherwise
        """
        calibration_script = self.test_config.get('calibration_script', 'bash scripts/run_calibration.sh')
        
        # Workaround: Patch run_quantization.py inside the container to fix
        # Conv1D AttributeError (transformers removed Conv1D from modeling_utils
        # in v4.42+; the check is dead code for Llama models anyway).
        self._patch_container_conv1d()
        
        # Workaround: Install auto_round package if missing in the container.
        # The Docker image may not ship auto_round pre-installed, but
        # run_quantization.py imports it for model calibration.
        if not self._install_auto_round():
            self.logger.error("Aborting calibration — auto_round is required but could not be installed")
            return False
        
        if not self._run_calibration_script(calibration_script):
            return False
        
        # Post-calibration validation — check file existence only
        self.logger.info("Validating calibrated model files...")
        
        all_valid = True
        for filename in expected_files:
            filepath = f"{calibrated_path}/{filename}"
            check_cmd = [
                "docker", "exec", self.container_name, "bash", "-c",
                f'test -f "{filepath}" && test -s "{filepath}"'
            ]
            try:
                result = subprocess.run(
                    check_cmd,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.returncode != 0:
                    self.logger.fail_msg(f"  ✗ Missing after calibration: {filename}")
                    all_valid = False
                else:
                    self.logger.pass_msg(f"  ✓ Found: {filename}")
            except Exception as e:
                self.logger.error(f"  Error validating {filename}: {e}")
                all_valid = False
        
        # Validate safetensors shard files dynamically
        if not self._validate_safetensors_shards(calibrated_path):
            all_valid = False
        
        if all_valid:
            self.logger.pass_msg("✓ Post-calibration validation passed")
        else:
            self.logger.fail_msg("✗ Post-calibration validation failed - expected files missing")
        
        return all_valid
    
    def _patch_container_conv1d(self):
        """Patch run_quantization.py inside the container to work around
        the removed transformers.modeling_utils.Conv1D attribute.
        
        The original line:
            if isinstance(m, torch.nn.Linear) or isinstance(m, transformers.modeling_utils.Conv1D):
        is replaced with a safe getattr fallback.  Conv1D is irrelevant for
        Llama models (it was a GPT-2-era layer type), so this is a no-op patch.
        """
        target_file = '/workspace/run_quantization.py'
        self.logger.info(f"  [PATCH] Checking {target_file} for Conv1D compatibility...")
        
        # sed: replace the problematic isinstance check with a safe getattr fallback
        sed_cmd = (
            r"sed -i "
            r"'s/isinstance(m, transformers\.modeling_utils\.Conv1D)/"
            r"isinstance(m, getattr(transformers.modeling_utils, \"Conv1D\", type(None)))/g' "
            f"{target_file}"
        )
        
        try:
            result = subprocess.run(
                ["docker", "exec", self.container_name, "/bin/bash", "-c", sed_cmd],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                self.logger.pass_msg(f"  [PATCH] ✓ Conv1D compatibility patch applied")
            else:
                self.logger.warning(f"  [PATCH] Could not patch {target_file} (rc={result.returncode}): {result.stderr.strip()}")
                self.logger.warning("  [PATCH] Calibration may still fail — consider updating the Docker image")
        except Exception as e:
            self.logger.warning(f"  [PATCH] Patch attempt failed: {e}")
            self.logger.warning("  [PATCH] Calibration may still fail — consider updating the Docker image")

    def _patch_user_conf_for_gpu_count(self):
        """Generate user.conf files for other GPU counts from the 4x source.

        Inside the MLPerf container, /workspace/systems/user.conf.1-node-4x-BMG-B60
        (and the corresponding B70 file) define target_qps values for a 4-GPU
        configuration.  When fewer GPUs are present, the benchmark fails because
        the matching user.conf files do not exist.

        This workaround copies each 4x file and scales every *.target_qps value
        proportionally:
            3x  ->  value * 3/4
            2x  ->  value * 2/4
            1x  ->  value * 1/4
        Non-target_qps lines (e.g. *.min_query_count) are copied as-is.
        """
        self.logger.info("  [PATCH] Generating user.conf files for 3x, 2x and 1x GPU counts...")

        variants = ['BMG-B60', 'BMG-B70']
        scale_map = {
            '3x': 3.0 / 4.0,
            '2x': 2.0 / 4.0,
            '1x': 1.0 / 4.0,
        }

        for variant in variants:
            src = f'/workspace/systems/user.conf.1-node-4x-{variant}'

            # Read the 4x source file
            try:
                read_result = subprocess.run(
                    ["docker", "exec", self.container_name, "cat", src],
                    capture_output=True, text=True, timeout=10
                )
                if read_result.returncode != 0:
                    self.logger.warning(f"  [PATCH] Source file not found: {src} – skipping {variant}")
                    continue
            except Exception as e:
                self.logger.warning(f"  [PATCH] Error reading {src}: {e}")
                continue

            src_content = read_result.stdout

            for gpu_tag, factor in scale_map.items():
                dst = f'/workspace/systems/user.conf.1-node-{gpu_tag}-{variant}'
                new_lines = []

                for line in src_content.splitlines():
                    stripped = line.strip()
                    # Scale lines matching  *.target_qps = <number>
                    if stripped and '.target_qps' in stripped and '=' in stripped:
                        lhs, rhs = stripped.split('=', 1)
                        try:
                            original_val = float(rhs.strip())
                            scaled_val = round(original_val * factor, 2)
                            # Remove unnecessary trailing zeros (e.g. 6.50 -> 6.5)
                            formatted = f'{scaled_val:g}'
                            new_lines.append(f'{lhs.strip()} = {formatted}')
                        except ValueError:
                            new_lines.append(line)
                    else:
                        new_lines.append(line)

                file_body = '\n'.join(new_lines) + '\n'

                # Write the scaled file into the container
                try:
                    write_result = subprocess.run(
                        ["docker", "exec", "-i", self.container_name,
                         "bash", "-c", f"cat > {dst}"],
                        input=file_body, capture_output=True, text=True, timeout=10
                    )
                    if write_result.returncode == 0:
                        self.logger.info(f"  [PATCH] ✓ Created {dst} (factor {factor})")
                    else:
                        self.logger.warning(f"  [PATCH] Failed to write {dst}: {write_result.stderr}")
                except Exception as e:
                    self.logger.warning(f"  [PATCH] Error writing {dst}: {e}")

        self.logger.info("  [PATCH] user.conf GPU-count patch complete")

    def _install_auto_round(self) -> bool:
        """Install auto_round package inside the container if it is missing.

        run_quantization.py imports auto_round for INT4 weight quantisation.
        Some Docker image versions omit it, causing ModuleNotFoundError.

        Returns:
            True if auto_round is available, False if installation failed.
        """
        self.logger.info("  [PATCH] Checking for auto_round package in container...")

        # Quick probe – import auto_round in one-liner
        probe_cmd = [
            "docker", "exec", self.container_name,
            "python", "-c", "import auto_round"
        ]
        try:
            result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                self.logger.pass_msg("  [PATCH] ✓ auto_round already installed")
                return True
        except Exception:
            pass  # fall through to install

        self.logger.info("  [PATCH] auto_round not found – installing via pip...")
        install_cmd = [
            "docker", "exec", self.container_name,
            "pip", "install", "auto_round"
        ]
        try:
            result = subprocess.run(
                install_cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 min upper bound for pip install
            )
            if result.returncode == 0:
                self.logger.pass_msg("  [PATCH] ✓ auto_round installed successfully")
                return True
            else:
                self.logger.warning(
                    f"  [PATCH] pip install auto_round failed (rc={result.returncode}): "
                    f"{result.stderr.strip()}"
                )
                self.logger.warning("  [PATCH] Calibration will likely fail — consider updating the Docker image")
                return False
        except subprocess.TimeoutExpired:
            self.logger.warning("  [PATCH] pip install auto_round timed out")
            return False
        except Exception as e:
            self.logger.warning(f"  [PATCH] Failed to install auto_round: {e}")
            return False

    # Error patterns that indicate a failed calibration even if exit code is 0
    _CALIBRATION_ERROR_PATTERNS = re.compile(
        r'Traceback \(most recent call last\)|'
        r'(?:AttributeError|RuntimeError|ModuleNotFoundError|ImportError|TypeError|ValueError|OSError|FileNotFoundError):\s',
        re.IGNORECASE
    )

    def _run_calibration_script(self, script_cmd: str) -> bool:
        """Execute calibration script inside container
        
        Args:
            script_cmd: Calibration command to run (e.g. 'bash scripts/run_calibration.sh')
        """
        self.logger.info(f"Running calibration: {script_cmd}")
        self.logger.info("This may take a long time (30+ minutes) depending on GPU performance...")
        
        # Wrap with 'set -e' so bash propagates non-zero exit codes from inner commands
        wrapped_cmd = f"set -e; {script_cmd}"
        exec_cmd = ["docker", "exec", self.container_name, "/bin/bash", "-c", wrapped_cmd]
        
        try:
            process = subprocess.Popen(
                exec_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            # Stream output and scan for error indicators
            error_lines = []
            for line in process.stdout:
                stripped = line.rstrip()
                self.logger.info(f"  [CALIBRATION] {stripped}")
                if self._CALIBRATION_ERROR_PATTERNS.search(stripped):
                    error_lines.append(stripped)
            
            # Timeout: 3 hours upper bound for calibration (typically 30+ minutes)
            process.wait(timeout=10800)
            
            # Check exit code first
            if process.returncode != 0:
                self.logger.fail_msg(f"✗ Calibration failed with return code {process.returncode}")
                return False
            
            # Even with exit code 0, fail if error patterns were detected in output
            if error_lines:
                self.logger.fail_msg("✗ Calibration script exited 0 but errors were detected in output:")
                for err in error_lines[:5]:  # Show up to 5 error lines
                    self.logger.error(f"    {err}")
                if len(error_lines) > 5:
                    self.logger.error(f"    ... and {len(error_lines) - 5} more error(s)")
                return False
            
            self.logger.pass_msg("✓ Calibration script completed successfully")
            return True
        
        except subprocess.TimeoutExpired:
            self.logger.error("Calibration timed out after 3 hours. The process may still be running in the container.")
            process.kill()
            return False
        except Exception as e:
            self.logger.error(f"Error running calibration: {e}")
            return False
    
    def _apply_container_patches(self):
        """Step 5: Apply container patches before benchmark execution."""
        self.logger.subheader("STEP 5: Container Patches")
        self._patch_user_conf_for_gpu_count()
    
    def run_mlperf_benchmark(self) -> bool:
        """
        Step 6: Run MLPerf benchmark test and collect results
        
        Returns:
            True if benchmark completed successfully, False otherwise
        """
        self.logger.subheader("STEP 6: MLPerf Benchmark Execution")
        
        benchmark_script = self.test_config.get('benchmark_script')
        
        if not benchmark_script:
            self.logger.fail_msg("No benchmark script specified!")
            return False
        
        self.logger.info(f"Running benchmark script: {benchmark_script}")
        
        # Run benchmark inside container
        exec_cmd = ["docker", "exec", self.container_name, "/bin/bash", "-c", benchmark_script]
        
        try:
            start_time = time.time()
            
            process = subprocess.Popen(
                exec_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            # Stream output and collect results
            benchmark_output = []
            for line in process.stdout:
                self.logger.info(f"  [BENCHMARK] {line.rstrip()}")
                benchmark_output.append(line)
            
            process.wait()
            
            elapsed_time = time.time() - start_time
            
            if process.returncode == 0:
                self.logger.pass_msg(f"✓ Benchmark completed in {elapsed_time:.2f} seconds")
                
                # Save output to file
                self._save_benchmark_output(benchmark_output)
                
                return True
            else:
                self.logger.fail_msg(f"✗ Benchmark failed with return code {process.returncode}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error running benchmark: {e}")
            return False
    
    def _save_benchmark_output(self, output: List[str]):
        """Save benchmark output to log file"""
        log_dir = self.test_config.get('log_dir', self.log_dir)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(log_dir, f"mlperf_benchmark_{timestamp}.log")
        
        try:
            with open(output_file, 'w') as f:
                f.writelines(output)
            self.logger.info(f"Benchmark output saved to: {output_file}")
        except Exception as e:
            self.logger.warning(f"Could not save benchmark output: {e}")
    
    def present_results(self) -> bool:
        """
        Present test results on terminal console.
        Parses MLPerf benchmark output from gpu_test_results and displays
        structured results using the framework's printTableFromDict.
        
        Returns:
            True if results presented successfully
        """
        log_dir = self.test_config.get('log_dir', self.log_dir)
        container_log_path = "/logs"
        
        self.logger.info("Collecting results from container...")
        
        # List files in container logs directory
        list_cmd = ["docker", "exec", self.container_name, "ls", "-lh", container_log_path]
        try:
            result = subprocess.run(list_cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                self.logger.info("Results in container:")
                for line in result.stdout.splitlines():
                    self.logger.info(f"  {line}")
        except Exception as e:
            self.logger.warning(f"Could not list container log directory: {e}")
        
        # Parse MLPerf output from captured benchmark stdout
        benchmark_output = ''
        if hasattr(self, 'gpu_test_results') and self.gpu_test_results:
            benchmark_output = self.gpu_test_results[0] if self.gpu_test_results[0] else ''
        
        parsed = self._parse_mlperf_output(benchmark_output)
        
        if not parsed:
            self.logger.warning("Could not parse MLPerf benchmark output from process stdout")
            return False
        
        # ── MLPerf Results Summary Table ──
        summary = parsed.get('summary', {})
        if summary:
            self.logger.info('')
            self.logger.subheader('MLPerf Results Summary')
            summary_table = {
                'MLPerf_Result': {
                    'SUT Name':   summary.get('sut_name', 'N/A'),
                    'Scenario':   summary.get('scenario', 'N/A'),
                    'Mode':       summary.get('mode', 'N/A'),
                    'Samples/s':  summary.get('samples_per_second', 'N/A'),
                    'Tokens/s':   summary.get('tokens_per_second', 'N/A'),
                    'Result':     summary.get('result', 'N/A'),
                }
            }
            self.logger.printTableFromDict(summary_table)
            
            # Display validation checks
            for check_name, check_val in summary.get('checks', {}).items():
                if check_val.lower() == 'yes':
                    self.logger.pass_msg(f"  ✓ {check_name}: {check_val}")
                else:
                    self.logger.fail_msg(f"  ✗ {check_name}: {check_val}")
        
        # ── Latency Statistics Table ──
        latency = parsed.get('latency', {})
        if latency:
            self.logger.info('')
            self.logger.subheader('Latency Statistics')
            latency_table = {}
            for i, (metric_name, value_ns) in enumerate(latency.items(), 1):
                latency_table[f'Metric_{i}'] = {
                    'Metric': metric_name,
                    'Latency (ns)': value_ns,
                    'Latency (ms)': f"{float(value_ns) / 1e6:.2f}" if value_ns != 'N/A' else 'N/A',
                }
            self.logger.printTableFromDict(latency_table)
        
        # ── Test Parameters Table ──
        params = parsed.get('parameters', {})
        if params:
            self.logger.info('')
            self.logger.subheader('Test Parameters')
            params_table = {}
            for i, (param_name, param_val) in enumerate(params.items(), 1):
                params_table[f'Param_{i}'] = {
                    'Parameter': param_name,
                    'Value': param_val,
                }
            self.logger.printTableFromDict(params_table)
        
        # ── Run Info ──
        run_info = parsed.get('run_info', {})
        if run_info:
            self.logger.info('')
            if run_info.get('duration'):
                self.logger.info(f"Test Duration: {run_info['duration']} sec")
            if run_info.get('throughput_line'):
                self.logger.info(f"Progress: {run_info['throughput_line']}")
        
        # ── Warnings / Errors ──
        if parsed.get('warnings'):
            self.logger.warning(f"Warnings: {parsed['warnings']}")
        if parsed.get('errors'):
            self.logger.error(f"Errors: {parsed['errors']}")
        
        # ── Container Status ──
        self.logger.info('')
        self.logger.info("=" * 70)
        self.logger.info("Container Status:")
        self.logger.pass_msg(f"✓ Container '{self.container_name}' is still running")
        self.logger.info(f"  Container ID: {self.container_id}")
        self.logger.info(f"\nTo access the container:")
        self.logger.info(f"  docker exec -it {self.container_name} /bin/bash")
        self.logger.info("=" * 70)
        
        return True
    
    def _parse_mlperf_output(self, output: str) -> Optional[Dict]:
        """
        Parse MLPerf benchmark stdout output into structured dictionaries.
        
        Extracts:
          - summary: SUT name, scenario, mode, samples/s, tokens/s, result, validation checks
          - latency: min/max/mean/percentile latency values
          - parameters: test parameters used
          - run_info: duration, throughput progress line
          - warnings/errors: any warning/error messages
        
        Args:
            output: Raw benchmark stdout text
            
        Returns:
            Dict with parsed results, or None if output cannot be parsed
        """
        if not output or not output.strip():
            return None
        
        parsed = {
            'summary': {},
            'latency': {},
            'parameters': {},
            'run_info': {},
            'warnings': '',
            'errors': '',
        }
        
        # ── Summary section ──
        summary = parsed['summary']
        
        m = re.search(r'SUT\s+name\s*:\s*(.+)', output)
        if m:
            summary['sut_name'] = m.group(1).strip()
        
        m = re.search(r'Scenario\s*:\s*(.+)', output)
        if m:
            summary['scenario'] = m.group(1).strip()
        
        m = re.search(r'Mode\s*:\s*(.+)', output)
        if m:
            summary['mode'] = m.group(1).strip()
        
        m = re.search(r'Samples\s+per\s+second\s*:\s*([\d.]+)', output)
        if m:
            summary['samples_per_second'] = m.group(1).strip()
        
        m = re.search(r'Tokens\s+per\s+second\s*:\s*([\d.]+)', output)
        if m:
            summary['tokens_per_second'] = m.group(1).strip()
        
        m = re.search(r'Result\s+is\s*:\s*(\w+)', output)
        if m:
            summary['result'] = m.group(1).strip()
        
        # Validation checks
        checks = {}
        for label, pattern in [
            ('Min duration satisfied',  r'Min\s+duration\s+satisfied\s*:\s*(\w+)'),
            ('Min queries satisfied',   r'Min\s+queries\s+satisfied\s*:\s*(\w+)'),
            ('Early stopping satisfied', r'Early\s+stopping\s+satisfied\s*:\s*(\w+)'),
        ]:
            m = re.search(pattern, output)
            if m:
                checks[label] = m.group(1).strip()
        summary['checks'] = checks
        
        if not summary.get('result'):
            return None  # Could not parse essential fields
        
        # ── Latency Statistics section ──
        latency_patterns = [
            ('Min latency',                r'Min\s+latency\s*\(ns\)\s*:\s*(\d+)'),
            ('Max latency',                r'Max\s+latency\s*\(ns\)\s*:\s*(\d+)'),
            ('Mean latency',               r'Mean\s+latency\s*\(ns\)\s*:\s*(\d+)'),
            ('50th percentile',            r'50\.00\s+percentile\s+latency\s*\(ns\)\s*:\s*(\d+)'),
            ('90th percentile',            r'90\.00\s+percentile\s+latency\s*\(ns\)\s*:\s*(\d+)'),
            ('95th percentile',            r'95\.00\s+percentile\s+latency\s*\(ns\)\s*:\s*(\d+)'),
            ('97th percentile',            r'97\.00\s+percentile\s+latency\s*\(ns\)\s*:\s*(\d+)'),
            ('99th percentile',            r'99\.00\s+percentile\s+latency\s*\(ns\)\s*:\s*(\d+)'),
            ('99.9th percentile',          r'99\.90\s+percentile\s+latency\s*\(ns\)\s*:\s*(\d+)'),
        ]
        for label, pattern in latency_patterns:
            m = re.search(pattern, output)
            if m:
                parsed['latency'][label] = m.group(1).strip()
        
        # ── Test Parameters section ──
        param_patterns = [
            ('samples_per_query',           r'samples_per_query\s*:\s*(\S+)'),
            ('target_qps',                  r'target_qps\s*:\s*(\S+)'),
            ('ttft_latency (ns)',           r'ttft_latency\s*\(ns\)\s*:\s*(\S+)'),
            ('tpot_latency (ns)',           r'tpot_latency\s*\(ns\)\s*:\s*(\S+)'),
            ('max_async_queries',           r'max_async_queries\s*:\s*(\S+)'),
            ('min_duration (ms)',           r'min_duration\s*\(ms\)\s*:\s*(\S+)'),
            ('max_duration (ms)',           r'max_duration\s*\(ms\)\s*:\s*(\S+)'),
            ('min_query_count',             r'min_query_count\s*:\s*(\S+)'),
            ('max_query_count',             r'max_query_count\s*:\s*(\S+)'),
            ('performance_sample_count',    r'performance_sample_count\s*:\s*(\S+)'),
        ]
        for label, pattern in param_patterns:
            m = re.search(pattern, output)
            if m:
                parsed['parameters'][label] = m.group(1).strip()
        
        # ── Run Info ──
        m = re.search(r'Test\s+took\s+([\d.]+)\s+sec', output)
        if m:
            parsed['run_info']['duration'] = m.group(1).strip()
        
        # Capture final progress/throughput line (e.g. "100%|██...| 13368/13368 [35:11<00:00, 6.33it/s, 810.4toks/s]")
        progress_matches = re.findall(r'100%\|.*?toks/s\]', output)
        if progress_matches:
            parsed['run_info']['throughput_line'] = progress_matches[-1].strip()
        
        # ── Warnings and Errors ──
        m = re.search(r'No\s+warnings\s+encountered\s+during\s+test\.', output)
        if m:
            parsed['warnings'] = 'None'
        else:
            warn_match = re.search(r'WARNING:\s*(.+)', output)
            if warn_match:
                parsed['warnings'] = warn_match.group(1).strip()
        
        m = re.search(r'No\s+errors\s+encountered\s+during\s+test\.', output)
        if m:
            parsed['errors'] = 'None'
        else:
            err_match = re.search(r'(?:ERROR|Error):\s*(.+)', output)
            if err_match:
                parsed['errors'] = err_match.group(1).strip()
        
        return parsed
    
    def cleanup_container(self):
        """Stop and remove the container"""
        if self.is_container_running:
            self.logger.info(f"Stopping container: {self.container_name}")
            try:
                subprocess.run(
                    ["docker", "stop", self.container_name],
                    capture_output=True,
                    timeout=30
                )
                subprocess.run(
                    ["docker", "rm", self.container_name],
                    capture_output=True,
                    timeout=10
                )
                self.logger.pass_msg("✓ Container stopped and removed")
            except Exception as e:
                self.logger.warning(f"Could not cleanup container: {e}")
    
    def prepareGpuCommands(self):
        """
        Prepare GPU commands for the test.
        Steps 0-5: Setup and initialization (run before "RUNNING TEST...")
        Step 6: Benchmark execution (runs during GpuStressProcess)
        Step 7: Results presentation (runs in parseResults)
        """
        # Expand {user} placeholders in parsed arguments first
        self.expand_user_placeholders_in_args()
        
        self.gpuCommands = []
        self.execution_dir = '.'
        self.setup_successful = False
        
        # Run setup steps (0-5) before "RUNNING TEST..."
        try:
            # Step 0: HuggingFace model download (if --download_model was specified)
            # This is a standalone pre-requisite operation — skip test execution entirely
            if getattr(self.parsed_args, 'download_model', None):
                self.skip_test_execution = True
                if not self.download_hf_model():
                    self.logger.fail_msg("HuggingFace model download failed.")
                    self.overall_test_result = 'FAIL'
                else:
                    self.logger.pass_msg("✓ Model download completed successfully")
                    self.overall_test_result = 'PASS'
                return
            
            # Build test configuration from command-line arguments
            config = self._build_test_config_from_args()
            
            # Display configuration summary and wait for user confirmation
            self._display_test_configuration()
            
            # Step 1: Check Docker image
            if not self.check_docker_image():
                self.logger.fail_msg("Docker image validation failed. Please pull the image first.")
                return
            
            # Step 2: Check dataset and model files
            if not self.check_dataset_and_model_files():
                self.logger.fail_msg("Dataset/Model validation failed. Please download required files.")
                return
            
            # Step 3: Launch Docker container
            if not self.launch_docker_container():
                self.logger.fail_msg("Docker container launch failed.")
                return
            
            # Step 4: Check and run calibration if needed
            if not self.check_and_run_calibration():
                self.logger.fail_msg("Model calibration failed.")
                self.cleanup_container()
                return
            
            # Step 5: Apply container patches
            self._apply_container_patches()
            
            # Setup complete - prepare benchmark command for Step 6
            # This will run during GpuStressProcess (after "RUNNING TEST...")
            benchmark_script = self.test_config.get('benchmark_script')
            if benchmark_script:
                # Create a safe command list that executes inside the container (prevents command injection)
                benchmark_cmd = ["docker", "exec", self.container_name, "/bin/bash", "-c", benchmark_script]
                self.gpuCommands.append(benchmark_cmd)
                self.setup_successful = True
                self.logger.pass_msg("✓ Setup complete - benchmark ready to run")
            else:
                self.logger.fail_msg("No benchmark script configured")
                self.cleanup_container()
                return
            
        except KeyboardInterrupt:
            self.logger.warning("\nTest interrupted by user")
            self.cleanup_container()
            self.overall_test_result = 'FAIL'
        except Exception as e:
            self.logger.error(f"Test execution error: {e}")
            self.cleanup_container()
            self.overall_test_result = 'FAIL'
    
    def parseResults(self):
        """
        Parse test results
        Step 7: Present results and determine PASS/FAIL
        
        Checks:
          1. Setup completed successfully
          2. GPU process return code is 0
          3. MLPerf output contains "Result is : VALID"
        """
        self.logger.subheader('Results Parsing...')
        
        # Check if setup was successful
        if not hasattr(self, 'setup_successful') or not self.setup_successful:
            self.overall_test_result = 'FAIL'
            self.logger.fail_msg('OVERALL TEST RESULT : FAIL (Setup incomplete)')
            return
        
        # Check GPU return codes from benchmark execution
        if not (hasattr(self, 'gpu_return_codes') and len(self.gpu_return_codes) > 0):
            self.overall_test_result = 'FAIL'
            self.logger.fail_msg('OVERALL TEST RESULT : FAIL (No return codes from benchmark)')
            return
        
        if not all(code == 0 for code in self.gpu_return_codes):
            self.overall_test_result = 'FAIL'
            self.logger.error(f"Benchmark execution failed with return codes: {self.gpu_return_codes}")
            self.logger.fail_msg('OVERALL TEST RESULT : FAIL')
            return
        
        # Benchmark process succeeded — present structured results
        self.logger.subheader("STEP 7: Test Results Summary")
        self.present_results()
        
        # Determine PASS/FAIL based on parsed MLPerf "Result is : VALID/INVALID"
        benchmark_output = ''
        if hasattr(self, 'gpu_test_results') and self.gpu_test_results:
            benchmark_output = self.gpu_test_results[0] if self.gpu_test_results[0] else ''
        
        parsed = self._parse_mlperf_output(benchmark_output)
        
        if parsed and parsed.get('summary', {}).get('result', '').upper() == 'VALID':
            self.overall_test_result = 'PASS'
            self.logger.pass_msg('OVERALL TEST RESULT : PASS (MLPerf Result: VALID)')
        elif parsed and parsed.get('summary', {}).get('result'):
            # Parsed but result is not VALID (e.g. INVALID)
            mlperf_result = parsed['summary']['result']
            self.overall_test_result = 'FAIL'
            self.logger.fail_msg(f'OVERALL TEST RESULT : FAIL (MLPerf Result: {mlperf_result})')
        else:
            # Could not parse MLPerf result validity from output; treat as overall FAIL
            self.overall_test_result = 'FAIL'
            self.logger.warning('Could not parse MLPerf result validity from output — no VALID/INVALID result found')
            self.logger.fail_msg('OVERALL TEST RESULT : FAIL (MLPerf result validity not determined from output)')
        
        # Show container access info
        if self.overall_test_result == 'PASS':
            print("\n" + "=" * 70)
            if getattr(self.parsed_args, 'yes', False):
                self.cleanup_container()
                self.logger.info("Test session ended. Container cleaned up. (auto-confirmed with -y)")
            else:
                choice = input("Run another test? (y/n) [n]: ").strip().lower()
                if choice == 'y':
                    self.cleanup_container()
                    self.prepareGpuCommands()
                else:
                    self.cleanup_container()
                    self.logger.info("Test session ended. Container cleaned up.")