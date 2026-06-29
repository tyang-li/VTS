# Copyright (C) 2024-2026 Intel Corporation
import os
from common.utils import Utils
from .. import platform_defs

class paramsMonitor():
    def __init__(self, logger, device_manager, sampling_rate = 1000, mon_mode = 'xpum'):
        self.monCsvFilePath = None
        self.logger = logger
        self.utils = Utils(self.logger)
        self.dev_manager = device_manager
        if self.dev_manager.gpu_num is None:
            self.dev_manager.discover_devices()
        
        self.monProcess = None
        self.sampling_rate = sampling_rate
        self.mon_mode = mon_mode
        
        # Create platform definitions instance for configuration access
        self.platform_defs_instance = platform_defs.BMGPlatformDefs(logger, device_manager)
        
        project_root = os.path.dirname(os.path.abspath(__file__))
        self.logs_dir = os.path.abspath(os.path.join(project_root, '..', '..','..','logs'))
        self.diag_install_path = '/opt/Intel Corporation/DGDiagTool'
        
        # Install DGDiag if monitor mode is dgdiag
        if self.mon_mode == 'dgdiag':
            try:
                # Get DGDiag version from platform definitions
                dgdiag_version = self.platform_defs_instance.tool_versions_dict.get('DGDiag')
                self.diag_install_path = self.utils.installDGDiag(required_version=dgdiag_version)
            except Exception as e:
                self.logger.error(f"Failed to install DGDiag for monitoring: {e}")
                raise e  # Re-raise the exception instead of falling back
          
    def startMonitoring(self, testName, timestamp):
        cwd = os.getcwd()
       
        if self.mon_mode == 'xpum':
            self.dids = ','.join(str(i) for i in range(self.dev_manager.gpu_num))
            # Validate parameters before using them
            try:
                # Sanitize testName first so spaces/special chars don't break path construction or validation
                safeTestName = self.utils.sanitize_test_name(testName)
                self.monCsvFilePath = os.path.join(self.logs_dir, f'paramMon_{safeTestName}_{timestamp}.csv')
                validated_dids = self.utils.validate_device_ids(self.dids)
                validated_csv_path = self.utils.validate_monitor_file_path(self.monCsvFilePath, safeTestName, logs_dir=self.logs_dir)
                validated_sampling_rate = self.utils.validate_sampling_rate(self.sampling_rate)
                
                # Build safe command with list arguments (no shell injection possible)
                safe_command = self.utils.build_safe_xpu_smi_command(
                    validated_dids, validated_csv_path, validated_sampling_rate
                )
                
                self.monProcess = self.utils.run_command_non_blocking(safe_command)
                self.monCsvFilePath = validated_csv_path  # Use validated path
                
            except ValueError as e:
                self.logger.error(f"Parameter validation failed for xpu-smi: {e}")
                self.monProcess = None
                self.monCsvFilePath = None
        elif self.mon_mode == 'dgdiag':
            os.chdir(self.diag_install_path)
            self.dids = ','.join(str(i) for i in range(1,self.dev_manager.gpu_num+1))
            # Validate parameters before using them
            try:
                # Sanitize testName first so spaces/special chars don't break path construction or validation
                safeTestName = self.utils.sanitize_test_name(testName)
                self.monCsvFilePath = os.path.join(self.logs_dir, f'paramMon_{safeTestName}_')
                validated_dids = self.utils.validate_device_ids(self.dids)
                validated_csv_path = self.utils.validate_monitor_file_path(self.monCsvFilePath, safeTestName, logs_dir=self.logs_dir)
                validated_sampling_rate = self.utils.validate_sampling_rate(self.sampling_rate)
                
                # Build safe command with list arguments (no shell injection possible)
                safe_command = self.utils.build_safe_dgdiag_command(
                    validated_dids, validated_csv_path, validated_sampling_rate
                )
                
                self.monProcess = self.utils.run_command_non_blocking(safe_command)
                self.monCsvFilePath = validated_csv_path  # Use validated path
                
            except ValueError as e:
                self.logger.error(f"Parameter validation failed for DGDiag: {e}")
                self.monProcess = None
                self.monCsvFilePath = None
        else:
            self.monProcess = None
            self.monCsvFilePath = None

        os.chdir(cwd)

        return self.monCsvFilePath 

    def stopMonitoring(self):      
        success = True
        if self.monProcess:
            if self.mon_mode == 'dgdiag':
                self.utils.killParentandChildProcesses(self.monProcess.process.pid)
            self.monProcess.terminate_process()
            success = not self.monProcess.is_running()
        else:
            self.logger.warning('Monitoring process not found.')
        return success
            
    