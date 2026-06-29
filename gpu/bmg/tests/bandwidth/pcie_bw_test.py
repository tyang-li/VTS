# Copyright (C) 2024-2026 Intel Corporation
# Verification Test Suite
# Memory Bandwidth Test

import re
import os
from ..test_base import testBase
from common.common_defs import STATUS_SUCCESS, STATUS_FAILED, ERROR_EXCEPTION

class testClass(testBase):
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)
        self.dginstances = self.getGpuIndexesForMemBenchmarkL0()
        self.testName = 'PCIe_Bandwidth_Test'
        
        # Discover PCIe configuration of GPUs
        self.pcie_gen = None
        self.pcie_width = None
        
        try:
            self._discover_pcie_configuration()
        except Exception as discovery_error:
            self.logger.error(f"PCIe discovery failed with exception: {discovery_error}")
            # Set fallback values
            self.pcie_gen = 4  # Gen4 as reasonable fallback for BMG
            self.pcie_width = 8  # x8 as reasonable fallback for BMG
            self.logger.info(f"Using fallback PCIe config: Gen{self.pcie_gen} x{self.pcie_width}")
    
    def _discover_pcie_configuration(self):
        """Discover PCIe generation and width from GPU bus information."""
        try:
            # Get bus information for GPUs
            bus_info = self.platform_utils.getBusInfo(self.device_manager.gpu_did, verbose=False)
            
            # Extract PCIe info from non-matching devices (PCIe root ports/bridges)
            # These devices have reliable PCIe speed/width info, unlike the GPU endpoints
            gpu_pcie_configs = []
            for device_sbdf, device_info in bus_info.items():
                index = device_info.get('Index', '')
                curr_speed = device_info.get('Curr Link Speed', '')
                curr_width = device_info.get('Curr Link Width', '')
                
                if index == '':  # This is a non-matching device (PCIe root port/bridge)
                    if curr_speed and curr_width:
                        # Parse PCIe generation from speed (e.g., "8 GT/s" -> gen 3)
                        pcie_gen = self._parse_pcie_generation(curr_speed)
                        # Parse PCIe width (e.g., "x16" -> 16)
                        pcie_width = self._parse_pcie_width(curr_width)
                        
                        if pcie_gen and pcie_width:
                            gpu_pcie_configs.append((pcie_gen, pcie_width))
            
            # Validate that all GPUs have the same PCIe configuration
            if gpu_pcie_configs:
                unique_configs = list(set(gpu_pcie_configs))
                
                if len(unique_configs) == 1:
                    # All GPUs have the same configuration
                    self.pcie_gen, self.pcie_width = unique_configs[0]
                else:
                    # Mixed PCIe configurations - issue warning and use most common config
                    self.logger.warning(f"Mixed PCIe configurations detected: {unique_configs}")
                    # Use the most common configuration
                    config_counts = {config: gpu_pcie_configs.count(config) for config in unique_configs}
                    most_common_config = max(config_counts, key=config_counts.get)
                    self.pcie_gen, self.pcie_width = most_common_config
                    self.logger.warning(f"Using most common configuration: Gen{self.pcie_gen} x{self.pcie_width}")
            else:
                # No PCIe info found - use defaults
                self.logger.warning("No PCIe configuration detected, using defaults: Gen5 x8")
                self.pcie_gen = 5
                self.pcie_width = 8
                
        except Exception as e:
            self.logger.error(f"Failed to discover PCIe configuration: {e}")
            self.logger.error(f"Exception details: {type(e).__name__}: {str(e)}")
            # Use defaults on error
            self.logger.warning(f"Setting fallback defaults: Gen4 x8 (reasonable for BMG)")
            self.pcie_gen = 4  # More reasonable default for BMG than Gen5
            self.pcie_width = 8
            
        # Final validation - ensure we have valid values
        if self.pcie_gen is None or self.pcie_width is None:
            self.logger.warning(f"PCIe values not set properly (gen={self.pcie_gen}, width={self.pcie_width}), using Gen4 x8 fallback")
            self.pcie_gen = 4
            self.pcie_width = 8
    
    def _parse_pcie_generation(self, speed_str):
        """Parse PCIe generation from speed string (e.g., '8 GT/s' -> 3, '16.0 GT/s PCIe' -> 4, '16.0 GT/s pcie' -> 4)."""
        speed_to_gen = {
            2.5: 1,
            5.0: 2, 
            8.0: 3,
            16.0: 4,
            32.0: 5,
            64.0: 6
        }
        
        # Extract numeric part from speed string (handle both "8 GT/s" and "16.0 GT/s PCIe" formats)
        match = re.search(r'([0-9.]+)\s*GT/s', speed_str, re.IGNORECASE)
        if match:
            try:
                speed = float(match.group(1))
                return speed_to_gen.get(speed, 4)  # Default to Gen4 if unknown
            except ValueError:
                return 4
        return 4
    
    def _parse_pcie_width(self, width_str):
        """Parse PCIe width from width string (e.g., 'x16' -> 16, 'x8' -> 8)."""
        match = re.search(r'x?([0-9]+)', width_str, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return 16  # Default to x16 if unknown

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
        self.add_parser_argument('-mode', 'Execution Mode', str, 'all', 'mode', choices=['serial', 'parallel', 'all']) #all means do it serially and parallely 
        self.add_parser_argument('-dir', 'Traffic Direction', str, 'all', 'dir', choices=['h2d', 'd2h', 'bidirectional', 'all']) #all means do all directions (d2h, h2d and 'bidirectional')
        self.add_parser_argument('-engine', 'Traffic Engine', str, 'all', 'engine', choices=['copy', 'compute', 'all']) #all means do all engines (copy, compute)
        self.add_parser_argument('-tool', 'Bandwidth Test Tool', str, 'all', 'tool', choices=['ze_bandwidth', 'memory_benchmark_l0', 'all']) #all means use both tools
        self.add_parser_argument('-iterations', 'Number of test iterations', int, 500, 'iterations') #number of iterations for bandwidth test
        self.add_parser_argument('-size', 'Buffer size in bytes', int, 268435456, 'size') #buffer size for bandwidth test (default 256MB)

    def _parse_instance_spec(self, inst_spec):
        """Parse and validate zero-based GPU IDs for the bandwidth test."""
        available_gpu_count = len(self.dginstances) if self.dginstances else None
        return self.resolve_selected_gpu_ids(inst_spec, available_gpu_count=available_gpu_count)
    
    def prepareGpuCommands(self):
        self.gpuCommands = []

        try:
            requested_instances = self._parse_instance_spec(self.parsed_args.inst)
        except ValueError as parse_error:
            self.logger.error(f"Invalid -inst argument: {parse_error}")
            return STATUS_FAILED
        
        # Set execution directory to tools folder at root level - find project root
        current_dir = os.path.dirname(__file__)
        project_root = current_dir
        while not os.path.exists(os.path.join(project_root, 'tools')) and os.path.dirname(project_root) != project_root:
            project_root = os.path.dirname(project_root)
        self.execution_dir = os.path.join(project_root, 'tools')
        
        # Validate required tools exist and make script executable
        runbw_script = os.path.join(self.execution_dir, 'runBwTest.sh')
        if not os.path.exists(runbw_script):
            self.logger.error(f"Required script not found: {runbw_script}")
            return STATUS_FAILED
            
        # Ensure runBwTest.sh is executable
        if not self.utils.make_script_executable(runbw_script):
            self.logger.warning(f"Could not make runBwTest.sh executable at {runbw_script}")
        
        # Ensure bandwidth test binaries are executable
        ze_bandwidth_path = os.path.join(self.execution_dir, 'ze_bandwidth')
        memory_benchmark_path = os.path.join(self.execution_dir, 'memory_benchmark_l0')
        
        if os.path.exists(ze_bandwidth_path):
            if not self.utils.make_script_executable(ze_bandwidth_path):
                self.logger.warning(f"Could not make ze_bandwidth executable at {ze_bandwidth_path}")
        else:
            self.logger.warning(f"ze_bandwidth binary not found at {ze_bandwidth_path}")
            
        if os.path.exists(memory_benchmark_path):
            if not self.utils.make_script_executable(memory_benchmark_path):
                self.logger.warning(f"Could not make memory_benchmark_l0 executable at {memory_benchmark_path}")
        else:
            self.logger.warning(f"memory_benchmark_l0 binary not found at {memory_benchmark_path}")
        
        # Define specific test combinations to run by default
        default_test_combinations = [
            ('ze_bandwidth', 'd2h', 'copy', 'serial'),
            ('ze_bandwidth', 'd2h', 'compute', 'serial'),
            ('ze_bandwidth', 'h2d', 'copy', 'serial'),
            ('ze_bandwidth', 'h2d', 'compute', 'serial'),
            #('memory_benchmark_l0', 'bidirectional', 'compute', 'serial'), # Disabled due to known issues
            ('ze_bandwidth', 'd2h', 'copy', 'parallel'),
            ('ze_bandwidth', 'd2h', 'compute', 'parallel'),
            ('ze_bandwidth', 'h2d', 'copy', 'parallel'),
            ('ze_bandwidth', 'h2d', 'compute', 'parallel')
            #('memory_benchmark_l0', 'bidirectional', 'compute', 'parallel') # Disabled due to known issues
        ]
        
        # Check if we should use default combinations or user-specified parameters
        use_default_combinations = (self.parsed_args.tool == 'all' and 
                                  self.parsed_args.mode == 'all' and 
                                  self.parsed_args.dir == 'all' and 
                                  self.parsed_args.engine == 'all')
        
        if use_default_combinations:
            # Use predefined test combinations
            test_combinations = default_test_combinations
        else:
            # Generate combinations based on user-specified parameters
            tools = ['ze_bandwidth', 'memory_benchmark_l0'] if self.parsed_args.tool == 'all' else [self.parsed_args.tool]
            modes = ['serial', 'parallel'] if self.parsed_args.mode == 'all' else [self.parsed_args.mode]
            directions = ['h2d', 'd2h', 'bidirectional'] if self.parsed_args.dir == 'all' else [self.parsed_args.dir]
            engines = ['copy', 'compute'] if self.parsed_args.engine == 'all' else [self.parsed_args.engine]
            
            # Generate all combinations from user parameters
            test_combinations = []
            for mode in modes:
                for tool in tools:  
                    for direction in directions:
                        for engine in engines:
                            test_combinations.append((tool, direction, engine, mode))
        
        # Generate commands for each test combination
        for tool, direction, engine, mode in test_combinations:
            # Handle GPU specification based on mode and selected instances.
            if requested_instances is None:  # All GPUs
                if mode == 'serial':
                    # Serial mode: run on each GPU individually
                    for gpu_id in self.dginstances.keys():
                        command = f'bash runBwTest.sh {tool} {direction} {engine} {gpu_id} {self.parsed_args.iterations} {self.parsed_args.size} {mode}'
                        self.gpuCommands.append(command)
                else:  # parallel mode
                    # Parallel mode: run on all GPUs at once
                    command = f'bash runBwTest.sh {tool} {direction} {engine} all {self.parsed_args.iterations} {self.parsed_args.size} {mode}'
                    self.gpuCommands.append(command)
            else:
                # Specific GPU list/range/single instance specified - validate when possible.
                if not self.dginstances:
                    self.logger.warning(
                        f"No GPU instances detected, proceeding with requested GPUs: {requested_instances}"
                    )
                    if mode == 'serial':
                        for gpu_id in requested_instances:
                            command = f'bash runBwTest.sh {tool} {direction} {engine} {gpu_id} {self.parsed_args.iterations} {self.parsed_args.size} {mode}'
                            self.gpuCommands.append(command)
                    else:
                        gpu_spec = ','.join(str(gpu_id) for gpu_id in requested_instances)
                        command = f'bash runBwTest.sh {tool} {direction} {engine} {gpu_spec} {self.parsed_args.iterations} {self.parsed_args.size} {mode}'
                        self.gpuCommands.append(command)
                else:
                    available_gpus = set(self.dginstances.keys())
                    valid_instances = [gpu_id for gpu_id in requested_instances if gpu_id in available_gpus]
                    invalid_instances = [gpu_id for gpu_id in requested_instances if gpu_id not in available_gpus]

                    if invalid_instances:
                        self.logger.warning(
                            f"Ignoring unavailable GPU instances {invalid_instances}; available GPUs: {sorted(available_gpus)}"
                        )

                    if not valid_instances:
                        self.logger.error(
                            f"No valid GPU instances in request {requested_instances}; available GPUs: {sorted(available_gpus)}"
                        )
                        continue

                    if mode == 'serial':
                        for gpu_id in valid_instances:
                            command = f'bash runBwTest.sh {tool} {direction} {engine} {gpu_id} {self.parsed_args.iterations} {self.parsed_args.size} {mode}'
                            self.gpuCommands.append(command)
                    else:
                        gpu_spec = ','.join(str(gpu_id) for gpu_id in valid_instances)
                        command = f'bash runBwTest.sh {tool} {direction} {engine} {gpu_spec} {self.parsed_args.iterations} {self.parsed_args.size} {mode}'
                        self.gpuCommands.append(command)

        # Final validation
        if not self.gpuCommands:
            self.logger.error("No valid GPU commands generated. Check GPU detection and tool availability.")
            return STATUS_FAILED

        return STATUS_SUCCESS

    def get_bandwidth_threshold(self, tool, direction, engine, mode, pcie_gen=None, pcie_width=None):
        """Get the bandwidth threshold for a specific test combination using dynamic calculation.
        
        Args:
            tool: Bandwidth test tool (ze_bandwidth, memory_benchmark_l0)
            direction: Traffic direction (h2d, d2h, bidirectional)
            engine: Traffic engine (copy, compute)
            mode: Execution mode (serial, parallel)
            pcie_gen: PCIe generation (1-6, uses detected value if None)
            pcie_width: PCIe width in lanes (1,2,4,8,16, uses detected value if None)
        
        Returns:
            float: Dynamically calculated bandwidth threshold in GBPS
        """
        try:
            # Use class-level discovered PCIe configuration (from bridge devices)
            # Don't re-detect from GPU devices due to HW bug causing Gen1 x1 false readings
            if pcie_gen is None:
                pcie_gen = self.pcie_gen  # Use bridge-discovered value from __init__
            if pcie_width is None:
                pcie_width = self.pcie_width  # Use bridge-discovered value from __init__
            
            # Convert to proper format for getPCIeBWPassFail
            gen_to_speed = {
                1: '2.5 GT/s',
                2: '5.0 GT/s', 
                3: '8.0 GT/s',
                4: '16.0 GT/s',
                5: '32.0 GT/s',
                6: '64.0 GT/s'
            }
            pcie_speed = gen_to_speed.get(pcie_gen, '16.0 GT/s')
            pcie_width_str = f'x{pcie_width}'
            
            # Calculate threshold using platform_utils
            threshold = self.platform_utils.getPCIeBWPassFail(
                pcie_speed=pcie_speed,
                pcie_width=pcie_width_str,
                direction=direction,
                engine=engine,
                mode=mode,
                pcie_bandwidth_factors=self.platform_defs_instance.pcie_bandwidth_factors,
                pcie_bandwidth_overhead_factor=self.platform_defs_instance.pcie_bandwidth_overhead_factor
            )
            
            return threshold if threshold is not None else 10.0
            
        except Exception as e:
            self.logger.error(f"Error calculating bandwidth threshold: {e}")
            return 10.0  # Default fallback threshold

    def parseResults(self):
        self.overall_test_result = 'FAIL'
        passed_tests = 0
        total_tests = 0
        
        # Dictionary to store detailed test results
        test_results_dict = {}
        result_counter = 1

        self.logger.subheader('Results Parsing...')

        # Check if we need to split combined parallel results
        results_to_process = []
        
        for result in self.gpu_test_results:
            # Check if this is a combined parallel result with multiple device sections
            if '==================== GPU' in result and 'RESULTS START' in result:
                # Split combined result into individual device results
                device_results = self.split_combined_result(result)
                results_to_process.extend(device_results)
            elif result.count('DEVICE:') > 1:
                # Alternative split using DEVICE: markers
                device_results = self.split_by_device_markers(result)
                results_to_process.extend(device_results)
            else:
                # Single result, process as-is
                results_to_process.append(result)
        
        # Parse each individual test result
        for i, result in enumerate(results_to_process):
            # Extract test parameters from command output
            test_info = self.extract_test_info(result)
            
            if not test_info:
                self.logger.warning(f"Could not extract test info from result {i+1}")
                continue
            
            # Parse bandwidth based on tool type  
            bandwidth_result = self.parse_bandwidth_by_tool(result, test_info)
            
            # Get the specific threshold for this test combination
            expected_bw = self.get_bandwidth_threshold(
                test_info['tool'], 
                test_info['direction'], 
                test_info['engine'], 
                test_info.get('mode', 'serial')
            )
            
            # Handle different return types from parse_bandwidth_by_tool
            if bandwidth_result is None:
                # No results found - create one failed entry
                total_tests += 1
                test_results_dict[f"Test_{result_counter}"] = {
                    'Mode': test_info.get('mode', 'N/A'),
                    'Tool': test_info['tool'],
                    'Direction': test_info['direction'],
                    'Engine': test_info['engine'],
                    'GPU': test_info.get('gpu', 'N/A'),
                    'Measured_BW_GBPS': "N/A",
                    'Expected_BW_GBPS': f"{expected_bw:.3f}",
                    'Result': 'FAIL'
                }
                result_counter += 1
            elif isinstance(bandwidth_result, dict):
                # Multiple devices found - create separate row for each device (both ze_bandwidth and memory_benchmark_l0)
                for device_id, bandwidth_gbps in bandwidth_result.items():
                    total_tests += 1
                    
                    # Determine pass/fail for this device
                    is_pass = bandwidth_gbps >= expected_bw
                    if is_pass:
                        passed_tests += 1
                    
                    # Create table row for this device
                    test_results_dict[f"Test_{result_counter}"] = {
                        'Mode': test_info.get('mode', 'N/A'),
                        'Tool': test_info['tool'],
                        'Direction': test_info['direction'],
                        'Engine': test_info['engine'],
                        'GPU': str(device_id),
                        'Measured_BW_GBPS': f"{bandwidth_gbps:.3f}",
                        'Expected_BW_GBPS': f"{expected_bw:.3f}",
                        'Result': 'PASS' if is_pass else 'FAIL'
                    }
                    result_counter += 1
            else:
                # Single bandwidth value (memory_benchmark_l0 tools)
                total_tests += 1
                bandwidth_gbps = float(bandwidth_result)
                
                # Determine pass/fail
                is_pass = bandwidth_gbps >= expected_bw
                if is_pass:
                    passed_tests += 1
                
                # Store result in dictionary for table display
                test_results_dict[f"Test_{result_counter}"] = {
                    'Mode': test_info.get('mode', 'N/A'),
                    'Tool': test_info['tool'],
                    'Direction': test_info['direction'],
                    'Engine': test_info['engine'],
                    'GPU': test_info.get('gpu', 'N/A'),
                    'Measured_BW_GBPS': f"{bandwidth_gbps:.3f}",
                    'Expected_BW_GBPS': f"{expected_bw:.3f}",
                    'Result': 'PASS' if is_pass else 'FAIL'
                }
                result_counter += 1
        
        # Display results table
        if test_results_dict:
            self.logger.info("Detailed Test Results:")
            self.logger.printTableFromDict(test_results_dict)
        else:
            self.logger.error("No test results parsed successfully")
        
        # Determine overall result
        if total_tests > 0 and passed_tests == total_tests:
            self.overall_test_result = 'PASS'
            self.logger.pass_msg(f'OVERALL TEST RESULT : PASS ({passed_tests}/{total_tests} tests passed)')
            return STATUS_SUCCESS
        else:
            self.overall_test_result = 'FAIL'
            self.logger.fail_msg(f'OVERALL TEST RESULT : FAIL ({passed_tests}/{total_tests} tests passed)')
            return STATUS_FAILED

    def extract_test_info(self, result):
        """Extract test parameters from command output"""
        # First try to parse from "Bandwidth Test Configuration" section
        config_pattern = r'Bandwidth Test Configuration:\s+Tool:\s+(\w+)\s+Direction:\s+(\w+)\s+Engine:\s+(\w+)\s+GPUs:\s+(\w+|\d+|[\d\s]+)\s+Mode:\s+(\w+)'
        match = re.search(config_pattern, result, re.MULTILINE | re.DOTALL)
        
        if match:
            gpu_info = match.group(4).strip()
            # If GPU info contains multiple devices (e.g., "0 1 2 3"), try to extract specific device from result
            if ' ' in gpu_info:
                # Look for DEVICE: marker in result to get specific GPU ID
                device_marker_match = re.search(r'DEVICE:\s*(\d+)', result)
                if device_marker_match:
                    gpu_info = device_marker_match.group(1)
                else:
                    # Fallback to first GPU if no device marker found
                    gpu_info = gpu_info.split()[0]
            
            return {
                'tool': match.group(1),
                'direction': match.group(2), 
                'engine': match.group(3),
                'gpu': gpu_info,
                'mode': match.group(5)
            }
        
        # Alternative pattern for configuration section
        lines = result.split('\n')
        tool = direction = engine = gpu = mode = None
        
        for line in lines:
            line = line.strip()
            if line.startswith('Tool:'):
                tool = line.split(':')[1].strip()
            elif line.startswith('Direction:'):
                direction = line.split(':')[1].strip()
            elif line.startswith('Engine:'):
                engine = line.split(':')[1].strip()
            elif line.startswith('GPUs:'):
                gpu = line.split(':')[1].strip()
            elif line.startswith('Mode:'):
                mode = line.split(':')[1].strip()
        
        if tool and direction and engine and gpu and mode:
            return {
                'tool': tool,
                'direction': direction, 
                'engine': engine,
                'gpu': gpu,
                'mode': mode
            }
        
        # Fallback: Try original command patterns (now with mode parameter)
        patterns = [
            r'Command:\s+.*?runBwTest\.sh\s+(\w+)\s+(\w+)\s+(\w+)\s+(\w+|\d+)\s+\d+\s+\d+\s+(\w+)',
            r'runBwTest\.sh\s+(\w+)\s+(\w+)\s+(\w+)\s+(\w+|\d+)\s+\d+\s+\d+\s+(\w+)',
            r'\.\/runBwTest\.sh\s+(\w+)\s+(\w+)\s+(\w+)\s+(\w+|\d+)\s+\d+\s+\d+\s+(\w+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, result, re.MULTILINE | re.DOTALL)
            if match:
                return {
                    'tool': match.group(1),
                    'direction': match.group(2), 
                    'engine': match.group(3),
                    'gpu': match.group(4),
                    'mode': match.group(5)
                }
        
        # Enhanced fallback: Parse individual device results from split combined output
        # Look for "Running [tool] on GPU [id] (direction=[dir], engine=[eng]" pattern
        device_run_pattern = r'Running\s+(\w+)\s+on\s+GPU\s+(\d+)\s+\(.*?direction=(\w+).*?engine=(\w+)'
        match = re.search(device_run_pattern, result, re.MULTILINE)
        if match:
            tool = match.group(1)
            gpu_id = match.group(2)
            direction = match.group(3) 
            engine = match.group(4)
            return {
                'tool': tool,
                'direction': direction,
                'engine': engine, 
                'gpu': gpu_id,
                'mode': 'serial'  # Default to serial if not specified
            }
        
        # Alternative pattern for split results: Look for tool-specific patterns
        if 'ze_bandwidth' in result:
            # Look for ze_bandwidth command pattern
            ze_cmd_pattern = r'ze_bandwidth\s+-g\s+(\d+)\s+-d\s+(\d+).*?-t\s+(\w+)'
            match = re.search(ze_cmd_pattern, result)
            if match:
                engine_group = match.group(1)
                gpu_id = match.group(2)
                direction = match.group(3)
                engine = 'copy' if engine_group == '1' else 'compute'
                return {
                    'tool': 'ze_bandwidth',
                    'direction': direction,
                    'engine': engine,
                    'gpu': gpu_id,
                    'mode': 'serial'  # Default to serial if not specified
                }
        
        # Look for DEVICE: markers in split results
        device_marker_pattern = r'DEVICE:\s*(\d+)'
        device_match = re.search(device_marker_pattern, result)
        if device_match:
            gpu_id = device_match.group(1)
            # Try to infer other parameters from context
            if 'ze_bandwidth' in result:
                direction = 'h2d' if 'H2D' in result else 'd2h' if 'D2H' in result else None
                engine = 'copy' if 'Group 1 (copy)' in result else 'compute' if 'Group 0 (compute)' in result else None
                if direction and engine:
                    return {
                        'tool': 'ze_bandwidth',
                        'direction': direction,
                        'engine': engine,
                        'gpu': gpu_id,
                        'mode': 'parallel'
                    }
            elif 'memory_benchmark_l0' in result:
                direction = 'bidirectional'  # memory_benchmark_l0 is always bidirectional
                engine = 'compute' if 'UsmConcurrentCopy' in result else 'copy'
                # If we're in DEVICE marker section, it means this is a split parallel result
                mode = 'parallel'
                return {
                    'tool': 'memory_benchmark_l0',
                    'direction': direction,
                    'engine': engine,
                    'gpu': gpu_id,
                    'mode': mode
                }
        
        # Last resort: Parse actual tool commands
        if 'memory_benchmark_l0' in result:
            # Extract device ID from --l0DeviceIndex parameter
            device_match = re.search(r'--l0DeviceIndex=(\d+)', result)
            gpu_id = device_match.group(1) if device_match else '0'
            
            # Determine direction from blitter parameters
            direction = 'h2d' if '--h2dBlitters=1' in result else 'd2h' if '--d2hBlitters=1' in result else 'bidirectional'
            
            # Determine engine from test type
            engine = 'compute' if 'UsmConcurrentCopy' in result else 'copy'
            
            # Detect mode from command pattern or context  
            mode = 'parallel' if 'all' in result or 'parallel' in result else 'serial'
            
            return {
                'tool': 'memory_benchmark_l0',
                'direction': direction,
                'engine': engine,
                'gpu': gpu_id,
                'mode': mode
            }
            
        return None
        
    def parse_bandwidth_by_tool(self, result, test_info):
        """Parse bandwidth based on tool type and test configuration"""
        tool = test_info['tool']
        direction = test_info['direction']
        engine = test_info['engine']
        gpu = test_info['gpu']
        
        if tool == 'ze_bandwidth':
            device_bw_dict = self.parse_ze_bandwidth(result)
            if device_bw_dict is None:
                return None
            
            # For ze_bandwidth, return the device dictionary to allow multiple table rows
            return device_bw_dict
                
        elif tool == 'memory_benchmark_l0':
            if engine == 'copy':
                device_bw_dict = self.parse_memory_benchmark_copy(result, direction, gpu)
            elif engine == 'compute':
                device_bw_dict = self.parse_memory_benchmark_compute(result, gpu)
            else:
                device_bw_dict = None
            
            # For memory_benchmark_l0, return the device dictionary (consistent with ze_bandwidth)
            return device_bw_dict
        
        return None
        
    def parse_ze_bandwidth(self, result):
        """Parse ze_bandwidth output and return dictionary of device:bandwidth mappings"""
        device_bw_dict = {}
        
        # Pattern to match: [Device 3  268435456]:  BW = 11.023206 GBPS  Latency =  24351.85 usec
        device_pattern = r'\[Device\s+(\d+)\s+\d+\]:\s+BW\s*=\s*(\d+(?:\.\d+)?)\s*GBPS'
        matches = re.findall(device_pattern, result, re.IGNORECASE)
        
        for device_id, bandwidth in matches:
            device_bw_dict[int(device_id)] = float(bandwidth)
        
        # If no device-specific patterns found, try the [Total] pattern for single device scenarios
        if not device_bw_dict:
            total_pattern = r'\[Total\s+\d+\]:\s+BW\s*=\s*(\d+(?:\.\d+)?)\s*GBPS'
            total_matches = re.findall(total_pattern, result, re.IGNORECASE)
            if total_matches:
                # Assume single device (device 0) for total pattern
                device_bw_dict[0] = float(total_matches[-1])
        
        return device_bw_dict if device_bw_dict else None
    
    def split_combined_result(self, combined_result):
        """Split combined parallel result into individual device results"""
        device_results = []
        
        # Extract the configuration section from the header
        config_section = ""
        config_match = re.search(r'Bandwidth Test Configuration:[\s\S]*?={40}', combined_result)
        if config_match:
            config_section = config_match.group(0)
        
        # Split by device result boundaries
        sections = re.split(r'==================== GPU (\d+) RESULTS START ====================', combined_result)
        
        # The first section contains header information, skip it
        if len(sections) > 1:
            for i in range(1, len(sections), 2):  # Every other section starting from 1
                if i + 1 < len(sections):
                    device_id = sections[i]
                    device_content = sections[i + 1]
                    
                    # Extract content until the END marker or next GPU section
                    end_marker = f"===================== GPU {device_id} RESULTS END ====================="
                    if end_marker in device_content:
                        device_content = device_content.split(end_marker)[0]
                    
                    # Reconstruct individual result with device info and configuration
                    individual_result = f"{config_section}\nDEVICE: {device_id}\n{device_content}"
                    device_results.append(individual_result)
        
        return device_results if device_results else [combined_result]
    
    def split_by_device_markers(self, combined_result):
        """Split combined result using DEVICE: markers"""
        device_results = []
        
        # Extract the configuration section from the header
        config_section = ""
        config_match = re.search(r'Bandwidth Test Configuration:[\s\S]*?={40}', combined_result)
        if config_match:
            config_section = config_match.group(0)
        
        # Split by DEVICE: markers
        sections = re.split(r'DEVICE:\s*(\d+)', combined_result)
        
        # The first section contains header information, skip it
        if len(sections) > 2:
            for i in range(1, len(sections), 2):  # Every other section starting from 1
                if i + 1 < len(sections):
                    device_id = sections[i]
                    device_content = sections[i + 1]
                    
                    # Reconstruct individual result with device info and configuration
                    individual_result = f"{config_section}\nDEVICE: {device_id}\n{device_content}"
                    device_results.append(individual_result)
        
        return device_results if device_results else [combined_result]
        
    def parse_memory_benchmark_copy(self, result, direction, device_id_from_test_info):
        """Parse memory_benchmark_l0 copy engine output and return device dictionary"""
        # Check if this is a UsmCopyConcurrentMultipleBlits test
        if 'UsmCopyConcurrentMultipleBlits' not in result:
            return None
        
        # Use device ID from test_info if available, otherwise extract from result
        if device_id_from_test_info is not None and str(device_id_from_test_info).isdigit():
            device_id = int(device_id_from_test_info)
        else:
            # Extract device ID from the result (look for --l0DeviceIndex=X pattern)
            device_match = re.search(r'--l0DeviceIndex=(\d+)', result)
            device_id = int(device_match.group(1)) if device_match else 0
            
        if direction == 'h2d':
            # Look for [GPU] BCS-h2d [GB/s] line (bandwidth is first number on that line)
            pattern = r'\s+(\d+(?:\.\d+)?)\s+[\d\.]+\s+[\d\.]+%\s+[\d\.]+\s+[\d\.]+\s+\[GPU\]\s+BCS-h2d\s+\[GB/s\]'
        elif direction == 'd2h':
            # Look for [GPU] BCS-d2h [GB/s] line
            pattern = r'\s+(\d+(?:\.\d+)?)\s+[\d\.]+\s+[\d\.]+%\s+[\d\.]+\s+[\d\.]+\s+\[GPU\]\s+BCS-d2h\s+\[GB/s\]'
        elif direction == 'bidirectional':
            # For bidirectional, get the total GPU bandwidth
            pattern = r'\s+(\d+(?:\.\d+)?)\s+[\d\.]+\s+[\d\.]+%\s+[\d\.]+\s+[\d\.]+\s+\[GPU\]\s+Total\s+\(Gpu\)\s+\[GB/s\]'
        else:
            return None
        
        matches = re.findall(pattern, result, re.MULTILINE)
        
        if matches:
            # Return device dictionary format
            return {device_id: float(matches[0])}
        
        return None
        
    def parse_memory_benchmark_compute(self, result, device_id_from_test_info):
        """Parse memory_benchmark_l0 compute engine output and return device dictionary"""
        # Check if this is a UsmConcurrentCopy test
        if 'UsmConcurrentCopy' not in result:
            return None
        
        # Use device ID from test_info if available, otherwise extract from result
        if device_id_from_test_info is not None and str(device_id_from_test_info).isdigit():
            device_id = int(device_id_from_test_info)
        else:
            # Extract device ID from the result (look for --l0DeviceIndex=X pattern)
            device_match = re.search(r'--l0DeviceIndex=(\d+)', result)
            device_id = int(device_match.group(1)) if device_match else 0
            
        # Look for [CPU] [GB/s] line (bandwidth is first number on that line)
        pattern = r'\s+(\d+(?:\.\d+)?)\s+[\d\.]+\s+[\d\.]+%\s+[\d\.]+\s+[\d\.]+\s+\[CPU\]\s+\[GB/s\]'
        
        matches = re.findall(pattern, result, re.MULTILINE)
        
        if matches:
            # Return device dictionary format
            return {device_id: float(matches[0])}
        
        return None

    def getGpuIndexesForMemBenchmarkL0(self):        
        """
        Parse memory_benchmark_l0 --hwInfo output to get GPU device mappings.
        
        Returns:
            dict: Dictionary where keys are GPU identifiers (0, 1, 2, ...) and values are 
                    dictionaries containing 'l0DriverIndex' and 'l0DeviceIndex' keys.
                    
        Example return:
            {
                0: {'l0DriverIndex': 0, 'l0DeviceIndex': 0},
                1: {'l0DriverIndex': 0, 'l0DeviceIndex': 1},
                2: {'l0DriverIndex': 0, 'l0DeviceIndex': 2},
                3: {'l0DriverIndex': 0, 'l0DeviceIndex': 3}
            }
        """
        gpu_mappings = {}
        
        try:
            # Find project root and construct path to memory_benchmark_l0
            current_dir = os.path.dirname(__file__)
            project_root = current_dir
            while not os.path.exists(os.path.join(project_root, 'tools')) and os.path.dirname(project_root) != project_root:
                project_root = os.path.dirname(project_root)
            
            tools_dir = os.path.join(project_root, 'tools')
            memory_benchmark_tool = os.path.join(tools_dir, 'memory_benchmark_l0')
            
            # Check if tool exists
            if not os.path.exists(memory_benchmark_tool):
                self.logger.warning(f"memory_benchmark_l0 tool not found at {memory_benchmark_tool}")
                # Return default GPU mapping for GPU 0 if tool is missing
                return {0: {'l0DriverIndex': 0, 'l0DeviceIndex': 0}}
            
            # Run the command to get hardware info
            command = f'{memory_benchmark_tool} --hwInfo'
            output = self.utils.run_command_blocking(command)
            
            if not output:
                self.logger.error("memory_benchmark_l0 command returned no output")
                return gpu_mappings
            
            gpu_counter = 0
            
            # Parse each line of the output
            for line in output.split('\n'):
                line = line.strip()
                
                # Look for device lines with the pattern containing l0DriverIndex and l0DeviceIndex
                if 'select this device with --l0DriverIndex=' in line:
                    # Extract driver and device indices using regex
                    driver_match = re.search(r'--l0DriverIndex=(\d+)', line)
                    device_match = re.search(r'--l0DeviceIndex=(\d+)', line)
                    
                    if driver_match and device_match:
                        driver_index = int(driver_match.group(1))
                        device_index = int(device_match.group(1))
                        
                        gpu_mappings[gpu_counter] = {
                            'l0DriverIndex': driver_index,
                            'l0DeviceIndex': device_index
                        }
                        gpu_counter += 1
            
            # If no GPUs found, provide default mapping
            if not gpu_mappings:
                self.logger.warning("No GPU mappings found, using default GPU 0")
                gpu_mappings = {0: {'l0DriverIndex': 0, 'l0DeviceIndex': 0}}
            
            return gpu_mappings
            
        except Exception as e:
            self.logger.error(f"Error running memory_benchmark_l0: {str(e)}")
            # Return default GPU mapping on error
            return {0: {'l0DriverIndex': 0, 'l0DeviceIndex': 0}}    