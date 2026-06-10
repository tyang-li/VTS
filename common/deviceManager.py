# Copyright (C) 2024-2026 Intel Corporation
# Verification Test Suite Device Manager
import os
import glob
import re
import subprocess # nosec
from common.utils import Utils
from common import common_defs


# --- Device Manager ---
class DeviceManager:
    """Manages device discovery and validation."""

    def __init__(self, logger):
        self.logger = logger
        self.utils = Utils(self.logger)
        self.gpu_did = None
        self.gpu_type = None
        self.gpu_num = None
        self.cpu_type = None
        self.cpu_num = None
        self.gpuDidDevicesDict = getattr(common_defs,'gpuDidDevicesDict')

    # create a discovery class that contains gpu type, family, and count
    def discover_devices(self, silent=False):
        try:
            self.gpu_family, self.gpu_did, self.gpu_type, self.gpu_num = self._gpuDiscovery(verbose=False)
            # Always log the discovery results for user feedback unless silent
            if not silent:
                self.logger.info(f"GPU Type  : {self.gpu_type}")
                self.logger.info(f"GPU Count : {self.gpu_num}")
        except Exception as e:
            self.logger.error(f"GPU discovery failed: {e}")
            raise
            
        try:
            self.cpu_type, self.cpu_num = self._cpuDiscovery(verbose=False)
            if not silent:
                self.logger.info(f"CPU Type  : {self.cpu_type}")
                self.logger.info(f"CPU Count : {self.cpu_num}")
        except Exception as e:
            self.logger.error(f"CPU discovery failed: {e}")
            raise

    def _gpuDiscovery(self, verbose=True):
        """
        This function will discover how many Accelerator Cards and which type are on the system
        """                     
        for did in self.gpuDidDevicesDict.keys():
            command = f'lspci -d:{did} -nn'
            output = self.utils.run_command_blocking(command)
            if output != '':
                gpu_did = did
                gpu_type = self.gpuDidDevicesDict.get(did,'unknown').get('model','unknown')
                gpu_family = self.gpuDidDevicesDict.get(did,'unknown').get('family','unknown')
                gpu_num = output.count(did.lower())
                
                if verbose: self.logger.info(f"GPU Type  : {gpu_type}")
                if verbose: self.logger.info(f"GPU Count : {gpu_num}")

                return gpu_family, gpu_did, gpu_type, gpu_num

        raise Exception("Can't determine gpu type and count")
    

    
    def _cpuDiscovery(self, verbose=True):
        command = f'lscpu'
        output = self.utils.run_command_blocking(command)
        if output != '':
            # Extract number of sockets
            match_sockets = re.search(r'^Socket\(s\):\s+(\d+)', output, re.MULTILINE)
            if match_sockets:
                num_cpus = int(match_sockets.group(1))
            else:
                raise Exception("Unable to parse number of sockets from lscpu output")
            # Extract model name
            match_model = re.search(r'^Model name:\s+(.+)', output, re.MULTILINE)
            if match_model:
                model_name = match_model.group(1).strip()
            else:
                raise Exception("Unable to parse model name from lscpu output")
            if verbose:
                self.logger.info(f"CPU Type  : {model_name}")
                self.logger.info(f"CPU Count : {num_cpus}")
                
            return model_name, num_cpus
        else:
            raise Exception("lscpu command returned no output")

    def getGpuInstances(self):
        """
        Get GPU instance numbers by mapping BDFs from xe driver to card numbers.
        
        Returns:
            list: List of instance numbers corresponding to each GPU on the system
            
        Example:
            If BDF 0000:0c:00.0 maps to card1, the instance number is 1
        """
        
        
        try:
            # Step 1: Get available PCI BDFs from xe driver
            xe_driver_path = "/sys/bus/pci/drivers/xe"
            if not os.path.exists(xe_driver_path):
                self.logger.warning("xe driver path not found, no GPU instances available")
                return []
            
            # Get all BDF directories (format: 0000:xx:xx.x)
            bdf_pattern = os.path.join(xe_driver_path, "????:??:??.?")
            bdf_paths = glob.glob(bdf_pattern)
            
            if not bdf_paths:
                self.logger.warning("No BDFs found in xe driver path")
                return []
            
            # Extract BDF strings from paths
            bdfs = [os.path.basename(path) for path in bdf_paths]
            
            # Step 2: Map BDFs to card numbers using /dev/dri/by-path
            dri_by_path = "/dev/dri/by-path"
            if not os.path.exists(dri_by_path):
                self.logger.warning("DRI by-path not found, cannot map BDFs to card numbers")
                return []
            
            instance_numbers = []
            
            for bdf in bdfs:
                # Look for symlink matching this BDF pattern: pci-{bdf}-card
                card_link_pattern = f"pci-{bdf}-card"
                card_link_path = os.path.join(dri_by_path, card_link_pattern)
                
                if os.path.exists(card_link_path):
                    # Read the symlink to get the card target (e.g., ../card1)
                    card_target = os.readlink(card_link_path)
                    
                    # Extract card number from target (e.g., card1 -> 1)
                    card_match = re.search(r'card(\d+)$', card_target)
                    if card_match:
                        instance_number = int(card_match.group(1))
                        instance_numbers.append(instance_number)
                    else:
                        self.logger.warning(f"Could not extract card number from {card_target} for BDF {bdf}")
                else:
                    self.logger.warning(f"Card symlink not found for BDF {bdf}: {card_link_path}")
            
            # Sort instance numbers for consistent ordering
            instance_numbers.sort()
            
            if len(instance_numbers) == 0:
                self.logger.warning("No GPU instances could be mapped")
                
            return instance_numbers
            
        except Exception as e:
            self.logger.error(f"Error getting GPU instances: {e}")
            return []

    def getGpuInstancesDGDiag(self):
        """
        Get GPU instance numbers using DGDiag's PCIE.UTIL.ListGfx command.
        
        Returns:
            list: List of instance numbers from DGDiag Index column
            
        Example:
            If DGDiag shows Index 0X01, 0X02, 0X03, 0X04, returns [1, 2, 3, 4]
        """
        try:
            # Get DGDiag installation path
            diag_path = self.utils.installDGDiag(verbose=False)
            
            dgdiag_tool_path = os.path.join(diag_path, "DGDiagTool")
            if not os.path.exists(dgdiag_tool_path):
                self.logger.warning(f"DGDiagTool executable not found at: {dgdiag_tool_path}")
                return []
            
            # Change to DGDiag directory and execute command
            original_cwd = os.getcwd()
            os.chdir(diag_path)
            
            try:
                command_str = "./DGDiagTool -PCIE.UTIL.ListGfx"
                try:
                    output = self.utils.run_command_blocking(command_str)
                except RuntimeError as run_error:
                    # DGDiag can return non-zero even when it prints valid Index rows.
                    # Fall back to raw subprocess output and continue parsing if present.
                    result = subprocess.run(
                        ["./DGDiagTool", "-PCIE.UTIL.ListGfx"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        check=False,
                    )
                    output = result.stdout or ""
                    if output.strip() == "":
                        raise run_error
                    self.logger.warning(
                        f"DGDiag ListGfx returned {result.returncode}; parsing available output."
                    )
                
                # Parse the output to extract Index values
                instance_numbers = []
                lines = output.split('\n')
                
                for line in lines:
                    # Look for lines with hex values like: 0X01 : 0X18 0X00   0X00     0X8086   0XE211
                    if ':' in line and '0X' in line.upper():
                        parts = line.strip().split()
                        if len(parts) >= 6:
                            try:
                                # Extract Index value (first part before colon)
                                index_str = parts[0]  # 0X01, 0X02, etc.
                                
                                # Convert hex string to integer
                                instance = int(index_str, 16)
                                instance_numbers.append(instance)
                                
                            except (ValueError, IndexError) as e:
                                # Skip malformed lines
                                self.logger.debug(f"Skipping malformed line: {line.strip()}")
                                continue
                
                # Sort instance numbers for consistent ordering
                instance_numbers.sort()
                
                if len(instance_numbers) == 0:
                    self.logger.warning("No GPU instances found in DGDiag output")
                
                return instance_numbers
                
            finally:
                # Always restore original working directory
                os.chdir(original_cwd)
            
        except Exception as e:
            self.logger.error(f"Failed to get DGDiag GPU instances: {e}")
            return []

