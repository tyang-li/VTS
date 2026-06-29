# Copyright (C) 2024-2026 Intel Corporation
from abc import abstractmethod
from ..test_base import testBase

class aiwlBase(testBase):
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)

    @abstractmethod
    def prepareGpuCommands(self):
        pass

    def parseResults(self):
        if self.overall_test_result == 'FAIL':
            self.logger.fail_msg('OVERALL TEST RESULT : FAILED')
        else:
            self.logger.pass_msg('OVERALL TEST RESULT : COMPLETED')
        return



