# Copyright (C) 2024-2026 Intel Corporation
# Verification Test Suite
# DGDiag Power Stress Test

from .dgdiag_base import dgdiagBase
from common.common_defs import (REALISTIC_POWER_MIN_W, REALISTIC_POWER_MAX_W,
                                REALISTIC_TEMP_MIN_C, REALISTIC_TEMP_MAX_C,
                                REALISTIC_FREQ_MIN_MHZ, REALISTIC_FREQ_MAX_MHZ)
import os
import subprocess # nosec
from datetime import datetime
from time import sleep

import pandas as pd

class testClass(dgdiagBase):
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)
        self.testName = 'Power_and_Thermal_Stress_Test'
        self.pass_fail_results = None
    
    def add_arguments(self):
        super().add_arguments()
        
        # Add all arguments using the helper function
        self.add_parser_argument(
            '-inst',
            "GPU device instance spec: -1 (all), single ID (e.g. 2), range (e.g. 0-3), or list (e.g. 0,1,2,3)",
            str,
            '-1',
            'inst'
        )
        self.add_parser_argument('-testtime', 'Time duration to run Stress Test in secs', int, 300, 'testtime')
        
        # Override CPU stress default to stress-ng for power and thermal testing
        self.add_parser_argument('-cs', 'CPU Stress tool to run in parallel', str, 'stress-ng', 'cs', choices=['None', 'stress-ng', 'ptat'])
    
    def prepareGpuCommands(self):
        super().prepareGpuCommands()
        self.gpuCommands = []
        envVars = f''

        try:
            _requested_gpu_ids, target_instances = self.resolve_selected_gpu_instances()
        except ValueError as parse_error:
            self.logger.error(f"Invalid -inst argument: {parse_error}")
            return STATUS_FAILED
        if not target_instances:
            self.logger.error("No GPU instances selected for power/thermal stress test")
            return STATUS_FAILED
        inst = ','.join(str(gpu_id) for gpu_id in target_instances)

        self.gpuCommands.append(f'{envVars}./DGDiagTool -SYSTEM.UTIL.GFXStress inst={inst} interface=Vulkan testtime={self.parsed_args.testtime} headless=1')

    def _execute_processes(self, monProcess, cpuProcess, gpuProcess):
        """
        Execute test processes in a specific order. Can be overridden in child classes
        to customize the execution sequence and timing.
        
        Args:
            monProcess: GPU monitoring process
            cpuProcess: CPU stress process
            gpuProcess: GPU stress process
        """
        # Custom execution order: CPU Stress -> GPU Stress -> Monitor
        if self.parsed_args.cs != 'None' and cpuProcess is not None:
            cpuProcess.start()
            self.logger.info(f'Starting {cpuProcess.name} at {datetime.now().strftime("%Y-%m-%d_%H%M%S")}...')
            sleep(30)

        gpuProcess.start()
        self.logger.info(f'Starting {gpuProcess.name} at {datetime.now().strftime("%Y-%m-%d_%H%M%S")}...')
        sleep(5)

        if self.parsed_args.mt != 'None' and monProcess is not None:
            monProcess.start()
            self.logger.info("")
            self.logger.info(f'Starting {monProcess.name} at {datetime.now().strftime("%Y-%m-%d_%H%M%S")}...')
            self.logger.info("")
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
        self._collect_results(queue_gpu, self.gpu_test_results, "GPU Stress", gpuProcess)

        # Custom join order: GPU Monitoring -> GPU Stress -> CPU Stress
        if self.parsed_args.mt != 'None' and monProcess is not None:
            
            # Signal monitor to stop first
            if stop_event_mon is not None:
                stop_event_mon.set()
            self._collect_results(queue_mon, self.mon_test_results, "GPU Monitor", monProcess)
            monProcess.join()
            self.logger.info(f'{monProcess.name} finished at {datetime.now().strftime("%Y-%m-%d_%H%M%S")}.')
            
            # Extract CSV file path from monitoring results
            if self.mon_test_results and self.mon_test_results[0] and isinstance(self.mon_test_results[0], str):
                self.monCsvFilePath = self.mon_test_results[0]

        gpuProcess.join()
        
        # Collect return codes from the queue with timeout
        try:
            self.gpu_return_codes = queue_gpu_return_codes.get(timeout=10)
        except Exception as e:
            self.gpu_return_codes = []
            
        self.logger.info(f'{gpuProcess.name} finished at {datetime.now().strftime("%Y-%m-%d_%H%M%S")}.')
        sleep(10)

        if self.parsed_args.cs != 'None' and cpuProcess is not None:
            # Signal cpu stress to stop after gpu processes finish
            if stop_event_cpu is not None:
                stop_event_cpu.set()
            self._collect_results(queue_cpu, self.cpu_test_results, "CPU Stress", cpuProcess)
            cpuProcess.join()
            self.logger.info(f'{cpuProcess.name} finished at {datetime.now().strftime("%Y-%m-%d_%H%M%S")}.')

    def parseResults(self):
        self.logger.subheader('Results Parsing...')

        # Analyze monitoring CSV file if available
        device_stats = None
        if self.monCsvFilePath is not None and os.path.exists(self.monCsvFilePath):
            device_stats = self._analyze_monitoring_data()

        # Run sanity check using systemReboot.sh testonly
        sanity_check_result = self._run_sanity_check()

        # Process results based on analysis and sanity check
        self._process_test_results(device_stats, sanity_check_result)
        
        # Store device stats for future use
        self.device_stats = device_stats

    def _run_sanity_check(self):
        """Run sanity check using systemReboot.sh testonly command."""
        self.logger.subheader('Running Post-Test Sanity Check...')
        
        try:
            # Find the systemReboot.sh script in the reset test directory
            reset_dir = os.path.join(os.path.dirname(__file__), '..', 'reset')
            script_path = os.path.join(reset_dir, 'systemReboot.sh')
            
            if not os.path.exists(script_path):
                self.logger.warning(f"systemReboot.sh not found at {script_path}")
                return False
                
            # Make script executable
            if not self.utils.make_script_executable(script_path):
                self.logger.warning(f"Could not make systemReboot.sh executable at {script_path}")
                return False
            
            # Ensure reset content binaries are executable (ze_peak, LTSSMtool)
            content_dir = os.path.join(reset_dir, 'content')
            for binary_name in ['ze_peak', 'LTSSMtool']:
                binary_path = os.path.join(content_dir, binary_name)
                if os.path.exists(binary_path):
                    if not self.utils.make_script_executable(binary_path):
                        self.logger.warning(f"Could not make {binary_name} executable at {binary_path}")
                else:
                    self.logger.debug(f"Reset content binary not found: {binary_path} (may not be needed for sanity check)")
            
            # Execute testonly command
            self.logger.info(f"Running sanity check: bash {script_path} testonly")
            
            # Run command with shell=False for security
            result = subprocess.run(
                ['bash', script_path, 'testonly'],
                capture_output=True,
                text=True
            )
            
            if result.stdout:
                self.logger.log_file_only("")
                self.logger.log_file_only(f"\tSanity check stdout:\n\n{result.stdout}")
            
            if result.stderr:
                self.logger.log_file_only("")
                self.logger.log_file_only(f"\tSanity check stderr:\n\n{result.stderr}")
            
            sanity_passed = result.returncode == 0
            
            self.logger.info("")
            if sanity_passed:
                self.logger.pass_msg('Sanity Check: PASS')
            else:
                self.logger.fail_msg('Sanity Check: FAIL')
                    
            return sanity_passed
                
        except Exception as e:
            self.logger.error(f"Error running sanity check: {e}")
            return False

    def _process_test_results(self, device_stats, sanity_check_result):
        """Process test results and determine overall pass/fail status."""
        # Check if monitoring data is available
        if device_stats is None:
            # If no monitoring data, result depends only on sanity check
            if sanity_check_result:
                self.overall_test_result = 'UNKNOWN'
                self.logger.warning('No monitoring data available for power and thermal analysis')
                self.logger.warning(f'OVERALL TEST RESULT : {self.overall_test_result} (Sanity Check: PASS)')
            else:
                self.overall_test_result = 'FAIL'
                self.logger.warning('No monitoring data available for power and thermal analysis')
                self.logger.info('Review log files for sanity check details')
                self.logger.info('')
                self.logger.fail_msg(f'OVERALL TEST RESULT : {self.overall_test_result}')
        else:
            # Check return codes and power/thermal results: PASS only if all conditions met
            gpu_commands_passed = self.gpu_return_codes and all(code == 0 for code in self.gpu_return_codes)
            
            # Check if any device failed power or thermal tests
            power_thermal_passed = True
            if self.pass_fail_results:
                for device_key, results in self.pass_fail_results.items():
                    # Check if results start with 'FAIL' since they now include detailed values
                    power_key = 'Power Result (Measured/Expected)' if 'Power Result (Measured/Expected)' in results else 'Power Result'
                    thermal_key = 'Thermal Result (Measured/Expected)' if 'Thermal Result (Measured/Expected)' in results else 'Thermal Result'
                    
                    if results[power_key].startswith('FAIL') or results[thermal_key].startswith('FAIL'):
                        power_thermal_passed = False
                        break
            
            # Overall result: PASS only if GPU commands, power/thermal tests, and sanity check all pass
            self.logger.info("")
            if gpu_commands_passed and power_thermal_passed and sanity_check_result:
                self.overall_test_result = 'PASS'
                self.logger.pass_msg(f'OVERALL TEST RESULT : {self.overall_test_result}')
            else:
                self.overall_test_result = 'FAIL'
                if not gpu_commands_passed:
                    self.logger.fail_msg('GPU commands failed')
                    self.logger.info("")
                if not power_thermal_passed:
                    self.logger.fail_msg('Power or thermal criteria not met')
                    self.logger.info("")
                if not sanity_check_result:
                    self.logger.info('Review log files for sanity check details')
                    self.logger.info("")
                self.logger.fail_msg(f'OVERALL TEST RESULT : {self.overall_test_result}')

    def _filter_by_timestamp(self, df):
        """Filter dataframe by timestamp to include only data after initial idle period and within test time."""
        try:
            # Convert Timestamp column to datetime
            df['Timestamp'] = pd.to_datetime(df['Timestamp'])
            
            # Sort by timestamp to ensure chronological order
            df = df.sort_values('Timestamp').reset_index(drop=True)
            
            if len(df) == 0:
                self.logger.warning("No data points found after timestamp conversion")
                return df
            
            start_time = df['Timestamp'].iloc[0]
            
            # Find the first active point (when GPU becomes active)
            first_active_idx = None
            
            # Check for EU Array Idle column with different possible names
            eu_idle_col = None
            possible_names = ['GPU EU Array Idle (%)', 'GPU EU Array Idle(%)', 'EU Array Idle (%)', 'EU Array Idle(%)']
            for col_name in possible_names:
                if col_name in df.columns:
                    eu_idle_col = col_name
                    break
            
            if eu_idle_col is not None:
                # Look for first non-idle point (using < 99.0 threshold)
                for i, idx in enumerate(df.index):
                    eu_idle_val = df.loc[idx, eu_idle_col]
                    if pd.notna(eu_idle_val) and eu_idle_val < 99.0:
                        first_active_idx = idx
                        break
            
            # Determine the start time for averaging
            if first_active_idx is not None:
                active_start_time = df.loc[first_active_idx, 'Timestamp']
            else:
                # If no EU idle data available, assume immediate start (skip initial few seconds as buffer)
                active_start_time = start_time + pd.Timedelta(seconds=30)  # 30 second buffer
            
            # Calculate the end time based on testtime parameter
            test_end_time = active_start_time + pd.Timedelta(seconds=self.parsed_args.testtime)
            
            # Filter data to include only the active test period
            filtered_df = df[(df['Timestamp'] >= active_start_time) & (df['Timestamp'] <= test_end_time)]
            
            return filtered_df
            
        except Exception as e:
            self.logger.warning(f"Error in timestamp filtering: {e}. Falling back to EU idle-based filtering.")
            return df

    def _detect_thermal_throttling(self, filtered_data):
        """Detect thermal throttling in the filtered data."""
        thermal_throttle_detected = False
        if 'Throttle reason' in filtered_data.columns:
            throttle_data = filtered_data['Throttle reason'].dropna()
            thermal_throttle_detected = any('thermal' in str(reason).lower() for reason in throttle_data if pd.notna(reason))
        return thermal_throttle_detected

    def _filter_realistic_values(self, data, column_name):
        """Filter out unrealistic sensor values that could be measurement errors.
        Uses shared constants from common_defs for consistency across all tests."""
        if len(data) == 0:
            return data
            
        # Define realistic ranges for different sensor types using shared constants
        realistic_ranges = {
            'GPU Power (W)': (REALISTIC_POWER_MIN_W, REALISTIC_POWER_MAX_W),
            'GPU Core Temperature (Celsius Degree)': (REALISTIC_TEMP_MIN_C, REALISTIC_TEMP_MAX_C),
            'GPU Memory Temperature (Celsius Degree)': (REALISTIC_TEMP_MIN_C, REALISTIC_TEMP_MAX_C),
            'GPU EU Array Idle (%)': (0.0, 100.0),  # Percentage 0-100%
        }
        
        # Get the range for this column type
        if column_name in realistic_ranges:
            min_val, max_val = realistic_ranges[column_name]
            # Filter out values outside realistic range
            filtered_data = data[(data >= min_val) & (data <= max_val)]
            
            # Log if significant filtering occurred
            if len(filtered_data) < len(data) * 0.9:  # If >10% of data filtered
                filtered_count = len(data) - len(filtered_data)
                self.logger.warning(f"Filtered {filtered_count}/{len(data)} unrealistic {column_name} values (outside {min_val}-{max_val} range)")
            
            return filtered_data
        else:
            # No filtering for unknown column types
            return data

    def _analyze_monitoring_data(self):
        """Analyze the monitoring CSV file and return summary statistics dictionary."""
        
        try:
            # Read the CSV file
            df = pd.read_csv(self.monCsvFilePath)
            
            # Strip whitespace from column names
            df.columns = df.columns.str.strip()
            
            # Check if required columns exist
            required_cols = ['DeviceId', 'GPU Power (W)', 'GPU Core Temperature (Celsius Degree)', 'GPU Memory Temperature (Celsius Degree)']
            missing_cols = [col for col in required_cols if col not in df.columns]
            
            if missing_cols:
                self.logger.warning(f"Missing columns in CSV: {missing_cols}")
                self.logger.info(f"Available columns: {list(df.columns)}")
                return None
            
            # Convert columns to numeric, replacing invalid values with NaN
            numeric_cols = ['GPU Power (W)', 'GPU Core Temperature (Celsius Degree)', 'GPU Memory Temperature (Celsius Degree)']
            
            # Add EU Array Idle column if it exists (optional for filtering)
            possible_eu_cols = ['GPU EU Array Idle (%)', 'GPU EU Array Idle(%)', 'EU Array Idle (%)', 'EU Array Idle(%)']
            for col_name in possible_eu_cols:
                if col_name in df.columns:
                    numeric_cols.append(col_name)
                    break
            
            # Check if Throttle reason column exists (used for thermal analysis)
            throttle_col_exists = 'Throttle reason' in df.columns
            
            for col in numeric_cols:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            # Handle timestamp-based filtering if Timestamp column is available
            if 'Timestamp' in df.columns:
                df = self._filter_by_timestamp(df)
            else:
                pass  # Use EU Array Idle-based filtering as fallback
            
            # Group by DeviceId and calculate statistics
            device_stats = {}
            raw_device_stats = {}  # Store raw numeric values for future processing
            
            for device_id in df['DeviceId'].unique():
                if pd.isna(device_id):
                    continue
                    
                device_data = df[df['DeviceId'] == device_id]
                
                # If timestamp filtering was used, data is already filtered
                if 'Timestamp' in df.columns and len(device_data) > 0:
                    filtered_data = device_data
                else:
                    # Fall back to EU Array Idle filtering for legacy behavior
                    # Filter out only initial idle data points (before stress test starts)
                    # Once GPU becomes active, include all subsequent data points even if it goes idle again
                    first_active_idx = None
                    
                    # Check for EU Array Idle column with different possible names
                    eu_idle_col = None
                    possible_names = ['GPU EU Array Idle (%)', 'GPU EU Array Idle(%)', 'EU Array Idle (%)', 'EU Array Idle(%)']
                    for col_name in possible_names:
                        if col_name in device_data.columns:
                            eu_idle_col = col_name
                            break
                    
                    if eu_idle_col is None:
                        # No EU idle column found, use all data
                        filtered_data = device_data
                    else:
                        # Look for first non-idle point (using < 99.0 threshold)
                        for i, idx in enumerate(device_data.index):
                            eu_idle_val = device_data.loc[idx, eu_idle_col]
                            if pd.notna(eu_idle_val) and eu_idle_val < 99.0:
                                first_active_idx = idx
                                break
                        
                        if first_active_idx is not None:
                            # Include all data from first active point onwards
                            filtered_data = device_data.loc[first_active_idx:]
                        else:
                            # All data points are idle - use original data
                            filtered_data = device_data
                
                # Calculate power statistics from filtered data
                power_data = filtered_data['GPU Power (W)'].dropna()
                core_temp_data = filtered_data['GPU Core Temperature (Celsius Degree)'].dropna()
                mem_temp_data = filtered_data['GPU Memory Temperature (Celsius Degree)'].dropna()
                
                # Apply realistic value filtering to remove sensor errors
                power_data = self._filter_realistic_values(power_data, 'GPU Power (W)')
                core_temp_data = self._filter_realistic_values(core_temp_data, 'GPU Core Temperature (Celsius Degree)')
                mem_temp_data = self._filter_realistic_values(mem_temp_data, 'GPU Memory Temperature (Celsius Degree)')
                
                # Check for thermal throttling if Throttle reason column exists
                thermal_throttle_detected = self._detect_thermal_throttling(filtered_data)
                
                if len(power_data) == 0 and len(core_temp_data) == 0 and len(mem_temp_data) == 0:
                    continue
                
                device_key = f"Device {int(device_id)}"
                
                # Store formatted data for display with reordered columns
                device_stats[device_key] = {
                    'Device ID': f"{int(device_id)}",
                    'Power Min (W)': f"{power_data.min():.2f}" if len(power_data) > 0 else "N/A",
                    'Power Avg (W)': f"{power_data.mean():.2f}" if len(power_data) > 0 else "N/A",
                    'Power Max (W)': f"{power_data.max():.2f}" if len(power_data) > 0 else "N/A",
                    'Core Temp Min (°C)': f"{core_temp_data.min():.1f}" if len(core_temp_data) > 0 else "N/A",
                    'Core Temp Avg (°C)': f"{core_temp_data.mean():.1f}" if len(core_temp_data) > 0 else "N/A",
                    'Core Temp Max (°C)': f"{core_temp_data.max():.1f}" if len(core_temp_data) > 0 else "N/A",
                    'Mem Temp Min (°C)': f"{mem_temp_data.min():.1f}" if len(mem_temp_data) > 0 else "N/A",
                    'Mem Temp Avg (°C)': f"{mem_temp_data.mean():.1f}" if len(mem_temp_data) > 0 else "N/A",
                    'Mem Temp Max (°C)': f"{mem_temp_data.max():.1f}" if len(mem_temp_data) > 0 else "N/A"
                }
                
                # Store raw numeric data for future processing
                raw_device_stats[device_key] = {
                    'power_avg': power_data.mean() if len(power_data) > 0 else None,
                    'power_min': power_data.min() if len(power_data) > 0 else None,
                    'power_max': power_data.max() if len(power_data) > 0 else None,
                    'core_temp_avg': core_temp_data.mean() if len(core_temp_data) > 0 else None,
                    'core_temp_min': core_temp_data.min() if len(core_temp_data) > 0 else None,
                    'core_temp_max': core_temp_data.max() if len(core_temp_data) > 0 else None,
                    'mem_temp_avg': mem_temp_data.mean() if len(mem_temp_data) > 0 else None,
                    'mem_temp_min': mem_temp_data.min() if len(mem_temp_data) > 0 else None,
                    'mem_temp_max': mem_temp_data.max() if len(mem_temp_data) > 0 else None,
                    'thermal_throttle_detected': thermal_throttle_detected
                }
            
            if device_stats:
                self.logger.info("")
                self.logger.info("Power and Thermal Statistics per Device:")
                self.logger.printTableFromDict(device_stats)
                
                # Create pass/fail results table
                self._create_pass_fail_table(raw_device_stats)
                
                return {'display': device_stats, 'raw': raw_device_stats}
            else:
                self.logger.warning("No valid device data found in monitoring CSV")
                return None
                
        except ImportError:
            self.logger.error("pandas library is required for CSV analysis. Please install pandas.")
            return None
        except Exception as e:
            self.logger.error(f"Error analyzing monitoring data: {e}")
            return None

    def _create_pass_fail_table(self, raw_device_stats):
        """Create a pass/fail results table based on power and thermal criteria."""
        
        # Get platform specifications
        specs = self.platform_defs_instance.power_thermal_specs
        power_threshold = specs['power']['min_threshold_watts']
        
        self.pass_fail_results = {}
        
        for device_key, stats in raw_device_stats.items():
            device_id = device_key.split()[1]  # Extract device ID from "Device X"
            
            # Get power and thermal results
            power_result_text = self._evaluate_power_result(stats, power_threshold)
            thermal_result_text = self._evaluate_thermal_result(stats)
            
            self.pass_fail_results[device_key] = {
                'Device ID': device_id,
                'Power Result (Measured/Expected)': power_result_text,
                'Thermal Result (Measured/Expected)': thermal_result_text
            }
        
        if self.pass_fail_results:
            self.logger.info("")
            self.logger.info("Pass/Fail Results per Device:")
            self.logger.printTableFromDict(self.pass_fail_results)

    def _evaluate_power_result(self, stats, power_threshold):
        """Evaluate power result for a device. Can be overridden by subclasses."""
        # Power Result: PASS if average power > threshold, FAIL otherwise
        power_result = "FAIL"
        power_avg = stats['power_avg'] if stats['power_avg'] is not None else 0
        if power_avg >= power_threshold:
            power_result = "PASS"
        
        # Format power result with measured/threshold values
        return f"{power_result} ({power_avg:.1f}W/{power_threshold:.1f}W)"

    def _evaluate_thermal_result(self, stats):
        """Evaluate thermal result for a device. Can be overridden by subclasses."""
        # Thermal Result: PASS if no thermal throttling detected, FAIL if thermal throttling found
        thermal_result = "PASS"
        max_core_temp = stats['core_temp_max'] if stats['core_temp_max'] is not None else 0
        max_mem_temp = stats['mem_temp_max'] if stats['mem_temp_max'] is not None else 0
        
        # Check for thermal throttling
        if stats.get('thermal_throttle_detected', False):
            thermal_result = "FAIL"
        
        # Format thermal result with throttle status and temperature info
        throttle_status = "Throttling Detected" if stats.get('thermal_throttle_detected', False) else "No Throttling"
        return f"{thermal_result} ({throttle_status}, Core:{max_core_temp:.1f}°C, Mem:{max_mem_temp:.1f}°C)"

    def _filter_device_for_active_periods(self, device_data):
        """Filter device data to remove initial idle periods.
        
        This method can be reused by subclasses that need to filter individual
        device data for active periods only (e.g., for pulse detection).
        """
        # Check for EU Array Idle column with different possible names
        eu_idle_col = None
        possible_names = ['GPU EU Array Idle (%)', 'GPU EU Array Idle(%)', 'EU Array Idle (%)', 'EU Array Idle(%)']
        for col_name in possible_names:
            if col_name in device_data.columns:
                eu_idle_col = col_name
                break
        
        if eu_idle_col is None:
            # No EU idle column found, use all data
            return device_data
        
        # Look for first non-idle point (using < 99.0 threshold)
        first_active_idx = None
        for idx in device_data.index:
            eu_idle_val = device_data.loc[idx, eu_idle_col]
            if pd.notna(eu_idle_val) and eu_idle_val < 99.0:
                first_active_idx = idx
                break
        
        if first_active_idx is not None:
            # Include all data from first active point onwards
            return device_data.loc[first_active_idx:]
        else:
            # All data points are idle - use original data
            return device_data
