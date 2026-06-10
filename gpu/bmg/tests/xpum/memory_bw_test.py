# Copyright (C) 2024-2026 Intel Corporation
# Verification Test Suite
# Memory Bandwidth Test

import re
from ..test_base import testBase
from common.common_defs import STATUS_SUCCESS, STATUS_FAILED

class testClass(testBase):
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)
        self.testName = 'Memory_Bandwidth_Test'
    
    def add_arguments(self):
        super().add_arguments()
        
        # Add all arguments using the helper function
        self.add_parser_argument('-inst', 'GPU Device instance spec: -1 (all), single ID (e.g. 0), range (e.g. 0-3), or list (e.g. 0,1,2)', str, '-1', 'inst')

    def prepareGpuCommands(self):
        self.gpuCommands = []

        try:
            dgdiag_instances = self.device_manager.getGpuInstancesDGDiag()
            available_gpu_count = len(dgdiag_instances) if dgdiag_instances else None
            requested_instances = self.resolve_selected_gpu_ids(
                self.parsed_args.inst,
                available_gpu_count=available_gpu_count,
            )
        except ValueError as parse_error:
            self.logger.error(f"Invalid -inst argument: {parse_error}")
            return STATUS_FAILED

        if requested_instances is None:
            # All GPUs: let xpu-smi run without -d (or use device_manager if available)
            self.gpuCommands.append('xpu-smi diag --singletest 3')
        else:
            for gpu_id in requested_instances:
                self.gpuCommands.append(f'xpu-smi diag -d {gpu_id} --singletest 3')

        self.execution_dir = '.'
        return STATUS_SUCCESS

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
            return STATUS_FAILED
        else:
            self.logger.pass_msg('OVERALL TEST RESULT : PASS')
            return STATUS_SUCCESS