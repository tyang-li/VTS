# Copyright (C) 2024-2026 Intel Corporation
from ..test_base import testBase
from ... import platform_defs
import re
import os

class dgdiagBase(testBase):
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)
        self.dginstances = self.device_manager.getGpuInstancesDGDiag()
        if len(self.dginstances) == 0:
            raise Exception("Couldn't get GPU instances")
        self.overall_test_result = 'PASS'  # Start optimistic

    def resolve_selected_gpu_instances(self, inst_spec=None):
        """Map user-facing VTS GPU IDs to DGDiag instance IDs.

        VTS exposes GPUs as zero-based IDs across tests (0..N-1), while DGDiag
        often enumerates the same devices as one-based instance IDs (1..N).
        Resolve the user selection to both logical VTS GPU IDs and the DGDiag
        instances that should be passed to DGDiagTool.
        """
        ordered_dginstances = sorted(self.dginstances)
        requested_gpu_ids = self.resolve_selected_gpu_ids(
            self.parsed_args.inst if inst_spec is None else inst_spec,
            available_gpu_count=len(ordered_dginstances),
        )
        resolved_dginstances = [ordered_dginstances[gpu_id] for gpu_id in requested_gpu_ids]
        self.logger.info(
            f"Resolved VTS GPU IDs {requested_gpu_ids} to DGDiag instances {resolved_dginstances}"
        )
        return requested_gpu_ids, resolved_dginstances

    def prepareGpuCommands(self):
        # Ensure DGDiag is installed before running tests
        # Get version from platform definitions
        dgdiag_version = self.platform_defs_instance.tool_versions_dict.get('DGDiag')
        self.execution_dir = self.utils.installDGDiag(required_version=dgdiag_version)
        self.logger.info(f'GPU instances found: {self.dginstances}')
        
    def add_arguments(self):
        super().add_arguments()

    def parseResults(self):
        error_code = None
        error_description = None

        self.logger.subheader('Results Parsing...')

        fail_pattern = r'Result\s*:\s*FAIL'
        pass_pattern = r'Result\s*:\s*PASS'
        error_code_pattern = r'Error\s+Code\s*:\s*(0x[0-9A-Fa-f]{1,5})'

        # Define the path to the error description CSV file
        csv_path = os.path.join(os.path.dirname(__file__), 'Diag_Error_Description.csv')
        error_dict = self.platform_utils.utils.load_error_descriptions(default_csv_path=csv_path)

        # Check all results - if ANY fail, the overall test fails
        for result in self.gpu_test_results:
            if re.search(fail_pattern, result, re.IGNORECASE):
                self.overall_test_result = 'FAIL'
                # Extract error code if present
                match = re.search(error_code_pattern, result, re.IGNORECASE)
                if match:
                    error_code = match.group(1)
                    # Normalize case to uppercase for dictionary lookup (only hex digits after 0x)
                    if error_code.startswith('0x'):
                        error_code_normalized = '0x' + error_code[2:].upper()
                    else:
                        error_code_normalized = error_code.upper()
                    error_description = error_dict.get(error_code_normalized, 'Unknown error code')
                # Don't break - continue checking other results for additional failures

        if self.overall_test_result == 'FAIL':
            msg = f'OVERALL TEST RESULT : FAIL'
            if error_code:
                msg += f' (Error Code: {error_code})'
                msg += f' - {error_description}'
            self.logger.fail_msg(msg)
        else:
            self.logger.pass_msg('OVERALL TEST RESULT : PASS')
