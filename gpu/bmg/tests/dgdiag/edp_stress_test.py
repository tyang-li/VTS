# Copyright (C) 2024-2026 Intel Corporation
# Verification Test Suite
# DGDiag Excursion Design Power Stress Test

from .power_thermal_stress_test import testClass as PowerThermalStressTest
from ... import platform_defs
import pandas as pd

class testClass(PowerThermalStressTest):
    # Minimum number of consecutive readings required to consider a valid pulse
    # This filters out noise and transient spikes that don't represent actual power pulses
    MIN_PULSE_DURATION_READINGS = 2
    
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)
        self.sampling_rate = 200
        self.testName = 'Excursion_Design_Power_Stress_Test'

    def add_arguments(self):
        super().add_arguments()
        
        # Add all arguments using the helper function
        self.add_parser_argument('-at', 'Active time for the stress test in sec', int, 3, 'active_time')
        self.add_parser_argument('-it', 'Idle time for the stress test in sec', int, 3, 'idle_time')
    
    def prepareGpuCommands(self):
        super().prepareGpuCommands()
        self.gpuCommands = []
        
        # Ensure power thermal specs are initialized (lazy loading trigger)
        _ = self.platform_defs_instance.power_thermal_specs
        
        # Get DGDiag duration buffer from platform configuration (following dgdiagBase pattern)
        dgdiag_buffer = self.platform_defs_instance.test_timeout_dict['EDP_STRESS_TEST']['dgdiag_duration_buffer']

        if self.parsed_args.inst == -1:
            for inst in self.dginstances:
                self.gpuCommands.append(f'./DGDiagTool -PM.TEST.PulseStress inst={inst} duration={self.parsed_args.testtime+dgdiag_buffer} active_time={self.parsed_args.active_time} idle_time={self.parsed_args.idle_time} headless=1 stime=5000')
        else:
            self.gpuCommands.append(f'./DGDiagTool -PM.TEST.PulseStress inst={self.parsed_args.inst} duration={self.parsed_args.testtime+dgdiag_buffer} active_time={self.parsed_args.active_time} idle_time={self.parsed_args.idle_time} headless=1 stime=5000')
    
    def _should_timeout_gpu_command(self, elapsed_time):
        """
        Check if the GPU command should be terminated due to timeout.
        For EDP stress test, timeout after testtime + configurable buffer.
        
        Args:
            elapsed_time (float): Time elapsed since command started in seconds
            
        Returns:
            bool: True if command should be terminated, False otherwise
        """
        # Ensure power thermal specs are initialized (lazy loading trigger)
        _ = self.platform_defs_instance.power_thermal_specs
        
        # Get timeout buffer from platform configuration (following dgdiagBase pattern)
        timeout_buffer = self.platform_defs_instance.test_timeout_dict['EDP_STRESS_TEST']['timeout_buffer']
        timeout_threshold = self.parsed_args.testtime + timeout_buffer
        return elapsed_time > timeout_threshold

    def _evaluate_power_result(self, stats, power_threshold):
        """Evaluate EDP-specific power result including pulse count and peak power."""
        power_avg = stats['power_avg'] if stats['power_avg'] is not None else 0
        power_max = stats['power_max'] if stats['power_max'] is not None else 0
        
        # Get expected pulse count and peak power criteria
        expected_pulse_count = self._calculate_expected_pulse_count()
        actual_pulse_count = stats.get('pulse_count', 0)
        
        # Use the same power threshold from platform specs (0.95 * PsysPL2)
        expected_peak_power = power_threshold
        
        # Check pulse count (allow 10% tolerance - pass if >= 90% of expected)
        pulse_count_threshold = expected_pulse_count * 0.9
        pulse_count_ok = actual_pulse_count >= pulse_count_threshold
        
        # Check peak power (should reach at least the platform threshold)
        peak_power_ok = power_max >= expected_peak_power if expected_peak_power > 0 else False
        
        # Overall power result
        power_result = "PASS" if pulse_count_ok and peak_power_ok else "FAIL"
        
        # Format detailed result
        pulse_status = f"Pulses: {actual_pulse_count}/{expected_pulse_count}"
        peak_status = f"Peak: {power_max:.1f}W/{expected_peak_power:.1f}W" if expected_peak_power > 0 else f"Peak: {power_max:.1f}W/Unknown"
        
        return f"{power_result} ({pulse_status}, {peak_status})"

    def _calculate_expected_pulse_count(self):
        """Calculate expected number of power pulses based on test parameters."""
        cycle_time = self.parsed_args.active_time + self.parsed_args.idle_time
        expected_cycles = self.parsed_args.testtime // cycle_time
        return expected_cycles

    def _analyze_monitoring_data(self):
        """Override monitoring data analysis to use consistent filtering for EDP pulse tests."""
        
        if not hasattr(self, 'monCsvFilePath') or not self.monCsvFilePath:
            self.logger.warning("Monitoring CSV file path not available")
            return None
            
        try:
            df = pd.read_csv(self.monCsvFilePath)
            df.columns = df.columns.str.strip()
            
            # Skip timestamp filtering for EDP tests since devices may start pulsing at different times
            # EDP pulse tests have staggered device activation by design
            
            # Convert numeric columns to numeric type
            numeric_cols = ['GPU Power (W)', 'GPU Core Temperature (Celsius Degree)', 'GPU Memory Temperature (Celsius Degree)']
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            
            # Process each device without EU Array Idle filtering for EDP tests
            device_stats = {}
            raw_device_stats = {}
            
            for device_id in df['DeviceId'].unique():
                if pd.isna(device_id):
                    continue
                    
                device_data = df[df['DeviceId'] == device_id]
                
                # For EDP tests, use all device data without EU Array Idle filtering
                # This ensures we capture all pulse cycles (active + idle periods)
                filtered_data = device_data
                
                # Calculate power statistics from unfiltered data
                power_data = filtered_data['GPU Power (W)'].dropna()
                core_temp_data = filtered_data['GPU Core Temperature (Celsius Degree)'].dropna()
                mem_temp_data = filtered_data['GPU Memory Temperature (Celsius Degree)'].dropna()
                
                # Apply realistic value filtering to remove sensor errors
                power_data = self._filter_realistic_values(power_data, 'GPU Power (W)')
                core_temp_data = self._filter_realistic_values(core_temp_data, 'GPU Core Temperature (Celsius Degree)')
                mem_temp_data = self._filter_realistic_values(mem_temp_data, 'GPU Memory Temperature (Celsius Degree)')
                
                # Check for thermal throttling
                thermal_throttle_detected = self._detect_thermal_throttling(filtered_data)
                
                if len(power_data) == 0 and len(core_temp_data) == 0 and len(mem_temp_data) == 0:
                    continue
                
                device_key = f"Device {int(device_id)}"
                
                # Store formatted data for display
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
                
                # Store raw numeric data for pass/fail evaluation
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
                
                # Add pulse count for EDP evaluation
                pulse_count = self._count_power_pulses(device_data)
                raw_device_stats[device_key]['pulse_count'] = pulse_count
            
            # Display power and thermal statistics
            if device_stats:
                self.logger.info("")
                self.logger.info("Power and Thermal Statistics per Device:")
                self.logger.printTableFromDict(device_stats)
            
            # Create pass/fail table
            self._create_pass_fail_table(raw_device_stats)
            
            return {'formatted': device_stats, 'raw': raw_device_stats}
            
        except Exception as e:
            self.logger.warning(f"Error analyzing monitoring data: {e}")
            return None

    def _count_power_pulses(self, device_data):
        """Count the number of power pulses in the device data."""
        if 'GPU Power (W)' not in device_data.columns:
            return 0
            
        # For EDP pulse tests, don't apply EU Array Idle filtering as it can remove valid pulse periods
        # EDP tests alternate between active and idle periods by design, so filtering based on
        # EU idle percentage would incorrectly remove the idle periods between pulses
        
        # Only apply realistic value filtering to remove sensor errors
        power_data = pd.to_numeric(device_data['GPU Power (W)'], errors='coerce').dropna()
        power_data = self._filter_realistic_values(power_data, 'GPU Power (W)')
        
        if len(power_data) < 2:
            return 0
        
        # Calculate power threshold for pulse detection
        # For EDP tests with varying active/idle ratios, use a more robust baseline calculation
        
        # Method 1: Try using minimum + margin as baseline (works well for short idle periods)
        baseline_min = power_data.min()
        baseline_max = power_data.max()
        power_range = baseline_max - baseline_min
        baseline_margin = baseline_min + power_range * 0.2  # 20% above minimum (reduced from 30%)
        
        # Method 2: Use percentile that adapts based on active/idle ratio
        active_ratio = self.parsed_args.active_time / (self.parsed_args.active_time + self.parsed_args.idle_time)
        
        # Use a more adaptive approach based on the power range and active ratio
        if power_range < 10.0:  # Very low power variation - likely all low power
            # For devices with minimal power variation, use a very low threshold
            baseline_power = power_data.quantile(0.1)  # 10th percentile as baseline
            threshold = baseline_power + max(3.0, power_range * 0.5)  # At least 3W above baseline
        elif active_ratio > 0.7:  # For active-heavy tests (like 7s+1s), use lower percentile or min-based
            baseline_power = baseline_margin
            threshold = baseline_power * 1.4  # Reduced multiplier from 1.5
        else:  # For balanced or idle-heavy tests, use original method
            baseline_power = power_data.quantile(0.2)  # 20th percentile as baseline (reduced from 25th)
            threshold = baseline_power * 1.8  # Reduced multiplier from 2.0
        
        # For EDP tests, we need to detect sustained high-power periods, not individual spikes
        # Create boolean series indicating when power is above threshold
        above_threshold = power_data > threshold
        
        # Detect the start of high-power periods (transitions from low to high)
        # Use a more robust approach that handles noisy data
        pulse_starts = above_threshold & (~above_threshold.shift(1, fill_value=False))
        pulse_count = pulse_starts.sum()
        
        # Additional validation: Check for minimum pulse duration to avoid counting noise
        if pulse_count > 0:
            # Group consecutive high-power readings to validate pulse duration
            high_power_groups = []
            current_group_start = None
            
            for i, (idx, above) in enumerate(above_threshold.items()):
                if above and current_group_start is None:
                    current_group_start = i
                elif not above and current_group_start is not None:
                    group_length = i - current_group_start
                    high_power_groups.append(group_length)
                    current_group_start = None
            
            # If we end on a high-power reading, close the last group
            if current_group_start is not None:
                group_length = len(above_threshold) - current_group_start
                high_power_groups.append(group_length)
            
            # Filter out very short pulses (likely noise)
            valid_pulses = [g for g in high_power_groups if g >= self.MIN_PULSE_DURATION_READINGS]
            pulse_count = len(valid_pulses)
        
        self.logger.info('')
        self.logger.info(f"Power pulse analysis: baseline={baseline_power:.1f}W, threshold={threshold:.1f}W, pulses={pulse_count}")
        self.logger.info(f"Power data range: min={baseline_min:.1f}W, max={baseline_max:.1f}W, range={power_range:.1f}W")
        self.logger.info(f"Active ratio: {active_ratio:.2f}, Total data points: {len(power_data)}, Above threshold: {above_threshold.sum()}")
        if pulse_count > 0 and len(high_power_groups) > 0:
            self.logger.info(f"Pulse durations: {high_power_groups}, Valid pulses (>={self.MIN_PULSE_DURATION_READINGS}): {len(valid_pulses)}")
        
        return pulse_count

        