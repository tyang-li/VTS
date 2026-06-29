# Copyright (C) 2024-2026 Intel Corporation
from ..test_base import testBase
import os

class testClass(testBase):
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)
        self.testName = 'GPU_Health_Check'

    def add_arguments(self):
        super().add_arguments()

    def prepareGpuCommands(self):
        self.gpuCommands = []
        envVars = f''
       
        self.execution_dir = os.path.dirname(__file__)
        script_path = os.path.join(self.execution_dir, 'xpumTest.sh')
        
        # Make script executable before using it
        if not self.utils.make_script_executable(script_path):
            self.logger.warning(f"Could not make xpumTest.sh executable at {script_path}")
        
        # Use bash -c to ensure proper sourcing and function execution
        self.gpuCommands.append(f'{envVars}bash -c "source {script_path} && xpumTest health"')

    def parseResults(self):
        self.logger.subheader('Results Parsing...')

        # Check return codes: PASS only if all return codes are 0
        if self.gpu_return_codes and all(code == 0 for code in self.gpu_return_codes):
            self.overall_test_result = 'PASS'
            self.logger.pass_msg(f'OVERALL TEST RESULT : {self.overall_test_result}')
        else:
            self.overall_test_result = 'FAIL'
            self.logger.fail_msg(f'OVERALL TEST RESULT : {self.overall_test_result}')

        
