# Copyright (C) 2024-2026 Intel Corporation
# Verification Test Suite Test Orchestrator
import argparse
import copy
import importlib
import sys
import os
import json
import subprocess # nosec
import time
from common.inputParser import InputParser
from common.deviceManager import DeviceManager
from common.testManager import TestManager
from common import common_defs
from common.utils import Utils
from common.testSummaryManager import ResultsSummarizer

# --- Main Orchestrator ---
class TestSuiteOrchestrator:
    """Main orchestrator for the test suite."""

    # Mapping of JSON parameter names (as documented in README.md) to internal attribute names.
    # Defined at class level to avoid repeated allocations on every _translate_json_parameter call.
    _JSON_TO_INTERNAL_MAPPING = {
        # Global parameters
        # 'pcie_downgrade': 'pcie_downgrade',  # README: pcie_downgrade → internal: pcie_downgrade (no translation needed)
        'd': 'debug_collection',     # README/CLI: -d → internal: debug_collection
        'l': 'level',               # README/CLI: -l → internal: level (diagnostic level for xpum_diag)
        'rep': 'repetitions',       # README/CLI: -rep → internal: repetitions
        'y': 'yes',                 # README/CLI: -y → internal: yes (auto-confirm, MLPerf test)

        # Test 4 (LMT) parameters
        'n': 'numRepeats',           # README: -n → internal: numRepeats (camelCase conversion)
        'rn': 'rxNum',               # README: -rn → internal: rxNum (abbreviation + camelCase)

        # Test 5 (Reset) parameters
        'custom-script': 'custom_script',  # README: -custom-script → internal: custom_script (hyphen→underscore)

        # Test 9 (EDP) parameters
        'at': 'active_time',         # README: -at → internal: active_time (abbreviation expansion)
        'it': 'idle_time',           # README: -it → internal: idle_time (abbreviation expansion)

        # Test 11 (vLLM) parameters - hyphen to underscore conversions
        'max-concurrency': 'max_concurrency',
        'input-len': 'input_len',
        'output-len': 'output_len',
        'enforce-eager': 'enforce_eager',
        'trust-remote-code': 'trust_remote_code',
        'gpu-memory-util': 'gpu_memory_util',
        'enable-expert-parallel': 'enable_expert_parallel',
        'enable-prefix-caching': 'enable_prefix_caching',
        'disable-log-requests': 'disable_log_requests',
        'dataset-type': 'dataset_type',
        'max-model-len': 'max_model_len',
        'max-num-batched-tokens': 'max_num_batched_tokens',
        'block-size': 'block_size',
        'data-parallel-size': 'data_parallel_size',
        'docker-user': 'docker_user',
        'docker-token': 'docker_token',
        'docker-image': 'docker_image',
        'http-proxy': 'http_proxy',
        'https-proxy': 'https_proxy',
        'no-proxy': 'no_proxy',
        'hf-token': 'hf_token',
        'disable-sliding-window': 'disable_sliding_window',
    }

    def __init__(self,logger):
        self.logger = logger
        # Display enhanced header with branding and information
        
        self.logger.header_vts(f'Intel® Data Center GPU Verification Test Suite v{common_defs.version}')
        self.utils = Utils(self.logger)
        
        # Perform system run level check early - before heavy initialization
        try:
            from common.utils import BashScriptManager
            import os
            
            self.logger.subheader('Checking system run level...')
            
            # Create bash manager instance and register deviceDetect script
            bash_manager = BashScriptManager(self.logger)
            common_folder = os.path.join(os.path.dirname(__file__))
            device_detect_script = os.path.join(common_folder, 'deviceDetect.sh')
            bash_manager.register_script('deviceDetect', device_detect_script)
            
            # Use existing check_multi_user_target method
            run_level_check_status = self.utils.check_multi_user_target(bash_manager)
            
            if run_level_check_status != "0":
                # The interactive check already handled user communication
                # Only exit if user declined or switching failed
                raise SystemExit("VTS cannot continue without multi-user target mode.")
                
        except SystemExit:
            raise  # Re-raise SystemExit
        except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError, ImportError, AttributeError) as e:
            self.logger.error(f"Failed to check system run level: {e}")
            self.logger.error('VTS requires multi-user target mode to run properly.')
            raise SystemExit("System run level check failed. VTS cannot continue.")
         
        # Initialize Device Manager
        try:
            self.logger.subheader("Discovering GPU and CPU devices...")
            self.device_manager = DeviceManager(self.logger)
            self.device_manager.discover_devices()
        except (ImportError, AttributeError, subprocess.CalledProcessError, subprocess.SubprocessError, OSError, IOError) as e:
            raise Exception(f"Error initializing DeviceManager: {e}")
        
        # Check and install required packages/libraries
        try:
            module_name = f"gpu.{self.device_manager.gpu_family}.platform_defs"
            platform_module = importlib.import_module(module_name)
            
            # Create BMGPlatformDefs instance to access package requirements
            platform_defs_instance = platform_module.BMGPlatformDefs(self.logger, self.device_manager)
            self.python_packages = platform_defs_instance.python_packages
            self.linux_libraries = platform_defs_instance.linux_libraries
            self.utils.checkRequiredToolsInstalled(self.python_packages, self.linux_libraries)
        except (ImportError, ModuleNotFoundError, AttributeError, Exception) as e:
            # Translate requirement check failures into a controlled exit so start_vts.py can handle it
            self.logger.error(f"Required tools check failed: {e}")
            raise SystemExit("Required tools check failed. VTS cannot continue.")
        
        self.results_summarizer = ResultsSummarizer(self.logger)

        # Parsing input arguments
        try:
            self.logger.subheader('Parsing input arguments...')
            self.input_parser = InputParser(self.device_manager, self.logger)
            self.parsed_args = self.input_parser.parse()
            # Track which args the user explicitly provided on the CLI
            self.parsed_args._explicitly_set_args = self._detect_cli_explicit_args()
            # Snapshot defaults so config-driven runs can reset between entries
            self._default_parsed_args = copy.deepcopy(self.parsed_args)
        except SystemExit:
            # Allow argparse/SystemExit to propagate so callers can handle exit codes properly
            raise
        except (ImportError, AttributeError, argparse.ArgumentError, argparse.ArgumentTypeError) as e:
            raise Exception(f"Error parsing input arguments: {e}")

        # Check if help was requested
        if hasattr(self.parsed_args, 'help') and self.parsed_args.help:
            self._display_help()
            sys.exit(0)

        # Initialize Test Manager
        try:
            self.logger.subheader("Initializing Test Manager...")
            self.test_manager = TestManager(self.logger, self.device_manager, self.input_parser)
        except (ImportError, AttributeError, IOError, OSError, TypeError, ValueError) as e:
            raise Exception(f"Error initializing TestManager: {e}")

    def collectLogs(self):
        """Create summary html and collects logs by zipping the logs folder."""
        self.results_summarizer.summarize()
        self.utils.zip_logs_folder()
    
    def _cleanup_monitoring_processes(self):
        """
        Clean up any lingering monitoring processes that may be holding stdin/stdout.
        This is the key fix for the input capture issue after test execution.
        """
        try:
            # Kill any xpu-smi monitoring processes
            result = subprocess.run(['pkill', '-f', 'xpu-smi'], 
                         stdout=subprocess.DEVNULL, 
                         stderr=subprocess.DEVNULL, 
                         timeout=5)
            if result.returncode not in (0, 1):  # 1 = no matching processes
                self.logger.debug(f"pkill xpu-smi returned code {result.returncode}")
            
            # Kill any DGDiagTool monitoring processes  
            result = subprocess.run(['pkill', '-f', 'DGDiagTool'], 
                         stdout=subprocess.DEVNULL, 
                         stderr=subprocess.DEVNULL, 
                         timeout=5)
            if result.returncode not in (0, 1):
                self.logger.debug(f"pkill DGDiagTool returned code {result.returncode}")
            
            # Reset terminal to clean state
            subprocess.run(['stty', 'sane'], 
                         stdout=subprocess.DEVNULL, 
                         stderr=subprocess.DEVNULL, 
                         timeout=2)
            
            # Small delay to let processes fully terminate
            time.sleep(0.3)
            
        except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
            self.logger.warning(f"Failed to cleanup monitoring processes: {e}")
    
    def _handle_stop_on_error(self, test_number, test_name):
        """
        Handle stop_on_error functionality: collect debug logs and wait for user.
        
        Args:
            test_number: The test number that failed
            test_name: The test name that failed
        """
        # Wait for user input before exiting
        self.logger.info('')
        self.logger.info("Execution stopped due to test failure.")
        self.logger.info('')
    
    def _get_clean_input(self, prompt):
        """
        Get user input with proper cleanup of monitoring interference.
        """
        # Clean up any monitoring processes first
        self._cleanup_monitoring_processes()
        
        # More aggressive terminal reset for proper input handling
        try:
            # First, completely reset the terminal
            subprocess.run(['reset'], 
                         stdout=subprocess.DEVNULL, 
                         stderr=subprocess.DEVNULL, 
                         timeout=3)
            
            # Then set proper terminal modes
            subprocess.run(['stty', 'sane'], 
                         stdout=subprocess.DEVNULL, 
                         stderr=subprocess.DEVNULL, 
                         timeout=2)
            
            # Explicitly enable canonical mode and echo
            subprocess.run(['stty', 'icanon', 'echo', 'erase', '^H'], 
                         stdout=subprocess.DEVNULL, 
                         stderr=subprocess.DEVNULL, 
                         timeout=2)
                         
        except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
            # Fallback: try basic terminal reset
            try:
                subprocess.run(['stty', 'sane'], 
                             stdout=subprocess.DEVNULL, 
                             stderr=subprocess.DEVNULL, 
                             timeout=2)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                # Terminal reset failed - expected on some systems
                pass
            except (OSError, FileNotFoundError) as e:
                # reset command not available - continue anyway
                pass
            except subprocess.SubprocessError as e:
                # Log unexpected errors but continue cleanup
                print(f"Warning: Unexpected error during terminal reset: {e}", file=sys.stderr)
        
        # Flush all streams
        sys.stdout.flush()
        sys.stderr.flush()
        if hasattr(sys.stdin, 'flush'):
            sys.stdin.flush()
        
        # Small delay to let terminal settle
        time.sleep(0.1)
        
        # Get input
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            raise
    
    def _prompt_test_specific_parameters(self, test_number):
        """
        Prompt user for additional parameters based on the specific test selected.
        Returns True if parameters were set successfully, False if user cancelled.
        """
        try:
            result = self.input_parser.prompt_test_parameters(
                test_number, self.parsed_args, self._get_clean_input,
                self.logger, self.input_parser.test_parameter_definitions
            )

            # Track all prompted parameter names as explicitly set
            if result and test_number in self.input_parser.test_parameter_definitions:
                explicitly_set = getattr(self.parsed_args, '_explicitly_set_args', set())
                test_def = self.input_parser.test_parameter_definitions[test_number]
                for param in test_def.get('parameters', []):
                    explicitly_set.add(param['name'])
                self.parsed_args._explicitly_set_args = explicitly_set

            return result

        except (EOFError, KeyboardInterrupt):
            self.logger.info("Parameter input cancelled by user.")
            return False
        except (ValueError, TypeError, AttributeError, RuntimeError) as e:
            self.logger.error(f"Error prompting for test parameters: {e}")
            return True  # Continue with defaults


    def _display_help(self):
        """
        Display usage information from README.md file.
        """
        try:
            # Get the project root directory (where README.md is located)
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            readme_path = os.path.join(project_root, 'README.md')
            
            if os.path.exists(readme_path):
                with open(readme_path, 'r', encoding='utf-8') as f:
                    readme_content = f.read()
                
                # Display the content
                print(readme_content)
            else:
                self.logger.warning(f"README.md not found at {readme_path}")
                self._display_basic_help()
                
        except (IOError, OSError, FileNotFoundError, PermissionError, UnicodeDecodeError) as e:
            self.logger.error(f"Error reading README.md: {e}")
            self._display_basic_help()
    
    def _display_basic_help(self):
        """
        Display basic usage information as fallback.
        """
        help_text = """
        Intel® Verification Test Suite

        Usage:
            python3 start_vts.py -h or --help     : Display this help
            python3 start_vts.py                  : Launch interactive menu
            python3 start_vts.py -tn TESTNUMBER   : Run specific test
            python3 start_vts.py -tn a            : Run all tests
            python3 start_vts.py -tc CONFIG_FILE  : Run tests from config file


        """
        print(help_text)
    
    def _detect_cli_explicit_args(self):
        """Detect which argument dest names were explicitly provided on the command line."""
        explicit = set()
        parser = getattr(self.input_parser, "parser", None)
        args_list = getattr(self.input_parser, "args", None)

        # If we don't have a parser or args list, we cannot detect explicit args
        if parser is None or args_list is None:
            return explicit

        option_actions = getattr(parser, "_option_string_actions", None)
        if not isinstance(option_actions, dict):
            return explicit

        for token in args_list:
            if isinstance(token, str) and token.startswith("-"):
                action = option_actions.get(token)
                if action is not None and getattr(action, "dest", None):
                    explicit.add(action.dest)
        return explicit

    def _translate_json_parameter(self, json_key):
        """
        Translate JSON configuration parameter names to internal parsed_args attribute names.
        
        This provides a user-friendly interface where JSON configs can use the same parameter
        names as documented in README.md, while internally mapping to the correct attribute names.
        
        Args:
            json_key (str): Parameter name from JSON configuration
            
        Returns:
            str: Internal attribute name for parsed_args
        """
        # Return mapped name if exists, otherwise use original key
        return self._JSON_TO_INTERNAL_MAPPING.get(json_key, json_key)
    
    def _run_all_tests(self):
        """
        Execute tests defined in the run_all.json configuration file.
        """
        # Construct path to run_all.json in the platform-specific tests directory
        platform_tests_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'gpu',
            self.device_manager.gpu_family,
            'tests'
        )
        run_all_config_path = os.path.join(platform_tests_dir, 'run_all.json')
        
        # Check if the configuration file exists
        if not os.path.exists(run_all_config_path):
            self.logger.error(f"Run-all configuration file not found: {run_all_config_path}")
            self.logger.error("Falling back to running all numeric tests.")
            # Fallback to original behavior
            numeric_tests = [key for key in self.test_manager.testsDict.keys() if isinstance(key, int)]
            numeric_tests.sort()
            test_sequence = [{"tn": tn} for tn in numeric_tests]
        else:
            # Load test sequence from JSON file
            try:
                with open(run_all_config_path, 'r') as f:
                    test_sequence = json.load(f)
                self.logger.info(f"Loaded run-all configuration from: {run_all_config_path}")
            except (IOError, OSError, FileNotFoundError, PermissionError, json.JSONDecodeError) as e:
                self.logger.error(f"Error loading run-all configuration: {e}")
                return
        
        if not test_sequence:
            self.logger.error("No tests found in run-all configuration.")
            return
        
        self.logger.header_m(f"Running All Tests ({len(test_sequence)} test configurations)")
        self.logger.info('')
        
        passed_tests = []
        failed_tests = []
        
        for i, test_config in enumerate(test_sequence, 1):
            test_number = test_config.get("tn")
            comment = test_config.get("comment", "")
            
            # Handle special commands like 'c' for collect logs
            if isinstance(test_number, str):
                if test_number.lower() == 'c':
                    self.logger.info('')
                    self.logger.header_g(f"[{i}/{len(test_sequence)}] Collecting Logs")
                    self.logger.info('')
                    try:
                        self.collectLogs()
                        self.logger.info("✓ Log collection: COMPLETED")
                    except (IOError, OSError, subprocess.CalledProcessError, subprocess.SubprocessError) as e:
                        self.logger.error(f"✗ Log collection: ERROR - {e}")
                    continue
                elif test_number.lower() == 'd':
                    self.logger.info('')
                    self.logger.header_g(f"[{i}/{len(test_sequence)}] Running Debug Script")
                    self.logger.info('')
                    try:
                        self.test_manager.run_debug_script()
                        self.logger.info("✓ Debug script: COMPLETED")
                    except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError, ImportError, AttributeError) as e:
                        self.logger.error(f"✗ Debug script: ERROR - {e}")
                    continue
                else:
                    self.logger.warning(f"Unknown command in run-all config: {test_number}")
                    continue
            
            # Get test name for display
            test_info = self.test_manager.testsDict.get(test_number, {})
            test_name = test_info.get('name', f'Test {test_number}')
            
            # Display test header with comment if available
            self.logger.info('')
            if comment:
                self.logger.header_g(f"[{i}/{len(test_sequence)}] {test_name}")
                self.logger.info(f"Configuration: {comment}")
            else:
                self.logger.header_g(f"[{i}/{len(test_sequence)}] Starting: {test_name}")
            self.logger.info('')
            
            try:
                # Reset parsed_args to defaults before applying this config entry
                # to prevent parameters from previous entries leaking into this one
                self.parsed_args = copy.deepcopy(self._default_parsed_args)

                # Track explicitly-set args: start with CLI args, add JSON config keys
                explicitly_set = set(getattr(self.parsed_args, '_explicitly_set_args', set()))

                # Set test parameters from config with translation layer
                for key, value in test_config.items():
                    if key not in ['comment']:  # Skip metadata fields
                        # Translate JSON parameter names to internal attribute names
                        internal_key = self._translate_json_parameter(key)
                        # Normalize rxNum to a list so LMT and other tests can safely iterate
                        if internal_key == 'rxNum' and isinstance(value, int):
                            value = [value]
                        setattr(self.parsed_args, internal_key, value)
                        explicitly_set.add(internal_key)

                self.parsed_args._explicitly_set_args = explicitly_set
                
                # Run the test
                result = self.test_manager.run_test(test_number, self.parsed_args)
                
                # Conditionally run debug script based on -d parameter
                if getattr(self.parsed_args, 'debug_collection', False):
                    self.test_manager.run_debug_script_silent()
                
                # Track results
                if result == "PASS" or result == 0:
                    passed_tests.append(f"Test {test_number}")
                    self.logger.info(f"✓ Test {test_number} ({test_name}): PASSED")
                else:
                    failed_tests.append(f"Test {test_number}")
                    self.logger.info(f"✗ Test {test_number} ({test_name}): FAILED")
                    
                    # Check for stop_on_error functionality
                    if getattr(self.parsed_args, 'stop_on_error', False):
                        self._handle_stop_on_error(test_number, test_name)
                        return  # Exit run_all execution
                
            except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, ImportError, AttributeError, TypeError, ValueError, IOError, OSError, RuntimeError, Exception) as e:
                failed_tests.append(f"Test {test_number}")
                self.logger.error(f"✗ Test {test_number} ({test_name}): ERROR - {e}")
                
                # Check for stop_on_error functionality
                if getattr(self.parsed_args, 'stop_on_error', False):
                    self._handle_stop_on_error(test_number, test_name)
                    return  # Exit run_all execution
            
            # Clean up monitoring processes after each test
            self._cleanup_monitoring_processes()
        
        # Final summary
        self.logger.info('')
        self.logger.header_m("Run All Tests - Final Summary")
        self.logger.info(f"Total test configurations: {len(test_sequence)}")
        self.logger.info(f"Passed: {len(passed_tests)}")
        self.logger.info(f"Failed: {len(failed_tests)}")
        
        if passed_tests:
            self.logger.info(f"Passed tests: {passed_tests}")
        if failed_tests:
            self.logger.info(f"Failed tests: {failed_tests}")
        
        if len(failed_tests) == 0:
            self.logger.info("🎉 ALL TESTS PASSED!")
        else:
            self.logger.info(f"⚠️  {len(failed_tests)} test(s) failed. Check logs for details.")
        
        self.logger.info('')

    def menu(self):
        while True:
            self.test_manager.list_tests()
            
            try:
                choice = self._get_clean_input("Select test: \n> ")
            except (EOFError, KeyboardInterrupt):
                self.logger.info("Input interrupted, exiting menu.")
                break
                
            if choice.lower() == 'q':
                break
            elif choice.lower() == 'c':
                self.collectLogs()
                continue
            elif choice.lower() == 'd':
                self.test_manager.run_debug_script()
                continue
            elif choice.lower() == 'a':
                self._run_all_tests()
                continue
            try:
                test_number = int(choice)
                valid_test_numbers = [k for k in self.test_manager.testsDict.keys() if isinstance(k, int)]
                if test_number not in valid_test_numbers:
                    self.logger.error(f"Test number {test_number} is not valid. Valid tests: {sorted(valid_test_numbers)}")
                    continue
                self.parsed_args.tn = test_number
                
                # Prompt for test-specific parameters if needed
                if not self._prompt_test_specific_parameters(test_number):
                    continue  # User cancelled, return to menu
                
                print('')
                self.test_manager.run_test(test_number, self.parsed_args)
                # Conditionally run debug script based on -d parameter (default False for interactive)
                if getattr(self.parsed_args, 'debug_collection', False):
                    self.test_manager.run_debug_script_silent()
                # Critical: Clean up monitoring processes after test execution
                self._cleanup_monitoring_processes()
            except ValueError:
                self.logger.error("Invalid input. Please enter a number, 'c', 'd', or 'q'.")

    def run_by_option(self, test_option):
        """
        Runs a test or collects logs based on the provided test_option.
        """
        if isinstance(test_option, int):
            self.parsed_args.tn = test_option
            print('')
            result = self.test_manager.run_test(test_option, self.parsed_args)
            # Conditionally run debug script based on -d parameter
            if getattr(self.parsed_args, 'debug_collection', False):
                self.test_manager.run_debug_script_silent()
            return result
        elif isinstance(test_option, str):
            if test_option.lower() == 'c':
                self.collectLogs()
            elif test_option.lower() == 'd':
                self.test_manager.run_debug_script()
            elif test_option.lower() == 'a':
                self._run_all_tests()
            elif test_option.lower() in ['h', 'help', '-h', '--help']:
                self._display_help()
            else:
                self.logger.error("Invalid input. Please enter a number, 'c', 'd', 'a', 'h', or 'q'.")
        else:
            self.logger.error("Invalid input. Please enter a number, 'c', 'd', 'h', or 'q'.")

    def run_by_config(self, config_dict):
        """
        Runs a test using parameters from a config dictionary.
        Args:
            config_dict (dict): Dictionary with test parameters.
        """
        test_number = config_dict.get("tn")
        if test_number is None:
            self.logger.error("Missing 'tn' in test config.")
            return

        # Filter out config-only fields that shouldn't be passed as test parameters
        config_only_fields = {'comment'}
        
        # Reset parsed_args to defaults before applying this config entry
        # to prevent parameters from previous entries leaking into this one
        self.parsed_args = copy.deepcopy(self._default_parsed_args)

        # Track explicitly-set args: start with CLI args, add JSON config keys
        explicitly_set = set(getattr(self.parsed_args, '_explicitly_set_args', set()))

        # Set up arguments for the test - translate JSON parameter names (CLI aliases and
        # hyphenated keys) to internal dest names, matching the same layer used in _run_all_tests().
        for key, value in config_dict.items():
            if key not in config_only_fields:
                internal_key = self._translate_json_parameter(key)
                # Normalize rxNum to a list so LMT and other tests can safely iterate
                if internal_key == 'rxNum' and isinstance(value, int):
                    value = [value]
                setattr(self.parsed_args, internal_key, value)
                explicitly_set.add(internal_key)

        self.parsed_args._explicitly_set_args = explicitly_set
        
        # Reset the instance-level flag before each test run
        self._stop_on_error_triggered = False

        try:
            result = self.run_by_option(test_number)
            
            # Check for stop_on_error functionality if test failed (UNKNOWN is not a failure)
            if (getattr(self.parsed_args, 'stop_on_error', False) and
                    result not in {None, "PASS", "UNKNOWN", 0}):
                test_info = self.test_manager.testsDict.get(test_number, {})
                test_name = test_info.get('name', f'Test {test_number}')
                self._handle_stop_on_error(test_number, test_name)
                # Set explicit flag that stop_on_error was triggered
                self._stop_on_error_triggered = True
                return result
                
            return result
        except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, ImportError, AttributeError, TypeError, ValueError, IOError, OSError) as e:
            self.logger.error(f"Test {test_number} failed: {e}")
            
            # Check for stop_on_error functionality on exception
            if getattr(self.parsed_args, 'stop_on_error', False):
                test_info = self.test_manager.testsDict.get(test_number, {})
                test_name = test_info.get('name', f'Test {test_number}')
                self._handle_stop_on_error(test_number, test_name)
                # Set explicit flag that stop_on_error was triggered
                self._stop_on_error_triggered = True
            
            raise  # Re-raise exception so callers receive a non-zero exit code
