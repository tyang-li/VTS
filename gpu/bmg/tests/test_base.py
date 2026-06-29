# Copyright (C) 2024-2026 Intel Corporation
import os
import sys
import re
import argparse
import importlib
import signal
import subprocess # nosec
import queue  # For queue.Empty exception handling
from time import sleep
from abc import ABC, abstractmethod
from multiprocessing import Process, Queue, Semaphore, Event
from datetime import datetime
from common.utils import Utils
from common.loggerManager import OutputLogger
from .. import platform_defs
import threading


class testBase(ABC):
    def __init__(self, testNumber, logger, device_manager, input_parser):
        self.testNumber = testNumber
        self.logger = logger
        self.device_manager = device_manager
        self.input_parser = input_parser
        
        # Set global environment variable for all tests
        # This ensures consistent device ordering across all GPU operations
        os.environ['ZE_ENABLE_PCI_ID_DEVICE_ORDER'] = '1'
        
        # Create platform definitions instance for configuration access
        self.platform_defs_instance = platform_defs.BMGPlatformDefs(self.logger, self.device_manager)
        
        # Load platform-specific modules
        platform_utils_module = importlib.import_module(f"gpu.{self.device_manager.gpu_family}.platform_utils")
        self.platform_utils = platform_utils_module.platformUtils(self.logger, self.device_manager)
        # Store mon_params_module for later use in runTest()
        self.mon_params_module = importlib.import_module(f"gpu.{self.device_manager.gpu_family}.monitor.monParams")
        self.test_output = []
        self.utils = Utils(self.logger)
        self.sampling_rate = 1000
        self.gpu_return_codes = []  # Store return codes from GPU commands
        self.overall_test_result = "FAIL"
        self.monCsvFilePath = None  # Store monitoring CSV file path
        self.early_exit = False  # Flag to track if test exited early before process initialization
        self.gpu_process_name = 'GpuStressProcess'  # Configurable process name (override in subclass)
        self.skip_test_execution = False  # Flag to skip runTest/parseResults (for pre-requisite operations)

    def printTestName(self):
        self.logger.header_g(f'{self.testName}')

    # Argument dest names containing these keywords will have their values redacted in logs
    SENSITIVE_ARG_KEYWORDS = ('token', 'password', 'secret', 'key')

    @staticmethod
    def _redact_sensitive(dest_name, value):
        """Return '***REDACTED***' if dest_name contains a sensitive keyword segment, otherwise return value."""
        segments = dest_name.lower().split('_')
        if any(kw in segments for kw in testBase.SENSITIVE_ARG_KEYWORDS):
            return '***REDACTED***'
        return value

    def printArguments(self):
        self.logger.subheader('Test Arguments:')
        args_dict = vars(self.parsed_args)
        
        # Filter out the help argument
        filtered_args_dict = {k: v for k, v in args_dict.items() if k != 'help'}
        
        # Create a mapping of argument destination to user-facing parameter name and help text
        dest_to_param_name = {}
        arg_help_dict = {}
        
        for action in self.input_parser.parser._actions:
            if hasattr(action, 'dest') and action.dest != 'help':
                # Get the user-facing parameter name (prefer shorter option when available)
                if action.option_strings:
                    # Sort by length to get the shorter option first (e.g., '-d' before '--debug_collection') 
                    param_name = sorted(action.option_strings, key=len)[0]
                    dest_to_param_name[action.dest] = param_name
                else:
                    # For positional arguments, use dest name
                    dest_to_param_name[action.dest] = action.dest
                
                arg_help_dict[action.dest] = action.help or "No description available"
        
        # Define display order matching README.md documentation
        # Global parameters first (in README.md order), then test-specific parameters
        global_param_order = [
            'tn',               # Test Number
            'tc',               # JSON file test config path  
            'repetitions',      # Number of repetitions (-rep)
            'mt',               # Monitor type
            'cs',               # CPU stress tool
            'pcie_downgrade',   # PCIe downgrade control
            'debug_collection', # Debug collection (-d)  
            'stop_on_error',    # Stop on error
            'live_mon'          # Live monitor
        ]
        
        # Calculate max lengths for formatting
        if filtered_args_dict:  # Check if there are any arguments to display
            display_names = [dest_to_param_name.get(arg, arg) for arg in filtered_args_dict.keys()]
            max_arg_len = max(len(str(name)) for name in display_names)
            max_value_len = max(len(str(v)) for v in filtered_args_dict.values())
            
            # Display global parameters in README.md order first
            displayed_args = set()
            for dest_name in global_param_order:
                if dest_name in filtered_args_dict:
                    value = self._redact_sensitive(dest_name, filtered_args_dict[dest_name])
                    display_name = dest_to_param_name.get(dest_name, dest_name)
                    help_text = arg_help_dict.get(dest_name, "No description available")
                    self.logger.info(f"{display_name.ljust(max_arg_len)} = {str(value).ljust(max_value_len)} | {help_text}")
                    displayed_args.add(dest_name)
            
            # Display any remaining test-specific parameters
            for dest_name, value in filtered_args_dict.items():
                if dest_name not in displayed_args:
                    value = self._redact_sensitive(dest_name, value)
                    display_name = dest_to_param_name.get(dest_name, dest_name)
                    help_text = arg_help_dict.get(dest_name, "No description available")
                    self.logger.info(f"{display_name.ljust(max_arg_len)} = {str(value).ljust(max_value_len)} | {help_text}")
        else:
            self.logger.info("No arguments to display")

    def printStaticInfo(self):
        self.platform_utils.getStaticInfo()

    @abstractmethod
    def prepareGpuCommands(self):
        pass

    def prepareCpuCommands(self):
        self.cpuCommands = []
        if self.parsed_args.cs == 'stress-ng':
            envVars = f''
            # Generate CSV filename for cpuStress.sh output with repetition-specific timestamp
            timestamp_for_csv = getattr(self, 'current_rep_timestamp', self.timestamp)
            csv_filename = f"cpuStress_{self.testName.replace(' ', '_')}_{timestamp_for_csv}.csv"
            csv_filepath = os.path.join(self.logs_dir, csv_filename)
            # Use cpuStress.sh script instead of direct stress-ng
            self.cpuCommands.append(f'{envVars}sudo TARGET_CPU=80 TARGET_MEM=80 OUTPUT_MODE=csv CSV_FILE={csv_filepath} ./cpuStress.sh')
            # Set execution directory to tools folder where cpuStress.sh script is located
            project_root = os.path.dirname(os.path.abspath(__file__))  # This file's directory
            project_root = os.path.abspath(os.path.join(project_root, '..', '..', '..'))  # Go up to project root
            self.cpu_execution_dir = os.path.join(project_root, 'tools')
        elif self.parsed_args.cs == 'ptat':
            self.cpu_execution_dir = self.utils.installPTAT()
            self.ptat_mon_execution_dir = self.cpu_execution_dir
            envVars = f''
            self.cpuCommands.append(f'{envVars}./ptat -ct 3 -cp 80 -mt 3 -mi 4 -i 1000000 -ts -log -logdir {self.logs_dir} -csv -y -wf')

    def runTest(self):
        self.logger.subheader('Running Test...')

        self.paramsMonitor = self.mon_params_module.paramsMonitor(self.logger, self.device_manager, sampling_rate = self.sampling_rate, mon_mode = self.parsed_args.mt)

        semaphore = Semaphore(4)

        stop_event_gpu = Event()
        queue_gpu = Queue()
        queue_gpu_return_codes = Queue()
        self.gpu_test_results = []
        gpuProcess = Process(target=self._runGpuStress, name=self.gpu_process_name, kwargs={'semaphore': semaphore, 'queue': queue_gpu, 'queue_return_codes': queue_gpu_return_codes, 'stop_event': stop_event_gpu})

        # Initialize CPU-related variables to None
        cpuProcess = None
        stop_event_cpu = None
        queue_cpu = None

        if self.parsed_args.cs != 'None':
            stop_event_cpu = Event()
            queue_cpu = Queue()
            self.cpu_test_results = []
            if self.parsed_args.cs == 'ptat':
                self.utils.cleanPtatLogs()
            cpuProcess = Process(target=self._runCpuStress, name='CpuStressProcess', kwargs={'semaphore': semaphore, 'queue': queue_cpu, 'stop_event': stop_event_cpu})

        # Initialize monitoring-related variables to None
        monProcess = None
        stop_event_mon = None
        queue_mon = None

        if self.parsed_args.mt != 'None':
            stop_event_mon = Event()
            queue_mon = Queue()
            self.mon_test_results = []
            monProcess = Process(target=self._runGpuMon, name='MonitorProcess', kwargs={'semaphore': semaphore, 'queue': queue_mon, 'stop_event': stop_event_mon})

        self.logger.info('')
        self.logger.info(f'Test Execution Started: {datetime.now().strftime("%Y-%m-%d_%H%M%S")}')
        self.logger.info('')

        # Execute processes using customizable method
        self._execute_processes(monProcess, cpuProcess, gpuProcess)

        

        try:
            # Join processes using customizable method
            self._join_processes(
                monProcess, cpuProcess, gpuProcess,
                stop_event_mon, stop_event_cpu, stop_event_gpu,
                queue_mon, queue_cpu, queue_gpu, queue_gpu_return_codes
            )
        except (RuntimeError, OSError, AttributeError, TypeError, KeyboardInterrupt, BrokenPipeError, EOFError, ConnectionResetError) as e:
            self.logger.warning(f"Process joining error (non-fatal): {e}")
            # Signal all processes to stop
            stop_event_gpu.set()
            if self.parsed_args.cs != 'None' and stop_event_cpu is not None:
                stop_event_cpu.set()
            if self.parsed_args.mt != 'None' and stop_event_mon is not None:
                stop_event_mon.set()
        except SystemExit as se:
            # Catch SystemExit to prevent child process exits from terminating VTS
            self.logger.warning(f"Process attempted to exit VTS (non-fatal): exit code {se.code}")
            # Signal all processes to stop
            stop_event_gpu.set()
            if self.parsed_args.cs != 'None' and stop_event_cpu is not None:
                stop_event_cpu.set()
            if self.parsed_args.mt != 'None' and stop_event_mon is not None:
                stop_event_mon.set()
        except Exception as unexpected_e:
            # Catch any other unexpected exceptions (including signal-related) to prevent VTS termination
            self.logger.warning(f"Unexpected process error (non-fatal): {unexpected_e}")
            # Signal all processes to stop
            stop_event_gpu.set()
            if self.parsed_args.cs != 'None' and stop_event_cpu is not None:
                stop_event_cpu.set()
            if self.parsed_args.mt != 'None' and stop_event_mon is not None:
                stop_event_mon.set()
            
        finally:
            # Ensure all processes are joined even if an error occurs
            try:
                if gpuProcess.is_alive():
                    gpuProcess.join(timeout=5)
            except Exception as e:
                self.logger.warning(f"GPU process final join error: {e}")
                
            if self.parsed_args.cs != 'None' and cpuProcess is not None:
                try:
                    if cpuProcess.is_alive():
                        cpuProcess.join(timeout=5)
                except Exception as e:
                    self.logger.warning(f"CPU process final join error: {e}")
                    
            if self.parsed_args.mt != 'None' and monProcess is not None:
                try:
                    if monProcess.is_alive():
                        monProcess.join(timeout=5)
                except Exception as e:
                    self.logger.warning(f"Monitor process final join error: {e}")

        self.logger.info('')
        self.logger.info(f'Test Execution Finished: {datetime.now().strftime("%Y-%m-%d_%H%M%S")}')

    def _should_timeout_gpu_command(self, elapsed_time):
        """
        Check if the GPU command should be terminated due to timeout.
        Can be overridden in child classes to implement custom timeout logic.
        
        Args:
            elapsed_time (float): Time elapsed since command started in seconds
            
        Returns:
            bool: True if command should be terminated, False otherwise
        """
        return False

    def _collect_results(self, result_queue, results_list, name, process=None):
        """
        Collect results from a process queue with timeout handling.
        
        Args:
            result_queue: Queue object to collect results from
            results_list: List to append results to
            name: Name of the process for logging
            process: Optional process object to check if still alive
        """
        consecutive_timeouts = 0
        max_consecutive_timeouts = 10  # Allow up to 10 minutes of no activity
        
        while True:
            try:
                task = result_queue.get(timeout=60)
                consecutive_timeouts = 0  # Reset timeout counter on successful get
                if task is None:
                    break
                results_list.append(task)
            except (queue.Empty, queue.Full, BrokenPipeError, OSError, EOFError, ConnectionResetError) as e:
                consecutive_timeouts += 1
                
                # Continue if process is still alive and under timeout limit
                if process and process.is_alive() and consecutive_timeouts <= max_consecutive_timeouts:
                    continue
                elif not (process and process.is_alive()):
                    self.logger.info(f"{name} queue stopped: {e}")
                    break
                # If we exceed max timeouts but process is alive, reset and continue
                consecutive_timeouts = 0
            except (SystemExit, KeyboardInterrupt) as sig_e:
                # Log signal-based termination but don't propagate it
                self.logger.warning(f"{name} queue signal received (non-fatal): {sig_e}")
                break
            except Exception as unexpected_e:
                # Catch any other unexpected exceptions to prevent termination propagation
                self.logger.warning(f"{name} queue unexpected error (non-fatal): {unexpected_e}")
                break

    def _execute_processes(self, monProcess, cpuProcess, gpuProcess):
        """
        Execute test processes in a specific order. Can be overridden in child classes
        to customize the execution sequence and timing.
        
        Args:
            monProcess: GPU monitoring process
            cpuProcess: CPU stress process
            gpuProcess: GPU stress process
        """
        # Default execution order: Monitor -> CPU Stress -> GPU Stress
        if self.parsed_args.mt != 'None' and monProcess is not None:
            monProcess.start()
            self.logger.info(f'Starting {monProcess.name} at {datetime.now().strftime("%Y-%m-%d_%H%M%S")}...')
            sleep(5)

        if self.parsed_args.cs != 'None' and cpuProcess is not None:
            cpuProcess.start()
            self.logger.info(f'Starting {cpuProcess.name} at {datetime.now().strftime("%Y-%m-%d_%H%M%S")}...')
            sleep(30)

        gpuProcess.start()
        self.logger.info(f'Starting {gpuProcess.name} at {datetime.now().strftime("%Y-%m-%d_%H%M%S")}...')
        sleep(5)

    def _join_processes(self, monProcess, cpuProcess, gpuProcess,
                       stop_event_mon, stop_event_cpu, stop_event_gpu,
                       queue_mon, queue_cpu, queue_gpu, queue_gpu_return_codes):
        """
        Join test processes in a specific order. Can be overridden in child classes
        to customize the cleanup sequence and timing.
        
        Args:
            monProcess, cpuProcess, gpuProcess: Process objects
            stop_event_*: Event objects to signal process termination
            queue_*: Queue objects to collect results
        """
        # Default join order: GPU -> CPU -> GPU Monitor
        try:
            self._collect_results(queue_gpu, self.gpu_test_results, "GPU Stress", gpuProcess)
            gpuProcess.join()
        except (SystemExit, KeyboardInterrupt, BrokenPipeError, OSError, RuntimeError) as e:
            self.logger.warning(f"GPU process join failed (non-fatal): {e}")
            # Force terminate if still alive
            if gpuProcess.is_alive():
                try:
                    gpuProcess.terminate()
                    gpuProcess.join(timeout=5)
                    if gpuProcess.is_alive():
                        gpuProcess.kill()
                except Exception as term_e:
                    self.logger.warning(f"GPU process force termination error: {term_e}")
        
        # Collect return codes from the queue with timeout
        try:
            self.gpu_return_codes = queue_gpu_return_codes.get(timeout=10)
        except (queue.Empty, queue.Full, BrokenPipeError, OSError) as e:
            self.gpu_return_codes = []
            
        self.logger.info(f'{gpuProcess.name} finished at {datetime.now().strftime("%Y-%m-%d_%H%M%S")}.')
        sleep(10)

        if self.parsed_args.cs != 'None' and cpuProcess is not None:
            # Signal cpu stress to stop after gpu stress tests finish
            if stop_event_cpu is not None:
                stop_event_cpu.set()
            
            try:
                self._collect_results(queue_cpu, self.cpu_test_results, "CPU Stress", cpuProcess)
                cpuProcess.join()
                self.logger.info(f'{cpuProcess.name} finished at {datetime.now().strftime("%Y-%m-%d_%H%M%S")}.')
            except (RuntimeError, OSError, AttributeError, TypeError, KeyboardInterrupt, SystemExit, BrokenPipeError) as e:
                self.logger.warning(f"CPU stress process join failed (non-fatal): {e}")
                # Force terminate if still alive
                if cpuProcess.is_alive():
                    try:
                        cpuProcess.terminate()
                        cpuProcess.join(timeout=5)
                        if cpuProcess.is_alive():
                            cpuProcess.kill()
                    except Exception as term_e:
                        self.logger.warning(f"CPU stress force termination error: {term_e}")
                self.logger.info(f'{cpuProcess.name} finished with errors at {datetime.now().strftime("%Y-%m-%d_%H%M%S")}.')

        if self.parsed_args.mt != 'None' and monProcess is not None:
            # Signal monitor to stop after stress tests finish
            if stop_event_mon is not None:
                stop_event_mon.set()
            
            try:
                self._collect_results(queue_mon, self.mon_test_results, "GPU Monitor", monProcess)
                monProcess.join()
                self.logger.info(f'{monProcess.name} finished at {datetime.now().strftime("%Y-%m-%d_%H%M%S")}.')
            except (RuntimeError, OSError, AttributeError, TypeError, KeyboardInterrupt, SystemExit, BrokenPipeError) as e:
                self.logger.warning(f"Monitor process join failed (non-fatal): {e}")
                # Force terminate if still alive
                if monProcess.is_alive():
                    try:
                        monProcess.terminate()
                        monProcess.join(timeout=5)
                        if monProcess.is_alive():
                            monProcess.kill()
                    except Exception as term_e:
                        self.logger.warning(f"Monitor process force termination error: {term_e}")
                self.logger.info(f'{monProcess.name} finished with errors at {datetime.now().strftime("%Y-%m-%d_%H%M%S")}.')
            # Extract CSV file path from monitoring results
            if self.mon_test_results and self.mon_test_results[0] and isinstance(self.mon_test_results[0], str):
                self.monCsvFilePath = self.mon_test_results[0]

    def _runGpuStress(self, semaphore, queue, queue_return_codes, stop_event):
        with semaphore:
            cwd = os.getcwd()
            os.chdir(self.execution_dir)
            try:
                for command in self.gpuCommands:
                    if stop_event.is_set():
                        self.logger.info("GPU Stress received stop signal, terminating early.")
                        break

                    self.logger.info("")
                    self.logger.info(f"Command: {command}")
                    
                    # Initialize timing for progress tracking
                    import time
                    import sys
                    start_time = time.time()
                    
                    test_exec_object = self.utils.run_command_non_blocking(command)
                    
                    output_lines = []
                    line_count = 0
                    
                    try:
                        for line in test_exec_object.iter_output(stop_event):
                            line_count += 1
                            current_time = time.time()
                            elapsed = current_time - start_time
                            
                            # Check if command should timeout (can be overridden in child classes)
                            if self._should_timeout_gpu_command(elapsed):
                                self.logger.warning(f"GPU command exceeded timeout ({elapsed:.1f}s), terminating process")
                                test_exec_object.terminate_process()
                                break
                            
                            # Filter and display output appropriately
                            if line.startswith('[Process running') or line.startswith('[Error') or line.startswith('[Failed'):
                                # Skip all debug messages and progress indicators
                                continue
                            elif line.startswith('+') and len(line) > 1:
                                # Skip bash debug traces (from -x flag) - these are too verbose
                                continue
                            elif line.strip():
                                # Actual meaningful script output
                                self.logger.info(f'\t{line}')
                                output_lines.append(line)
                            
                            # Force immediate display
                            sys.stdout.flush()
                            sys.stderr.flush()
                        
                        # Check if loop exited due to stop_event
                        if stop_event.is_set():
                            self.logger.info("GPU Stress received stop signal during execution, terminating.")
                            test_exec_object.process.send_signal(signal.SIGINT)
                        
                        self.logger.info('')
                        test_exec_object.process.wait()
                        
                        # Get and store the return code
                        return_code = test_exec_object.get_return_code()
                        if return_code is None:
                            self.gpu_return_codes.append(-1)
                            self.logger.info('Return code was None, storing -1 as failure.')
                        else:
                            self.gpu_return_codes.append(return_code)
                        
                        test_output = '\n'.join(output_lines)
                    except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError, IOError) as e:
                        self.logger.error(f'{e}')
                        # Store -1 for failed commands
                        self.gpu_return_codes.append(-1)
                        self.logger.info(f'Command failed with exception, storing return code: -1')
                        test_output = str(e)
                    queue.put(test_output)
            finally:
                # Always send return codes, even if there was an error
                queue_return_codes.put(self.gpu_return_codes)
                os.chdir(cwd)
                # Signal completion to parent by putting None in the queue
                queue.put(None)

    def _runCpuStress(self, semaphore, queue, stop_event):
        with semaphore:
            cwd = os.getcwd()
            os.chdir(self.cpu_execution_dir)
            try:
                for command in self.cpuCommands:
                    if stop_event.is_set():
                        self.logger.info("CPU Stress received stop signal, terminating early.")
                        break

                    # Ensure script is executable using helper method
                    script_path = None
                    if self.parsed_args.cs == 'stress-ng':
                        # For cpuStress.sh script
                        script_path = os.path.join(self.cpu_execution_dir, "cpuStress.sh")
                    elif self.parsed_args.cs == 'ptat':
                        # For PTAT binary
                        script_path = os.path.join(self.cpu_execution_dir, "ptat")

                    if script_path is not None:
                        if not self.utils.make_script_executable(script_path):
                            # Fallback to direct chmod if helper method fails
                            try:
                                os.chmod(script_path, 0o750)
                            except (OSError, PermissionError) as e:
                                self.logger.warning(f"Failed to make script executable {script_path}: {e}")
                                # Script may still run if already executable
                            except (RuntimeError, OSError, PermissionError, AttributeError) as e:
                                self.logger.error(f"Unexpected error setting script permissions: {e}")
                                # Continue but log the issue
                    
                    test_exec_object = self.utils.run_command_non_blocking(command)
                    output_lines = []

                    # Thread to monitor stop_event and send SIGINT if set
                    def monitor_stop_event(proc, event):
                        while proc.poll() is None:
                            if event.is_set():
                                # Check if this is a PTAT process (needs special handling)
                                if 'ptat' in command:
                                    
                                    try:
                                        # Kill the main process first
                                        try:
                                            proc.send_signal(signal.SIGKILL)
                                        except Exception as sig_e:
                                            self.logger.warning(f"Failed to send SIGKILL: {sig_e}")
                                        
                                        sleep(0.5)  # Give main process time to die
                                        
                                        # Use targeted cleanup for PTAT processes
                                        cleanup_success = self._cleanup_ptat_processes()
                                        if not cleanup_success:
                                            self.logger.warning("PTAT process cleanup may be incomplete")
                                            
                                    except (subprocess.SubprocessError, ProcessLookupError, OSError) as e:
                                        self.logger.warning(f"Error during PTAT termination: {e}")
                                else:
                                    # stress-ng and cpuStress.sh processes need proper cleanup
                                    cleanup_success = self._cleanup_stress_processes(proc)
                                    if not cleanup_success:
                                        self.logger.warning("Stress process cleanup may be incomplete")
                                    
                                    # Also use the utility cleanup
                                    self.utils.killParentandChildProcesses(proc.pid)
                                break
                            sleep(0.5)  # Check every 0.5 seconds

                    monitor_thread = threading.Thread(target=monitor_stop_event, args=(test_exec_object.process, stop_event))
                    monitor_thread.daemon = True  # Don't prevent process termination
                    monitor_thread.start()

                    try:
                        for line in test_exec_object.iter_output(stop_event):
                            output_lines.append(line)
                            # CPU stress output is collected but not displayed on screen

                        test_exec_object.process.wait()
                        return_code = test_exec_object.get_return_code()
                        
                        # Log CPU stress completion status
                        if return_code not in (0, -15, -9):
                            # Only warn for unexpected return codes; -15 (SIGTERM) and -9 (SIGKILL) are expected during cleanup
                            self.logger.warning(f"CPU stress completed with unexpected return code {return_code}")
                        
                        # Ensure cleanup of any remaining CPU stress processes
                        self._final_process_cleanup(command)
                        
                        monitor_thread.join(timeout=5)  # Don't wait forever for monitor thread
                        test_output = '\n'.join(output_lines)
                    except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, IOError, OSError, RuntimeError) as e:
                        self.logger.warning(f'CPU stress error (non-fatal): {e}')
                        test_output = str(e)
                    
                    # Safely put output in queue with exception handling
                    try:
                        queue.put(test_output)
                    except (BrokenPipeError, OSError, EOFError, ConnectionResetError) as queue_e:
                        self.logger.warning(f"CPU stress queue error (non-fatal): {queue_e}")
                        # Continue execution even if queue operation fails
            except Exception as outer_e:
                # Catch any other exceptions that might terminate the process
                self.logger.warning(f"CPU stress outer exception (non-fatal): {outer_e}")
                try:
                    queue.put(f"CPU stress failed: {str(outer_e)}")
                except Exception:
                    pass  # Ignore queue errors in error path
            finally:
                # Final cleanup of any remaining CPU stress processes
                try:
                    self._emergency_process_cleanup()
                except Exception:
                    pass  # Best-effort cleanup
                
                try:
                    os.chdir(cwd)
                except Exception as chdir_e:
                    self.logger.warning(f"Directory change error: {chdir_e}")
                
                try:
                    queue.put(None)
                except (BrokenPipeError, OSError, EOFError, ConnectionResetError) as final_queue_e:
                    self.logger.warning(f"CPU stress final queue error (non-fatal): {final_queue_e}")
                    # Don't let queue errors terminate the process
    
    def _runGpuMon(self, semaphore, queue, stop_event):
        with semaphore:
            # Use repetition-specific timestamp for GPU monitoring CSV files
            timestamp_for_monitoring = getattr(self, 'current_rep_timestamp', self.timestamp)
            self.monCsvFilePath = self.paramsMonitor.startMonitoring(self.testName, timestamp_for_monitoring)
            if self.monCsvFilePath is not None:
                queue.put(self.monCsvFilePath)
            
            try:
                # Wait until stop_event is set, checking periodically
                while not stop_event.is_set():
                    stop_event.wait(timeout=1)
            finally:
                sucess = self.paramsMonitor.stopMonitoring()
                # Signal completion to parent by putting None in the queue
                queue.put(None)



    @abstractmethod
    def parseResults(self):
        pass

    def modifyTestName(self):
        pass

    def disable_pcie_downgrade_if_needed(self):
        """
        Disable PCIe downgrade if enabled via parameter and system has the capability.
        """
        
        # Check if PCIe downgrade disable is enabled
        disable_downgrade = getattr(self.parsed_args, 'pcie_downgrade', False)
        if not disable_downgrade:
            return
            
        # Check if system has PCIe auto downgrade capability before attempting to disable
        self.logger.subheader('Checking PCIe auto downgrade capability')
        
        # Check each DRM card for auto_link_downgrade_capable
        has_capability = False
        import glob
        import os
        
        try:
            drm_cards = glob.glob('/sys/class/drm/card*/device/auto_link_downgrade_capable')
            if not drm_cards:
                self.logger.info('No PCIe auto downgrade capability files found - skipping PCIe downgrade disable')
                return
                
            for card_path in drm_cards:
                try:
                    with open(card_path, 'r') as f:
                        capability = f.read().strip()
                        card_name = os.path.basename(os.path.dirname(os.path.dirname(card_path)))
                        
                        if capability == '1':
                            self.logger.info(f'{card_name}: PCIe auto downgrade capability enabled (1)')
                            has_capability = True
                        elif capability == '0':
                            self.logger.info(f'{card_name}: PCIe auto downgrade capability disabled (0)')
                        else:
                            self.logger.warning(f'{card_name}: Unexpected capability value: {capability}')
                            
                except (IOError, OSError) as e:
                    self.logger.warning(f'Failed to read {card_path}: {e}')
                    
        except Exception as e:
            self.logger.warning(f'Error checking PCIe auto downgrade capability: {e}')
            # Continue with disable attempt anyway - fallback behavior
            has_capability = True
            
        if not has_capability:
            self.logger.info('No GPUs have PCIe auto downgrade capability enabled - skipping PCIe downgrade disable')
            return
            
        # Try to disable PCIe downgrade for each GPU instance
        self.logger.subheader('Disabling PCIe downgrade')
        self.pcie_downgrade_failed = False
        self.pcie_downgrade_blocked_by_eom = False  # Flag to track EOM blocking PCIe operations
        
        # Get all GPU instances from device manager
        try:
            dginstances = self.device_manager.getGpuInstancesDGDiag()
            if len(dginstances) == 0:
                raise Exception("Couldn't get GPU instances")
            
            # Get EOM status for all instances first
            eom_status_dict = self.platform_utils.getEOMStatus()
            
            for gpu_instance in dginstances:
                # Check EOM status for this instance
                eom_status = eom_status_dict.get(gpu_instance, 0)  # Default to not set if unknown
                
                if eom_status == 1:
                    # EOM is set - PCIe downgrade disable won't work
                    self.logger.info(f'Instance {gpu_instance}: EOM is set - cannot disable PCIe downgrade')
                    self.pcie_downgrade_blocked_by_eom = True
                else:
                    # EOM not set - proceed with PCIe downgrade disable
                    result = self.platform_utils.disablePCIeDowngrade(gpu_instance)
                    if result != 0:
                        self.pcie_downgrade_failed = True
                        self.logger.error(f'Failed to disable PCIe downgrade for instance {gpu_instance}')
                    else:
                        self.logger.info(f'Instance {gpu_instance}: PCIe downgrade disabled')
            
            # Log final status
            if self.pcie_downgrade_blocked_by_eom:
                self.logger.warning('PCIe downgrade disable was blocked by EOM bit on one or more instances')
                
                # Ask for user consent to continue test execution
                if sys.stdin.isatty():
                    # Interactive mode - prompt user for consent
                    self.logger.warning('EOM bit prevents PCIe downgrade disable - continuing test may require reboot after execution')
                    try:
                        user_response = input('        Do you want to continue with test execution? [y/N]: ').strip().lower()
                        if user_response not in ['y', 'yes']:
                            self.logger.info('User declined to continue test execution')
                            raise RuntimeError('Test execution cancelled by user due to EOM bit preventing PCIe downgrade disable')
                        else:
                            self.logger.info('User confirmed to continue test execution')
                    except (KeyboardInterrupt, EOFError):
                        self.logger.info('User cancelled test execution')
                        raise RuntimeError('Test execution cancelled by user due to EOM bit preventing PCIe downgrade disable')
                else:
                    # Non-interactive mode - cancel test execution for safety
                    self.logger.error('EOM bit prevents PCIe downgrade disable and no user interaction available')
                    raise RuntimeError('Test execution cancelled in automated mode due to EOM bit preventing PCIe downgrade disable')
            
        except Exception as e:
            self.logger.error(f'Error during PCIe downgrade disable: {str(e)}')
            self.pcie_downgrade_failed = True
            self.execution_dir = '.'
            self.logger.warning('PCIe downgrade disable failed - test will be marked as FAIL but execution continues')

    def _attempt_pcie_recovery_if_needed(self):
        """
        Attempt to restore PCIe downgrade functionality after test completion if initial disable failed.
        Uses xpu-smi to reset PCIe downgrade and checks if reboot is needed.
        """
        # Check if PCIe downgrade was requested and failed or was blocked by EOM
        pcie_downgrade_enabled = getattr(self.parsed_args, 'pcie_downgrade', False)
        pcie_failed = getattr(self, 'pcie_downgrade_failed', False)
        pcie_blocked_by_eom = getattr(self, 'pcie_downgrade_blocked_by_eom', False)
        
        if not pcie_downgrade_enabled or (not pcie_failed and not pcie_blocked_by_eom):
            return  # No recovery needed
            
        self.logger.subheader('Attempting PCIe downgrade recovery')
        
        if pcie_failed:
            self.logger.info('PCIe downgrade disable failed during pre-test - attempting recovery with xpu-smi')
        elif pcie_blocked_by_eom:
            self.logger.info('PCIe downgrade disable was blocked by EOM during pre-test - attempting recovery with xpu-smi')
        
        try:
            # Get all GPU instances
            dginstances = self.device_manager.getGpuInstancesDGDiag()
            if len(dginstances) == 0:
                self.logger.warning('No GPU instances found - skipping PCIe recovery')
                return
                
            recovery_success = True
            
            # Run xpu-smi config command on all GPUs
            for gpu_instance in dginstances:
                device_id = gpu_instance - 1  # Convert from 1-based to 0-based indexing for xpu-smi
                recovery_command = f'xpu-smi config -d {device_id} --pciedowngrade 0'
                
                try:
                    self.logger.info(f'Running recovery command for GPU {gpu_instance}: {recovery_command}')
                    result = self.utils.run_command_blocking(recovery_command)
                    self.logger.info(f'GPU {gpu_instance} recovery command completed successfully')
                except Exception as cmd_e:
                    self.logger.error(f'Failed to run recovery command for GPU {gpu_instance}: {cmd_e}')
                    recovery_success = False
                    
            # Check PCIe speeds vs max advertised speeds
            if recovery_success:
                self._check_pcie_speeds_and_recommend_reboot()
            else:
                self.logger.warning('PCIe recovery failed - manual intervention may be required')
                
        except Exception as e:
            self.logger.error(f'Error during PCIe recovery attempt: {e}')
            self.logger.warning('PCIe recovery failed - manual intervention may be required')
                
    def _check_pcie_speeds_and_recommend_reboot(self):
        """
        Check if PCIe speeds are operating at maximum advertised speeds.
        Recommend reboot if speeds are not optimal.
        """
        try:
            self.logger.info('Checking PCIe speeds after recovery attempt')
            
            # Get all GPU instances to check PCIe speeds
            dginstances = self.device_manager.getGpuInstancesDGDiag()
            if len(dginstances) == 0:
                self.logger.warning('No GPU instances found for PCIe speed checking')
                return
                
            reboot_recommended = False
            
            for gpu_instance in dginstances:
                try:
                    # Use platform_utils methods that work with DGDiag instances
                    current_speed = self.platform_utils._get_dgdiag_speed(
                        'PCIE.UTIL.GetLinkSpeed', 'Current Link Speed', device_instance=gpu_instance)
                    max_speed = self.platform_utils._get_dgdiag_speed(
                        'PCIE.UTIL.GetMaxLinkSpeed', 'Max Link Speed', device_instance=gpu_instance)
                    current_width = self.platform_utils._get_dgdiag_speed(
                        'PCIE.UTIL.GetLinkWidth', 'Current Link Width', device_instance=gpu_instance)
                    max_width = self.platform_utils._get_dgdiag_speed(
                        'PCIE.UTIL.GetMaxLinkWidth', 'Max Link Width', device_instance=gpu_instance)
                    
                    # Format PCIe widths properly (DGDiag returns "x16" format, so don't add extra x)
                    current_width_formatted = current_width if current_width and current_width.startswith('x') else f'x{current_width}'
                    max_width_formatted = max_width if max_width and max_width.startswith('x') else f'x{max_width}'
                    
                    self.logger.info(f'GPU Instance {gpu_instance}: Current PCIe: {current_speed} {current_width_formatted}, Max: {max_speed} {max_width_formatted}')
                    
                    # Check if current speed/width is less than maximum, evaluating each independently
                    degraded_speed = False
                    degraded_width = False

                    # Only consider speed degraded if both values are known and not 'Unknown'
                    if current_speed and max_speed and current_speed != 'Unknown' and max_speed != 'Unknown':
                        degraded_speed = current_speed != max_speed

                    # Only consider width degraded if both values are known and not 'Unknown'
                    # Use formatted widths for consistent comparison (e.g., 'x16' vs '16')
                    if current_width and max_width and current_width != 'Unknown' and max_width != 'Unknown':
                        degraded_width = current_width_formatted != max_width_formatted

                    if degraded_speed or degraded_width:
                        reboot_recommended = True
                        self.logger.warning(f'GPU Instance {gpu_instance}: Operating below maximum PCIe capability')
                            
                except Exception as gpu_e:
                    self.logger.error(f'Error checking PCIe speeds for GPU instance {gpu_instance}: {gpu_e}')
                    reboot_recommended = True  # Recommend reboot as precaution if we can't check
                            
            if reboot_recommended:
                self.logger.warning('')
                self.logger.warning('=' * 60)
                self.logger.warning('REBOOT RECOMMENDED')
                self.logger.warning('=' * 60) 
                self.logger.warning('One or more GPUs are not operating at maximum PCIe speed/width.')
                self.logger.warning('A system reboot is recommended to restore optimal PCIe performance.')
                self.logger.warning('=' * 60)
                self.logger.warning('')
            else:
                self.logger.info('All GPUs appear to be operating at maximum PCIe capability')
                
        except Exception as e:
            self.logger.error(f'Error checking PCIe speeds: {e}')
            self.logger.warning('Unable to verify PCIe speeds - reboot recommended as precaution')

    def add_arguments(self):
        """Base implementation that adds common arguments. Subclasses should call super().add_arguments() first."""
        self.add_parser_argument('-cs', 'CPU Stress tool to run in parallel', str, 'None', 'cs', choices=['None', 'stress-ng', 'ptat'])

    def parse_gpu_instance_spec(self, inst_spec):
        """Parse -inst value into sorted unique GPU IDs, or None when '-1' is used.

        Supported formats:
        - '-1' for all GPUs
        - single ID: '2'
        - range: '0-3'
        - list: '0,2,4'
        - mixed list/range: '0-2,5,7-8'
        """
        spec = str(inst_spec).strip()
        if spec == '-1':
            return None

        tokens = [token.strip() for token in spec.split(',') if token.strip()]
        if not tokens:
            raise ValueError("Empty -inst value. Use -1, a single id, a range (e.g. 0-3), or a list (e.g. 0,1,2)")

        requested = set()
        for token in tokens:
            if '-' in token:
                parts = token.split('-')
                if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                    raise ValueError(f"Invalid range token '{token}' in -inst='{spec}'")
                start = int(parts[0])
                end = int(parts[1])
                if start > end:
                    raise ValueError(f"Invalid descending range '{token}' in -inst='{spec}'")
                for gpu_id in range(start, end + 1):
                    requested.add(gpu_id)
            else:
                if not token.isdigit():
                    raise ValueError(f"Invalid GPU id token '{token}' in -inst='{spec}'")
                requested.add(int(token))

        return sorted(requested)

    def resolve_selected_gpu_ids(self, inst_spec=None, available_gpu_count=None):
        """Resolve user-facing zero-based GPU IDs for ``-inst``.

        Args:
            inst_spec: Optional raw ``-inst`` value. Defaults to ``self.parsed_args.inst``.
            available_gpu_count: Optional detected GPU count. When provided, the
                selection is validated against the zero-based range ``0..N-1`` and
                ``-1`` expands to all detected GPU IDs.

        Returns:
            list[int] | None: Sorted zero-based GPU IDs, or None when ``-1`` was
            requested and no GPU count was provided for expansion.
        """
        requested_gpu_ids = self.parse_gpu_instance_spec(
            self.parsed_args.inst if inst_spec is None else inst_spec
        )

        if available_gpu_count is None:
            return requested_gpu_ids

        if available_gpu_count < 0:
            raise ValueError(f"Invalid GPU count {available_gpu_count}")

        available_gpu_ids = list(range(available_gpu_count))
        if requested_gpu_ids is None:
            return available_gpu_ids

        invalid_gpu_ids = [gpu_id for gpu_id in requested_gpu_ids if gpu_id not in available_gpu_ids]
        if invalid_gpu_ids:
            raise ValueError(
                f"Requested GPU IDs {invalid_gpu_ids} are out of range; "
                f"available GPU IDs: {available_gpu_ids}"
            )

        return requested_gpu_ids

    def add_parser_argument(self, arg_name, help_text, arg_type, default_value, dest_name, **kwargs):
        """
        Safely add an argument to the parser, handling duplicates gracefully.
        
        Args:
            arg_name (str): The argument name (e.g., '-testtime')
            help_text (str): Help text for the argument
            arg_type (type): Argument type (int, str, float, etc.)
            default_value: Default value for the argument
            dest_name (str): Destination variable name
            **kwargs: Additional keyword arguments (e.g., choices, action, etc.)
        """
        # Remove any existing actions with the same arg_name OR dest_name
        actions_to_remove = []
        for action in self.input_parser.parser._actions[:]:  # Create a copy to avoid modification during iteration
            if (hasattr(action, 'option_strings') and arg_name in action.option_strings) or \
               (hasattr(action, 'dest') and action.dest == dest_name):
                actions_to_remove.append(action)
        
        # Remove conflicting actions properly
        for action in actions_to_remove:
            # Remove from _actions list
            if action in self.input_parser.parser._actions:
                self.input_parser.parser._actions.remove(action)
            
            # Remove from _option_string_actions dict
            for option_string in getattr(action, 'option_strings', []):
                if option_string in self.input_parser.parser._option_string_actions:
                    del self.input_parser.parser._option_string_actions[option_string]
        
        # Add the new argument
        try:
            self.input_parser.parser.add_argument(
                arg_name, 
                help=help_text, 
                type=arg_type, 
                default=default_value, 
                dest=dest_name,
                **kwargs
            )
        except (argparse.ArgumentError, argparse.ArgumentTypeError, AttributeError, TypeError, ValueError) as e:
            self.logger.error(f"Failed to add argument {arg_name}: {e}")

    def main(self, parsed_args=None):
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        project_root = os.path.dirname(os.path.abspath(__file__))  # This file's directory
        project_root = os.path.abspath(os.path.join(project_root, '..', '..', '..'))  # Go up to project root
        self.logs_dir = os.path.join(project_root, 'logs')
        self.utils.secure_log_directory(self.logs_dir)
        
        # Parse arguments first to get repetition count
        self.add_arguments()
        self.parsed_args = self.input_parser.parse()
        self.parsed_args.tn = self.testNumber
        
        # If orchestrator provided updated args, merge only explicitly-set values
        # This preserves test-specific defaults (e.g. pcie_downgrade=True for Reset/LMT)
        # when the user didn't explicitly override them via CLI or JSON config
        if parsed_args is not None:
            explicitly_set = getattr(parsed_args, '_explicitly_set_args', None)
            for attr_name in dir(parsed_args):
                if not attr_name.startswith('_') and hasattr(self.parsed_args, attr_name):
                    # Only merge attributes explicitly set by user (CLI or JSON config)
                    # If no tracking info, merge all for backward compatibility
                    if explicitly_set is None or attr_name in explicitly_set:
                        setattr(self.parsed_args, attr_name, getattr(parsed_args, attr_name))
        
        # Now modify the test name after all arguments are properly set
        self.modifyTestName()
        
        # Get the number of repetitions from arguments
        # Priority: JSON config repetitions > command line -r > default 1
        repetitions = getattr(self.parsed_args, 'repetitions', 1)
        
        # If repetitions was set via JSON config, it takes priority
        # The JSON value will override the command-line -r value automatically through setattr in testSuiteOrchestrator
        
        overall_results = []
        
        # Create single log file for all repetitions
        # SECURITY: Sanitize test name to prevent path traversal attacks
        from common.utils import Utils
        utils = Utils(self.logger)
        safe_test_name = utils.sanitize_test_name(self.testName)
        log_path = os.path.join(self.logs_dir, f"{safe_test_name}_execution_{self.timestamp}.log")
        
        try:
            with OutputLogger(log_path):
                # Print test header info once at the beginning
                self.printTestName()
                self.printArguments()
                self.printStaticInfo()
                self.disable_pcie_downgrade_if_needed()

                # If PCIe downgrade disable was required but failed, mark test as FAIL immediately
                if getattr(self, 'pcie_downgrade_failed', False):
                    self.overall_test_result = 'FAIL'
                    overall_results.append('FAIL')
                    self.logger.fail_msg('Test marked as FAIL due to PCIe downgrade disable failure')
                    # Still try recovery
                    self._attempt_pcie_recovery_if_needed()
                    # Skip to log rename
                    final_result = 'FAIL'
                    status = 'testFail'
                    new_log_path = os.path.join(self.logs_dir, f"{safe_test_name}_execution_{self.timestamp}_{status}.log")
                    try:
                        os.rename(log_path, new_log_path)
                    except OSError as rename_err:
                        # Avoid letting a log rename failure mask the original PCIe downgrade failure
                        self.logger.error(f"Failed to rename log file '{log_path}' to '{new_log_path}': {rename_err}")
                    return final_result

                for rep in range(repetitions):
                    if repetitions > 1:
                        self.logger.info('')
                        self.logger.subheader(f"=== Running repetition {rep+1}/{repetitions} ===")
                        self.logger.info('')

                        # Create unique timestamp for each repetition to avoid CSV file overwrites
                        self.current_rep_timestamp = f"{self.timestamp}_rep{rep+1:03d}"
                    else:
                        # For single repetition, use original timestamp
                        self.current_rep_timestamp = self.timestamp
                    try:
                        self.prepareCpuCommands()
                        self.prepareGpuCommands()
                        
                        # Skip test execution and result parsing for pre-requisite operations
                        if self.skip_test_execution:
                            overall_results.append(self.overall_test_result)
                            break  # No repetitions needed for pre-requisite ops
                        
                        self.runTest()
                        self.parseResults()
                        
                        overall_results.append(self.overall_test_result)
                        
                        # Check for stop_on_error functionality if repetition failed
                        if getattr(self.parsed_args, 'stop_on_error', False) and self.overall_test_result == "FAIL":
                            self._handle_stop_on_error_repetition(rep+1, repetitions, None)
                            break  # Break out of loop so finalization/rename code still runs
                        
                    except (RuntimeError, AttributeError, TypeError, ImportError, subprocess.CalledProcessError, subprocess.SubprocessError, IOError, OSError) as e:
                        overall_results.append("FAIL")
                        self.logger.error(f"Error in repetition {rep+1}: {e}")
                        
                        # Check for stop_on_error functionality
                        if getattr(self.parsed_args, 'stop_on_error', False):
                            self._handle_stop_on_error_repetition(rep+1, repetitions, e)
                            break  # Break out of loop so finalization/rename code still runs
                        
                        if repetitions > 1:
                            self.logger.fail_msg(f"Repetition {rep+1}/{repetitions}: FAIL (Exception)")
                            self.logger.info(f"Continuing with remaining repetitions...")
                            continue
                        else:
                            raise Exception(e)
                    
                    # Add 30 second delay between repetitions (but not after the last one)
                    if repetitions > 1 and rep < repetitions - 1:
                        self.logger.info(f"Waiting 30 seconds before next repetition...")
                        sleep(30)
            
            # Post-test PCIe recovery if downgrade disable failed
            self._attempt_pcie_recovery_if_needed()
            
            # Determine overall result - PASS only if ALL repetitions pass
            if all(result == "PASS" for result in overall_results):
                final_result = "PASS"
            elif "FAIL" in overall_results:
                final_result = "FAIL"
            elif "UNKNOWN" in overall_results:
                final_result = "UNKNOWN"
            else:
                final_result = "FAIL"  # fallback
            
            # Rename log file with final status
            if final_result == "PASS":
                status = "testPass"
            elif final_result == "UNKNOWN":
                status = "testUnknown"
            else:
                status = "testFail"
            # SECURITY: Use the same sanitized test name as created above
            new_log_path = os.path.join(self.logs_dir, f"{safe_test_name}_execution_{self.timestamp}_{status}.log")
            os.rename(log_path, new_log_path)
            
        except (RuntimeError, AttributeError, TypeError, ImportError, subprocess.CalledProcessError, subprocess.SubprocessError, IOError, OSError) as e:
            # If there's an exception at the top level, mark as FAIL
            final_result = "FAIL"
            self.logger.error(f"Critical error during test execution: {e}")
            # Still try to rename the log file
            try:
                # SECURITY: Use the same sanitized test name as created above  
                new_log_path = os.path.join(self.logs_dir, f"{safe_test_name}_execution_{self.timestamp}_testFail.log")
                os.rename(log_path, new_log_path)
            except (OSError, FileNotFoundError, PermissionError) as e:
                # Expected file operation errors - keep original name
                self.logger.debug(f"Could not rename log file: {e}")
            except (RuntimeError, SystemError, MemoryError, TypeError, AttributeError) as e:
                # Unexpected errors should be investigated
                self.logger.warning(f"Unexpected error renaming log file: {type(e).__name__}: {e}")
            raise Exception(e)
        
        # Print final summary for multiple repetitions (append to log file)
        if repetitions > 1:
            pass_count = overall_results.count("PASS")
            fail_count = overall_results.count("FAIL")
            
            # Append summary directly to the log file
            with open(new_log_path, 'a') as log_file:
                # Write summary without ANSI color codes to log file
                log_file.write('\n')
                log_file.write('=== TEST REPETITION SUMMARY ===\n')
                log_file.write(f'Total repetitions: {repetitions}\n')
                log_file.write(f'Passed: {pass_count}\n')
                log_file.write(f'Failed: {fail_count}\n')
                
                if final_result == "PASS":
                    log_file.write("Overall result: PASS (all repetitions passed)\n")
                else:
                    log_file.write("Overall result: FAIL (one or more repetitions failed)\n")
            
            # Also print to console with colors
            self.logger.info('')
            self.logger.subheader(f"=== Test Repetition Summary ===")
            self.logger.info(f"Total repetitions: {repetitions}")
            self.logger.pass_msg(f"Passed: {pass_count}")
            self.logger.fail_msg(f"Failed: {fail_count}")
            
            if final_result == "PASS":
                self.logger.pass_msg("Overall result: PASS (all repetitions passed)")
            else:
                self.logger.fail_msg("Overall result: FAIL (one or more repetitions failed)")
        
        return final_result
    
    def _handle_stop_on_error_repetition(self, current_rep, total_reps, exception):
        """
        Handle stop_on_error functionality for repetition failures.
        
        Args:
            current_rep: Current repetition number that failed
            total_reps: Total number of repetitions
            exception: Exception that caused the failure (or None if test result was FAIL)
        """
        if exception:
            self.logger.error(f'{"="*60}\n       STOP ON ERROR: Repetition {current_rep}/{total_reps}\n       FAILED with EXCEPTION : {exception}\n       {"="*60}')
        else:
            self.logger.error(f'{"="*60}\n       STOP ON ERROR: Repetition {current_rep}/{total_reps} FAILED\n       {"="*60}')
        
        self.logger.info("")
    
    # Process Management Helper Methods (Phase 2 Error Handling Improvements)
    
    def _cleanup_ptat_processes(self):
        """
        Clean up PTAT processes with proper verification and fallback strategies.
        Returns: bool - True if cleanup successful, False if issues encountered
        """
        success = True
        
        try:
            # First attempt: Graceful termination
            try:
                result = subprocess.run(['sudo', 'pkill', '-TERM', '-f', 'ptat.*-ct'], 
                                      capture_output=True, text=True, timeout=10)
                sleep(2)  # Allow graceful shutdown
            except subprocess.TimeoutExpired:
                self.logger.warning("PTAT graceful termination timed out, proceeding to force kill")
                success = False
            except (OSError, subprocess.SubprocessError) as e:
                self.logger.warning(f"PTAT graceful termination failed: {e}")
                success = False
            
            # Second attempt: Force kill
            try:
                subprocess.run(['sudo', 'pkill', '-KILL', '-f', 'ptat.*-ct'], 
                             capture_output=True, text=True, timeout=5)
                subprocess.run(['sudo', 'pkill', '-KILL', '-f', f'ptat.*{re.escape(self.logs_dir)}'], 
                             capture_output=True, text=True, timeout=5)
            except subprocess.TimeoutExpired:
                self.logger.error("PTAT force kill timed out - processes may still be running")
                success = False
            except (OSError, subprocess.SubprocessError) as e:
                self.logger.error(f"PTAT force kill failed: {e}")
                success = False
            
            # Verification: Check if processes are actually gone
            try:
                remaining = subprocess.run(['pgrep', '-f', 'ptat.*-ct'], 
                                         capture_output=True, text=True, timeout=5)
                if remaining.stdout.strip():
                    self.logger.error(f"PTAT processes still running after cleanup: PIDs {remaining.stdout.strip()}")
                    success = False
            except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
                self.logger.warning(f"Could not verify PTAT process cleanup: {e}")
                
        except (subprocess.SubprocessError, subprocess.TimeoutExpired, ProcessLookupError, OSError, RuntimeError) as e:
            self.logger.error(f"Unexpected error during PTAT cleanup: {e}")
            success = False
        
        return success
    
    def _cleanup_stress_processes(self, proc):
        """
        Clean up stress-ng and related processes with proper verification.
        Args: proc - Main process object to terminate
        Returns: bool - True if cleanup successful, False if issues encountered
        
        IMPORTANT: Uses pkill WITHOUT -f flag for stress-ng patterns to avoid
        killing the main VTS process (whose command line contains '-cs stress-ng').
        Without -f, pkill matches only the process name, not the full command line.
        """
        success = True
        
        try:
            # Step 1: Terminate main process gracefully with longer delays
            try:
                proc.send_signal(signal.SIGTERM) 
                sleep(3)  # Increased delay for stress-ng child processes
                
                # Check if process actually terminated
                if proc.poll() is None:
                    proc.send_signal(signal.SIGKILL)
                    sleep(2)  # Increased delay after SIGKILL
                    
                    if proc.poll() is None:
                        self.logger.warning(f"Process {proc.pid} still running after SIGKILL")
                        success = False
            except (OSError, ProcessLookupError):
                # Process already dead - that's fine
                pass
            except (subprocess.SubprocessError, subprocess.TimeoutExpired, RuntimeError) as e:
                self.logger.warning(f"Failed to terminate main process: {e}")
                success = False
            
            # Step 2: System-wide stress-ng cleanup
            # NOTE: Do NOT use -f flag with pkill for stress-ng patterns.
            # The -f flag matches against the full command line, which would
            # also match 'python3 start_vts.py -cs stress-ng' and kill VTS itself.
            try:
                # Graceful termination - matches process names containing 'stress-ng'
                subprocess.run(['sudo', 'pkill', '-TERM', 'stress-ng'], 
                             capture_output=True, text=True, timeout=10)
                sleep(3)  # Allow time for graceful shutdown
                
                # Force kill any remaining stress-ng processes
                subprocess.run(['sudo', 'pkill', '-KILL', 'stress-ng'], 
                             capture_output=True, text=True, timeout=10)
                sleep(2)  # Allow time for processes to die
                
            except subprocess.TimeoutExpired:
                self.logger.warning("Stress process cleanup timed out")
                success = False
            except (OSError, subprocess.SubprocessError) as e:
                self.logger.warning(f"System-wide stress process cleanup failed: {e}")
                success = False
            
            # Step 3: Use utility cleanup as backup
            try:
                self.utils.killParentandChildProcesses(proc.pid)
                sleep(1)  # Allow utility cleanup to complete
            except (subprocess.CalledProcessError, subprocess.SubprocessError, OSError, AttributeError) as e:
                self.logger.warning(f"Utility process cleanup failed: {e}")
                success = False
                
        except (subprocess.SubprocessError, subprocess.TimeoutExpired, ProcessLookupError, OSError, RuntimeError) as e:
            self.logger.error(f"Unexpected error during stress process cleanup: {e}")
            success = False
        
        return success
    
    def _final_process_cleanup(self, command):
        """
        Final cleanup of CPU stress processes after test completion.
        Args: command - Original command to identify process type
        Returns: bool - True if cleanup successful, False if issues encountered  
        """
        success = True
        
        try:
            if 'ptat' in command:
                # PTAT cleanup with verification
                try:
                    subprocess.run(['sudo', 'pkill', '-TERM', '-f', 'ptat.*-ct'], 
                                 capture_output=True, text=True, timeout=5)
                    sleep(1)
                    subprocess.run(['sudo', 'pkill', '-KILL', '-f', 'ptat.*-ct'], 
                                 capture_output=True, text=True, timeout=5)
                    subprocess.run(['sudo', 'pkill', '-KILL', '-f', f'ptat.*{re.escape(self.logs_dir)}'], 
                                 capture_output=True, text=True, timeout=5)
                    
                    # Verify cleanup
                    check = subprocess.run(['pgrep', '-f', 'ptat'], 
                                         capture_output=True, text=True, timeout=3)
                    if check.stdout.strip():
                        success = False
                        
                except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
                    self.logger.warning(f"PTAT final cleanup encountered issues: {e}")
                    success = False
            else:
                # stress-ng cleanup with verification
                # NOTE: Do NOT use -f flag - it matches full command line and would kill VTS
                try:
                    subprocess.run(['sudo', 'pkill', '-TERM', 'stress-ng'], 
                                 capture_output=True, text=True, timeout=10)
                    sleep(3)  # Longer delay for graceful shutdown
                    
                    subprocess.run(['sudo', 'pkill', '-KILL', 'stress-ng'], 
                                 capture_output=True, text=True, timeout=10)
                    sleep(2)  # Allow processes to die
                    
                    # Verify cleanup - use pgrep without -f to match process names only
                    check = subprocess.run(['pgrep', 'stress-ng'], 
                                         capture_output=True, text=True, timeout=5)
                    if check.stdout.strip():
                        success = False
                        
                except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
                    success = False
                    
        except (subprocess.SubprocessError, subprocess.TimeoutExpired, ProcessLookupError, OSError, RuntimeError) as e:
            self.logger.error(f"Unexpected error during final process cleanup: {e}")
            success = False
        
        return success
    
    def _emergency_process_cleanup(self):
        """
        Emergency cleanup of all stress processes as last resort.
        Returns: bool - True if cleanup successful, False if critical issues remain
        
        IMPORTANT: stress-ng pkill commands use process name matching (no -f flag)
        to avoid killing the main VTS process whose command line contains '-cs stress-ng'.
        PTAT patterns use -f with specific suffixes (e.g., ptat.*-ct) that don't match VTS.
        """
        success = True
        critical_processes_remaining = []
        
        try:
            # Kill all PTAT and stress-ng processes
            # NOTE: stress-ng commands do NOT use -f flag to avoid killing VTS process
            # PTAT commands use -f with specific patterns that won't match VTS
            cleanup_commands = [
                ['sudo', 'pkill', '-KILL', '-f', 'ptat.*-ct'],
                ['sudo', 'pkill', '-KILL', '-f', f'ptat.*{re.escape(self.logs_dir)}'],
                ['sudo', 'pkill', '-KILL', 'stress-ng'],  # No -f: matches process name only
                ['sudo', 'pkill', '-KILL', 'cpuStress'],
            ]
            
            for cmd in cleanup_commands:
                try:
                    subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                    sleep(0.5)  # Brief pause between cleanup commands
                except subprocess.TimeoutExpired:
                    self.logger.error(f"Emergency cleanup command timed out: {' '.join(cmd)}")
                    success = False
                except (subprocess.CalledProcessError, subprocess.SubprocessError, OSError) as e:
                    self.logger.warning(f"Emergency cleanup command failed: {' '.join(cmd)} - {e}")
                    # Continue with other cleanup attempts
            
            sleep(3)  # Increased delay to allow processes to die
            
            # Final verification - check for any remaining problematic processes
            # NOTE: stress-ng verification uses pgrep without -f to avoid false positives from VTS
            verification_commands = [
                (['pgrep', '-f', 'ptat'], 'PTAT'),
                (['pgrep', 'stress-ng'], 'stress-ng'),  # No -f: matches process name only
                (['pgrep', 'cpuStress'], 'cpuStress')
            ]
            
            for cmd, proc_type in verification_commands:
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                    if result.stdout.strip():
                        remaining_count = len(result.stdout.strip().split('\n'))
                        critical_processes_remaining.append(f"{proc_type}: {remaining_count} processes")
                        success = False
                except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
                    self.logger.warning(f"Could not verify {proc_type} cleanup: {e}")
            
            # Remaining processes are expected during normal cleanup - no warning needed
                
        except (subprocess.SubprocessError, subprocess.TimeoutExpired, ProcessLookupError, OSError, RuntimeError) as e:
            self.logger.error(f"Emergency process cleanup failed with unexpected error: {e}")
            success = False
        
        return success
