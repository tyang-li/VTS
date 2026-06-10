# Copyright (C) 2024-2026 Intel Corporation
# Verification Test Suite
# DGDiag Memory Stress Test
import re 

from .dgdiag_base import dgdiagBase
from common.common_defs import STATUS_SUCCESS, STATUS_FAILED

class testClass(dgdiagBase):
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)
        self.testName = 'Memory_Stress_Test'
    
    def add_arguments(self):
        super().add_arguments()
        
        # Add all arguments using the helper function
        self.add_parser_argument('-inst', 'GPU Device instance spec: -1 (all), single ID (e.g. 0), range (e.g. 0-3), or list (e.g. 0,1,2)', str, '-1', 'inst')
        self.add_parser_argument('-testtime', 'Total time to monitor', int, 60, 'testtime')
        self.add_parser_argument('-stime', 'Sampling time interval', int, 0, 'stime')
    
    def prepareGpuCommands(self):
        super().prepareGpuCommands()
        self.gpuCommands = []
        envVars = f''

        try:
            target_gpu_ids, target_dgdiag_instances = self.resolve_selected_gpu_instances()
        except ValueError as parse_error:
            self.logger.error(f"Invalid -inst argument: {parse_error}")
            return STATUS_FAILED

        if not target_dgdiag_instances:
            self.logger.error("No GPU instances selected for memory stress test")
            return STATUS_FAILED

        for inst in target_dgdiag_instances:
            self.gpuCommands.append(f'{envVars}./DGDiagTool -MEMORY.TEST.OpenCLMemTest inst={inst} testtime={self.parsed_args.testtime} stime={self.parsed_args.stime}')
            self.gpuCommands.append(f'{envVars}./DGDiagTool -MEMORY.TEST.StressTest inst={inst} testtime={self.parsed_args.testtime} stime={self.parsed_args.stime}')

        for inst in target_gpu_ids:
            self.gpuCommands.append(f'{envVars}xpu-smi diag -d {inst} --singletest 2')
        return STATUS_SUCCESS

    def parseResults(self):
        fail_pattern = r'\|\s*Memory\s+Error\s*\|\s*Result\s*:\s*Fail'
        for result in self.gpu_test_results:
            if re.search(fail_pattern, result, re.IGNORECASE):
                self.overall_test_result = 'FAIL'
        return super().parseResults()
