# Copyright (C) 2024-2026 Intel Corporation
from ..test_base import testBase
import os

class testClass(testBase):
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)
        self.testName = 'PCIe_Reset_Test'

    def add_arguments(self):
        super().add_arguments()
        # Add all arguments using the helper function
        self.add_parser_argument('-rt', 'reset type', str, 'testonly', 'rt', choices=['flr', 'warm', 'cold', 'soft', 'sbr', 'custom', 'linkdisable', 'linkchange', 'retrain', 'testonly', 'clean'])
        self.add_parser_argument('-iterations', 'Number of Iteration', int, 1, 'iterations')
        self.add_parser_argument('-custom-script', 'Custom script to run', str, '', 'custom_script')
        # Default pcie_downgrade to True for Reset tests; explicit CLI/JSON values still take precedence
        self.add_parser_argument('-pcie_downgrade', 'Disable PCIe downgrade before test execution (True/False)', lambda x: x.lower() == 'true', True, 'pcie_downgrade')

    def modifyTestName(self):
        self.testName += f'_{self.parsed_args.rt}'
        self.parsed_args.mt = 'None'  # Ensure monitor type is None for reset tests

    def prepareGpuCommands(self):
        self.gpuCommands = []
        envVars = f''
       
        self.execution_dir = os.path.dirname(__file__)
        script_path = os.path.join(self.execution_dir, 'systemReboot.sh')
        # Ensure systemReboot.sh is executable
        if not self.utils.make_script_executable(script_path):
            self.logger.warning(f"Could not make systemReboot.sh executable at {script_path}")
        
        # Ensure reset content binaries are executable
        content_dir = os.path.join(self.execution_dir, 'content')
        binaries_to_check = ['ze_peak', 'LTSSMtool']
        
        for binary_name in binaries_to_check:
            binary_path = os.path.join(content_dir, binary_name)
            if os.path.exists(binary_path):
                if not self.utils.make_script_executable(binary_path):
                    self.logger.warning(f"Could not make {binary_name} executable at {binary_path}")
            else:
                self.logger.warning(f"Reset content binary not found: {binary_path}")
        
        # Create commands with explicit paths for better compatibility
        script_name = os.path.basename(script_path)
        
        # Execute reset script with standard bash execution
        
        # Execute reset commands directly
        self.gpuCommands.append(f'{envVars}bash {script_name} clean')
        
        # Build the main command based on reset type
        if self.parsed_args.rt in ['clean', 'testonly']:
            # For clean and testonly, no iterations or custom script needed
            self.gpuCommands.append(f'{envVars}bash {script_name} {self.parsed_args.rt}')
        elif self.parsed_args.rt == 'custom':
            # For custom reset type, include iterations and custom script
            cmd_parts = [f'{envVars}bash', script_name, str(self.parsed_args.iterations), self.parsed_args.rt]
            if self.parsed_args.custom_script:
                cmd_parts.append(self.parsed_args.custom_script)
            self.gpuCommands.append(' '.join(cmd_parts))
        else:
            # For all other reset types, include iterations but no custom script
            self.gpuCommands.append(f'{envVars}bash {script_name} {self.parsed_args.iterations} {self.parsed_args.rt}')
        
    def parseResults(self):
        self.logger.subheader('Results Parsing...')

        # Check return codes: PASS only if all return codes are 0
        if self.gpu_return_codes and all(code == 0 for code in self.gpu_return_codes):
            self.overall_test_result = 'PASS'
            self.logger.pass_msg(f'OVERALL TEST RESULT : {self.overall_test_result}')
        else:
            self.overall_test_result = 'FAIL'
            self.logger.fail_msg(f'OVERALL TEST RESULT : {self.overall_test_result}')
            
        # Clean up residual log files from reset folder after test completion
        self._cleanup_reset_logs()
        
    def _cleanup_reset_logs(self):
        """Clean up residual log files from reset folder that should have been moved to logs folder."""
        try:
            import glob
            
            # Look for reset log files in the reset test directory that are specific to this reset type
            reset_log_patterns = [
                os.path.join(self.execution_dir, f"{self.parsed_args.rt}_Reset*.log"),
                os.path.join(self.execution_dir, f"{self.parsed_args.rt}Reset*.log"),
            ]
            
            files_removed = 0
            for pattern in reset_log_patterns:
                matching_files = glob.glob(pattern)
                for log_file in matching_files:
                    try:
                        # Only remove if it's a file (not directory) and exists
                        if os.path.isfile(log_file):
                            os.remove(log_file)
                            files_removed += 1
                    except OSError as e:
                        self.logger.warning(f"Could not remove residual log file {log_file}: {e}")
                        
            if files_removed == 0:
                self.logger.debug("No residual reset log files found to clean up")
                
        except Exception as e:
            self.logger.warning(f"Reset log cleanup encountered error: {e}")

