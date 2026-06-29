# Copyright (C) 2024-2026 Intel Corporation
# Verification Test Suite
# Memory Bandwidth Test

import re
from ..test_base import testBase

class testClass(testBase):
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)
        self.testName = 'Memory_Bandwidth_Test'
    
    def add_arguments(self):
        super().add_arguments()
        
        # Add all arguments using the helper function
        self.add_parser_argument('-inst', 'GPU Device instance', int, -1, 'inst')
    
    def prepareGpuCommands(self):
        self.gpuCommands = []
        envVars = f''
        self.gpuCommands.append(f'{envVars}xpu-smi diag -d {self.parsed_args.inst} --singletest 3')
        self.execution_dir = '.'

    def parseResults(self):
        self.overall_test_result = 'FAIL'

        self.logger.subheader('Results Parsing...')

        # Pattern to match the Performance Memory Bandwidth result line in table format
        # Looks for | Performance Memory Bandwidth | Result: Pass/Fail |
        bandwidth_result_pattern = r'\|\s*Performance Memory Bandwidth\s*\|\s*Result:\s*(Pass|Fail)'

        for result in self.gpu_test_results:
            # Check for the new table format first
            bandwidth_match = re.search(bandwidth_result_pattern, result, re.IGNORECASE)
            if bandwidth_match:
                result_status = bandwidth_match.group(1).upper()
                if result_status == 'PASS':
                    self.overall_test_result = 'PASS'
                elif result_status == 'FAIL':
                    self.overall_test_result = 'FAIL'
                break
            else:
                # Fallback to original patterns for backwards compatibility
                fail_pattern = r'Result\s*:\s*FAIL'
                pass_pattern = r'Result\s*:\s*PASS'
                
                if re.search(fail_pattern, result, re.IGNORECASE):
                    self.overall_test_result = 'FAIL'
                    break
                elif re.search(pass_pattern, result, re.IGNORECASE):
                    self.overall_test_result = 'PASS'

        if self.overall_test_result == 'FAIL':
            self.logger.fail_msg('OVERALL TEST RESULT : FAIL')
        else:
            self.logger.pass_msg('OVERALL TEST RESULT : PASS')