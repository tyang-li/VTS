# Copyright (C) 2024-2026 Intel Corporation
from ..test_base import testBase
from common.common_defs import STATUS_SUCCESS, STATUS_FAILED
import os
import glob
import shutil
import sys

class testClass(testBase):
    def __init__(self, testNumber, logger, device_manager, parsed_args):
        super().__init__(testNumber, logger, device_manager, parsed_args)
        self.testName = 'PCIe_Margin_Test'
        
        # Set execution directory - required by parent testBase class
        self.execution_dir = os.getcwd()



    def _normalize_rx_num(self):
        """Normalize self.parsed_args.rxNum to a list and write it back.

        Handles three input forms:
          - list/tuple  -> kept as a list
          - scalar int  -> wrapped in a single-element list
          - None        -> defaults to [6]
        """
        rx_num = self.parsed_args.rxNum
        if isinstance(rx_num, (list, tuple)):
            rx_num_list = list(rx_num)
        elif rx_num is not None:
            rx_num_list = [rx_num]
        else:
            rx_num_list = [6]
        self.parsed_args.rxNum = rx_num_list
        return rx_num_list

    def _clean_lmt_results_directory(self):
        """Clean up any existing files in the LMT results directory before starting the test."""
        try:
            # Get path to LMT results directory
            current_dir = os.path.dirname(os.path.abspath(__file__))
            workspace_root = os.path.join(current_dir, '..', '..', '..', '..')
            lmt_results_dir = os.path.join(workspace_root, 'tools', 'LMT', 'PCIe_LMT_Results')
            lmt_results_dir = os.path.normpath(lmt_results_dir)
            
            if os.path.exists(lmt_results_dir):
                # Remove all files in the directory
                files_removed = 0
                for filename in os.listdir(lmt_results_dir):
                    file_path = os.path.join(lmt_results_dir, filename)
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        files_removed += 1
                
                if files_removed > 0:
                    self.logger.info(f'Cleaned up {files_removed} old files from LMT results directory')
                else:
                    self.logger.info('LMT results directory is already clean')
            else:
                # Create the directory if it doesn't exist
                os.makedirs(lmt_results_dir, exist_ok=True)
                self.logger.info('Created LMT results directory')
                
        except Exception as e:
            self.logger.error(f'Error cleaning LMT results directory: {str(e)}')

    def prepareGpuCommands(self):
        super().prepareGpuCommands()
        self.gpuCommands = []

        try:
            dgdiag_instances = self.device_manager.getGpuInstancesDGDiag()
            available_gpu_count = len(dgdiag_instances) if dgdiag_instances else None
            requested_instances = self.resolve_selected_gpu_ids(
                self.parsed_args.inst,
                available_gpu_count=available_gpu_count,
            )
            self.logger.info(f'LMT -inst selector resolved to: {requested_instances if requested_instances is not None else "all detected GPUs"}')
        except ValueError as parse_error:
            self.logger.error(f"Invalid -inst argument: {parse_error}")
            return STATUS_FAILED
        
        # Check if EOM is set on any GPU
        self.logger.subheader('Checking EOM status on all GPUs...')
        self.eom_status_dict = self.platform_utils.getEOMStatus()
        self.eom_detected = any(self.eom_status_dict.values()) if isinstance(self.eom_status_dict, dict) else bool(self.eom_status_dict)
        
        # If user already acknowledged EOM during PCIe downgrade and chose to continue,
        # respect that decision and proceed with test
        user_confirmed_eom = getattr(self, 'pcie_downgrade_blocked_by_eom', False)
        
        if self.eom_detected and not user_confirmed_eom:
            self.logger.warning('EOM detected on one or more GPUs - skipping all LMT test operations')
            self.execution_dir = '.'
            return STATUS_FAILED
        elif self.eom_detected and user_confirmed_eom:
            self.logger.info('EOM detected but user confirmed continuation during PCIe downgrade check - proceeding with test')

        # PCIe downgrade is handled by the centralized disable_pcie_downgrade_if_needed() in test_base.py
        # which runs before prepareGpuCommands() in the test execution flow

        # Unbind XE devices before running LMT test
        unbind_result = self.platform_utils.utils.unbindXeDevices(
            self.device_manager.gpu_did,
            verbose=True
        )
        
        # Store the list of unbound devices for later rebinding
        self.unbound_devices = unbind_result.get('unbound_devices', [])
        
        if not unbind_result['success']:
            self.logger.error(f"Failed to unbind XE devices: {unbind_result['error']}")
            # Continue with test anyway - LMT may still work with bound devices
        else:
            self.logger.info(f"Successfully unbound {len(unbind_result['unbound_devices'])} XE device(s)")
        
        # Clean up LMT results directory before starting test
        self._clean_lmt_results_directory()
        
        # Build command to call LMT_Qual_Fct_refactored.py script
        # Get absolute path to the LMT script from workspace root
        current_dir = os.path.dirname(os.path.abspath(__file__))
        workspace_root = os.path.join(current_dir, '..', '..', '..', '..')
        lmt_script_path = os.path.join(workspace_root, 'tools', 'LMT', 'LMT_Qual_Fct_refactored.py')
        lmt_script_path = os.path.normpath(lmt_script_path)
        
        rx_num_list = self._normalize_rx_num()
        rx_nums = ' '.join(map(str, rx_num_list))

        inst_spec = self.parsed_args.inst if self.parsed_args.inst is not None else '-1'
        self.gpuCommands.append(f'{sys.executable} {lmt_script_path} -n {self.parsed_args.numRepeats} -rn {rx_nums} -inst {inst_spec}')
        return STATUS_SUCCESS

    def add_arguments(self):
        super().add_arguments()
        
        # Add all arguments using the helper function
        self.add_parser_argument('-inst', 'GPU device selector. Supported forms: single (0), range (0-3), list (0,1,2,3), -1 for all devices', str, '-1', 'inst')
        self.add_parser_argument('-n', 'Number of repeats', int, 1, 'numRepeats')
        self.add_parser_argument(
            '-rn',
            'Receiver Number(s) to be tested on (1-6). Multiple space-separated values are accepted.',
            int,
            [6],
            'rxNum',
            nargs='+'
        )
        # Default pcie_downgrade to True for LMT tests; explicit CLI/JSON values still take precedence
        self.add_parser_argument('-pcie_downgrade', 'Disable PCIe downgrade before test execution (True/False)', lambda x: x.lower() == 'true', True, 'pcie_downgrade')

    def modifyTestName(self):
        self.parsed_args.mt = 'None'  # Ensure monitor type is None for LMT tests

        # Normalize rxNum to a list to handle scalar int (e.g. from JSON config) or None
        self._normalize_rx_num()

        # Add receiver number(s) to test name for log identification
        # De-duplicate while preserving order
        seen = set()
        rx_unique = []
        for rx in self.parsed_args.rxNum:
            if rx not in seen:
                seen.add(rx)
                rx_unique.append(rx)

        if len(rx_unique) == 1:
            rx_suffix = f"_Rx{rx_unique[0]}"
        else:
            rx_numbers = '-'.join(map(str, rx_unique))
            rx_suffix = f"_Rx{rx_numbers}"

        self.testName += rx_suffix

    def parseResults(self):
        self.logger.subheader('Results Parsing...')

        # Use EOM status from prepareGpuCommands to avoid duplicate calls
        eom_detected = getattr(self, 'eom_detected', False)
        user_confirmed_eom = getattr(self, 'pcie_downgrade_blocked_by_eom', False)
        pcie_failed = getattr(self, 'pcie_downgrade_failed', False)
        
        # If user confirmed EOM continuation, parse results normally
        # Only skip to UNKNOWN if EOM was detected without user confirmation, or PCIe downgrade failed
        if pcie_failed or (eom_detected and not user_confirmed_eom):
            if eom_detected and not user_confirmed_eom:
                self.logger.warning('EOM detected on one or more GPUs - setting test result to UNKNOWN')
            if pcie_failed:
                self.logger.warning('PCIe downgrade disable failed - setting test result to UNKNOWN')
            self.overall_test_result = 'UNKNOWN'
            self.logger.info('OVERALL TEST RESULT : UNKNOWN')
            return STATUS_FAILED
        
        # Get path to LMT results directory
        current_dir = os.path.dirname(os.path.abspath(__file__))
        workspace_root = os.path.join(current_dir, '..', '..', '..', '..')
        lmt_results_dir = os.path.join(workspace_root, 'tools', 'LMT', 'PCIe_LMT_Results')
        lmt_results_dir = os.path.normpath(lmt_results_dir)
        
        # Get logs directory
        logs_dir = os.path.join(workspace_root, 'logs')
        logs_dir = os.path.normpath(logs_dir)
        
        # Create logs directory with secure permissions
        from common.utils import Utils
        Utils.secure_log_directory(logs_dir)
        
        # Temporary workaround: Check if rxNum contains 1
        rx_num = getattr(self.parsed_args, 'rxNum', None)
        if isinstance(rx_num, (list, tuple, set)):
            rx_values = rx_num
        elif rx_num is None:
            rx_values = []
        else:
            rx_values = [rx_num]
        rx_contains_1 = 1 in rx_values
        
        # Check for combined results files to determine test result
        self.overall_test_result = 'UNKNOWN'
        
        if rx_contains_1:
            # Temporary workaround: Force result to UNKNOWN when rxNum=1
            self.overall_test_result = 'UNKNOWN'
        elif os.path.exists(lmt_results_dir):
            combined_files = glob.glob(os.path.join(lmt_results_dir, '*Combined_Results*'))
            
            for file in combined_files:
                filename = os.path.basename(file)
                if 'TestFail' in filename:
                    self.overall_test_result = 'FAIL'
                    break
                elif 'TestPass' in filename:
                    self.overall_test_result = 'PASS'
        
        # Move all files from LMT results directory to logs directory (if directory exists)
        if os.path.exists(lmt_results_dir):
            try:
                all_files = glob.glob(os.path.join(lmt_results_dir, '*'))
                for file_path in all_files:
                    if os.path.isfile(file_path):
                        filename = os.path.basename(file_path)
                        dest_path = os.path.join(logs_dir, filename)
                        shutil.move(file_path, dest_path)
                        # Only log individual file moves when not using rxNum=1 workaround
                        if not rx_contains_1:
                            self.logger.info(f'Moved {filename} to logs directory')
                
                self.logger.info(f'All LMT result files moved to {logs_dir}')
                
            except Exception as e:
                self.logger.error(f'Error moving LMT result files: {str(e)}')
        
        # Log final result
        if self.overall_test_result == 'FAIL':
            self.logger.error('OVERALL TEST RESULT : FAILED')
        elif self.overall_test_result == 'PASS':
            self.logger.info('OVERALL TEST RESULT : PASSED')
        else:
            self.logger.info('OVERALL TEST RESULT : UNKNOWN')
        
        # Rebind XE devices after LMT test completion
        self.logger.info('')
        bind_result = self.platform_utils.utils.bindXeDevices(
            self.platform_utils.bash_manager, 
            self.device_manager.gpu_did, 
            verbose=True,
            specific_devices=getattr(self, 'unbound_devices', None)
        )
        
        if bind_result['success']:
            self.logger.info(f"Successfully rebound {len(bind_result['bound_devices'])} XE device(s) to xe driver")
        else:
            self.logger.warning(f"Failed to rebind XE devices: {bind_result['error']}")
        
        # Important reboot recommendation
        self.logger.info('')
        self.logger.warning('=' * 80)
        self.logger.warning('WARNING: A SYSTEM REBOOT IS RECOMMENDED AFTER LMT TEST')
        self.logger.warning('The PCIe Lane Margin Test may have altered hardware state.')
        self.logger.warning('A reboot ensures complete system stability and driver recovery.')
        self.logger.warning('=' * 80)

        if self.overall_test_result == 'PASS':
            return STATUS_SUCCESS
        return STATUS_FAILED


