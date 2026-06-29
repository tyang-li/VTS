# Copyright (C) 2024-2026 Intel Corporation
from abc import abstractmethod
from ..test_base import testBase

class onecclBase(testBase):
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)

    @abstractmethod
    def prepareGpuCommands(self):
        pass

    def parseResults(self):
        self.overall_test_result = 'FAIL'
        
        if self.overall_test_result == 'FAIL':
            msg = f'OVERALL TEST RESULT : FAIL'
            self.logger.fail_msg(msg)
        else:
            self.logger.pass_msg('OVERALL TEST RESULT : PASS')
