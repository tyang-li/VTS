# Copyright (C) 2024-2026 Intel Corporation
# Verification Test Suite
# DGDiag Memory Stress Test
import re 

from .dgdiag_base import dgdiagBase

class testClass(dgdiagBase):
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)
        self.testName = 'Memory_Stress_Test'
    
    def add_arguments(self):
        super().add_arguments()
        
        # Add all arguments using the helper function
        self.add_parser_argument('-inst', 'GPU Device instance', int, -1, 'inst')
        self.add_parser_argument('-testtime', 'Total time to monitor', int, 60, 'testtime')
        self.add_parser_argument('-stime', 'Sampling time interval', int, 0, 'stime')
    
    def prepareGpuCommands(self):
        super().prepareGpuCommands()
        self.gpuCommands = []
        envVars = f''

        if self.parsed_args.inst == -1:
            for inst in self.dginstances:
                self.gpuCommands.append(f'{envVars}./DGDiagTool -MEMORY.TEST.OpenCLMemTest inst={inst} testtime={self.parsed_args.testtime} stime={self.parsed_args.stime}')
                self.gpuCommands.append(f'{envVars}./DGDiagTool -MEMORY.TEST.StressTest inst={inst} testtime={self.parsed_args.testtime} stime={self.parsed_args.stime}')
        else:
            self.gpuCommands.append(f'{envVars}./DGDiagTool -MEMORY.TEST.OpenCLMemTest inst={self.parsed_args.inst} testtime={self.parsed_args.testtime} stime={self.parsed_args.stime}')
            self.gpuCommands.append(f'{envVars}./DGDiagTool -MEMORY.TEST.StressTest inst={self.parsed_args.inst} testtime={self.parsed_args.testtime} stime={self.parsed_args.stime}')  

        self.gpuCommands.append(f'{envVars}xpu-smi diag -d {self.parsed_args.inst} --singletest 2')

    def parseResults(self):
        fail_pattern = r'\|\s*Memory\s+Error\s*\|\s*Result\s*:\s*Fail'
        for result in self.gpu_test_results:
            if re.search(fail_pattern, result, re.IGNORECASE):
                self.overall_test_result = 'FAIL'
        super().parseResults()
