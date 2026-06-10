# Copyright (C) 2024-2026 Intel Corporation
# Verification Test Suite
# DGDiag PCIe Margin Test

from .dgdiag_base import dgdiagBase
from common.common_defs import STATUS_SUCCESS, STATUS_FAILED

class testClass(dgdiagBase):
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)
        self.testName = 'PCIe_Margin_Test'
    
    def add_arguments(self):
        super().add_arguments()
        
        # Add all arguments using the helper function
        self.add_parser_argument('-inst', 'GPU Device instance spec: -1 (all), single ID (e.g. 0), range (e.g. 0-3), or list (e.g. 0,1,2)', str, '-1', 'inst')
        self.add_parser_argument('-p_range', 'Target Phase Sweep Range', int, 35, 'p_range')
        self.add_parser_argument('-p_step', 'Target Phase Sweep Step Size', int, 1, 'p_step')
        self.add_parser_argument('-d_range', 'Target DAC Sweep Range', int, 255, 'd_range')
        self.add_parser_argument('-d_step', 'Target DAC Sweep Step Size', int, 2, 'd_step')
        self.add_parser_argument('-lane_mask', 'Target lane Mask (hex value from 0x1 to 0xFFFF)', str, '0xFFFF', 'lane_mask')
        self.add_parser_argument('-iterations', 'Number of iterations', int, 1, 'iterations')

    
    def prepareGpuCommands(self):
        super().prepareGpuCommands()
        self.gpuCommands = []
        envVars = f''

        try:
            _requested_gpu_ids, target_instances = self.resolve_selected_gpu_instances()
        except ValueError as parse_error:
            self.logger.error(f"Invalid -inst argument: {parse_error}")
            return STATUS_FAILED

        for inst in target_instances:
            self.gpuCommands.append(f'{envVars}./DGDiagTool -PCIE.TEST.LMT inst={inst} p_range={self.parsed_args.p_range} p_step={self.parsed_args.p_step} d_range={self.parsed_args.d_range} d_step={self.parsed_args.d_step} lane_mask={self.parsed_args.lane_mask} itr={self.parsed_args.iterations}')
        return STATUS_SUCCESS
