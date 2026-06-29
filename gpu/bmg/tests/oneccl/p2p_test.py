# Copyright (C) 2024-2026 Intel Corporation
# Verification Test Suite
# DGDiag System Test

from .oneccl_base import onecclBase

class testClass(onecclBase):
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)
        self.testName = 'OneCCL_P2P_Test'
    
    def add_arguments(self):
        super().add_arguments()
        
    def prepareGpuCommands(self):
        self.gpuCommands = []
        self.logger.info('')
        self.logger.warning('Test not implemented yet')
        self.execution_dir = '.'