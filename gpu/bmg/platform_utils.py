# Copyright (C) 2024-2026 Intel Corporation
#Gaudi Verification Test Suite Habana Utils

from common.utils import Utils
from common.utils import BashScriptManager
import os
from . import platform_defs

class platformUtils():
    def __init__(self,logger, device_manager):
        self.logger = logger
        self.device_manager = device_manager
        self.utils = Utils(self.logger)
        self.busDict = {}
        
        # Initialize bash script manager for common scripts
        self.bash_manager = BashScriptManager(self.logger)
        self._init_device_detection_script()
    
    def _init_device_detection_script(self):
        """Initialize device detection script for hardware discovery operations."""
        common_folder = os.path.join(os.path.dirname(__file__), '..', '..', 'common')
        
        # Device detection script
        device_detect_script = os.path.join(common_folder, 'deviceDetect.sh')
        self.bash_manager.register_script('deviceDetect', device_detect_script)
    
    def _is_gpu_device(self, device_path):
        """Check if device is a GPU that DGDiag can query"""
        dgdiag_mapping = getattr(self, '_dgdiag_mapping', None)
        return self.utils.is_gpu_device(device_path, dgdiag_mapping)
    
    def _get_dgdiag_instance_mapping(self):
        """Create mapping from SBDF to DGDiag instance using ListGfx command"""
        try:
            diag_path = self.utils.installDGDiag(verbose=False)
            
            dgdiag_tool_path = os.path.join(diag_path, "DGDiagTool")
            if not os.path.exists(dgdiag_tool_path):
                return {}
            
            # Change to DGDiag directory and execute command
            original_cwd = os.getcwd()
            os.chdir(diag_path)
                
            command_str = "./DGDiagTool -PCIE.UTIL.ListGfx"
            output = self.utils.run_command_blocking(command_str)
            
            # Parse the output to create SBDF -> instance mapping
            mapping = {}
            lines = output.split('\n')
            
            for line in lines:
                # Look for lines with hex values like: 0X01 : 0X17 0X00   0X00     0X8086   0XE211
                if ':' in line and '0X' in line.upper():
                    parts = line.strip().split()
                    if len(parts) >= 6:
                        try:
                            # Extract components
                            index_str = parts[0]  # 0X01
                            bus_str = parts[2]    # 0X17
                            dev_str = parts[3]    # 0X00
                            func_str = parts[4]   # 0X00
                            
                            # Convert hex strings to integers
                            instance = int(index_str, 16)
                            bus = int(bus_str, 16)
                            device = int(dev_str, 16)
                            function = int(func_str, 16)
                            
                            # Construct SBDF (assuming segment 0000 as default)
                            sbdf = f"0000:{bus:02x}:{device:02x}.{function}"
                            mapping[sbdf] = instance
                            
                        except (ValueError, IndexError) as e:
                            # Skip malformed lines
                            continue
            
            return mapping
            
        except Exception as e:
            self.logger.error(f"Failed to get DGDiag instance mapping: {e}")
            return {}
        finally:
            try:
                os.chdir(original_cwd)
            except Exception as e:
                self.logger.error(f"Failed to restore working directory: {e}")
    
    def _get_dgdiag_instance_for_device(self, device_path):
        """Get DGDiag instance number for a given device path"""
        # Extract SBDF from device path (e.g., /sys/bus/pci/devices/0000:17:00.0 -> 0000:17:00.0)
        sbdf = device_path.split('/')[-1] if '/' in device_path else device_path
        
        # Get the mapping if not cached
        if not hasattr(self, '_dgdiag_mapping'):
            self._dgdiag_mapping = self._get_dgdiag_instance_mapping()
        
        # Return the instance for this SBDF, default to 1 if not found
        instance = self._dgdiag_mapping.get(sbdf, 1)
        return instance
    
    def _get_dgdiag_speed(self, command, search_pattern, device_path=None, device_instance=None):
        """Get speed using DGDiag command with correct instance mapping"""
        try:
            # Determine the correct device instance
            if device_instance is None:
                if device_path:
                    device_instance = self._get_dgdiag_instance_for_device(device_path)
                else:
                    device_instance = 1  # Default fallback
            
            diag_path = self.utils.installDGDiag(verbose=False)
            
            dgdiag_tool_path = os.path.join(diag_path, "DGDiagTool")
            if not os.path.exists(dgdiag_tool_path):
                return None
            
            # Change to DGDiag directory and execute command
            original_cwd = os.getcwd()
            os.chdir(diag_path)
                
            command_str = f"./DGDiagTool -{command} inst={device_instance}"
            
            output = self.utils.run_command_blocking(command_str)
            
            # Parse output to find the speed/width line
            lines = output.split('\n')
            
            for i, line in enumerate(lines):
                if search_pattern in line:
                    # Extract value after colon
                    value = line.split(':')[1].strip()
                    return value
            
            return None
            
        except Exception as e:
            self.logger.error(f"DGDiag command failed: {e}")
            return None
        finally:
            try:
                os.chdir(original_cwd)
            except Exception as e:
                self.logger.error(f"Failed to restore working directory: {e}")
    
    def get_current_speed(self, device_path):
        """Get current link speed for a PCI device with DGDiag fallback"""
        # Try sysfs first
        speed = self.utils.read_pci_attr(device_path, "current_link_speed")
        
        # If invalid, try DGDiag fallback (any device due to HW bug requiring bridge queries)
        if not self.utils.is_valid_pcie_speed(speed):
            speed = self._get_dgdiag_speed('PCIE.UTIL.GetLinkSpeed', 'Current Link Speed', device_path=device_path)
        
        return speed
    
    def get_current_width(self, device_path):
        """Get current link width for a PCI device with DGDiag fallback"""
        # Try sysfs first
        curr_width_raw = self.utils.read_pci_attr(device_path, "current_link_width")
        curr_link_width = f"x{curr_width_raw}" if curr_width_raw else ""
        
        # If invalid, try DGDiag fallback (any device due to HW bug requiring bridge queries)
        if not self.utils.is_valid_pcie_width(curr_link_width):
            curr_link_width = self._get_dgdiag_speed('PCIE.UTIL.GetLinkWidth', 'Current Link Width', device_path=device_path)
        
        return curr_link_width if curr_link_width else ""
    
    def get_max_speed(self, device_path):
        """Get maximum link speed for a PCI device with DGDiag fallback"""
        # Try sysfs first
        speed = self.utils.read_pci_attr(device_path, "max_link_speed")
        
        # If invalid, try DGDiag fallback (any device due to HW bug requiring bridge queries)
        if not self.utils.is_valid_pcie_speed(speed):
            speed = self._get_dgdiag_speed('PCIE.UTIL.GetMaxLinkSpeed', 'Max Link Speed', device_path=device_path)
        
        return speed
    
    def get_max_width(self, device_path):
        """Get maximum link width for a PCI device with DGDiag fallback"""
        # Try sysfs first
        max_width_raw = self.utils.read_pci_attr(device_path, "max_link_width")
        max_link_width = f"x{max_width_raw}" if max_width_raw else ""
        
        # If invalid, try DGDiag fallback (any device due to HW bug requiring bridge queries)
        if not self.utils.is_valid_pcie_width(max_link_width):
            max_link_width = self._get_dgdiag_speed('PCIE.UTIL.GetMaxLinkWidth', 'Max Link Width', device_path=device_path)
        
        return max_link_width if max_link_width else ""

    def getExpectedPCIeBW(self, pcie_speed, pcie_width):
        """
        Calculate theoretical PCIe bandwidth based on speed and width.
        
        Args:
            pcie_speed (str): PCIe speed in formats like "16.0 GT/s", "Gen4", or "16.0"
            pcie_width (str): PCIe width in formats like "x16", "16", or "X16"
            
        Returns:
            float: Theoretical bandwidth in GB/s, or None if inputs are invalid
        """
        try:
            # Parse and normalize speed to GT/s value
            speed_gts = self.utils.parse_pcie_speed_to_gts(pcie_speed)
            if speed_gts is None:
                return None
            
            # Parse and normalize width to lane count
            lane_count = self.utils.parse_pcie_width_to_lanes(pcie_width)
            if lane_count is None:
                return None
            
            # Determine encoding efficiency based on speed
            if speed_gts <= 5.0:  # PCIe 1.0 & 2.0 use 8b/10b encoding
                encoding_efficiency = 0.8  # 80%
            else:  # PCIe 3.0+ use 128b/130b encoding
                encoding_efficiency = 0.9846  # ~98.46%
            
            # Calculate bandwidth using formula:
            # Bandwidth (GB/s) = Speed (GT/s) × Width × Encoding Efficiency × Bytes per Transfer
            bytes_per_transfer = 1.0 / 8.0  # 1 bit per transfer, 8 bits = 1 byte
            bandwidth_gbps = speed_gts * lane_count * encoding_efficiency * bytes_per_transfer
            
            return round(bandwidth_gbps, 2)
            
        except Exception as e:
            self.logger.error(f"Failed to calculate PCIe bandwidth for speed='{pcie_speed}', width='{pcie_width}': {e}")
            return None
    
    def getPCIeBWPassFail(self, pcie_speed, pcie_width, direction, engine, mode, pcie_bandwidth_factors=None, pcie_bandwidth_overhead_factor=None):
        """
        Calculate PCIe bandwidth pass/fail threshold based on theoretical bandwidth
        and platform-specific factors.
        
        Args:
            pcie_speed (str): PCIe speed in formats like "16.0 GT/s", "Gen4", or "16.0"
            pcie_width (str): PCIe width in formats like "x16", "16", or "X16"
            direction (str): "bidirectional" or "unidirectional"
            engine (str): Engine type (currently ignored)
            mode (str): Test mode
            pcie_bandwidth_factors (dict): Platform-specific bandwidth factors
            pcie_bandwidth_overhead_factor (dict): Platform-specific bandwidth overhead factors
        Returns:
            float: Pass/fail threshold bandwidth in GB/s, or None if inputs are invalid
        """
        try:
            # Get theoretical PCIe bandwidth
            theoretical_bandwidth = self.getExpectedPCIeBW(pcie_speed, pcie_width)
            if theoretical_bandwidth is None:
                return None
            
            # Apply platform-specific factors from platform_defs.py
            if pcie_bandwidth_factors is None:
                # Default factors if not provided
                pcie_bandwidth_factors = {
                    'achievable': 0.95,
                    'part2part': 0.95,
                    'temp_impact': 0.99,
                    'std_dev_impact': 0.99
                }
            
            factors = pcie_bandwidth_factors
            adjusted_bandwidth = (theoretical_bandwidth * 
                                factors['achievable'] * 
                                factors['part2part'] * 
                                factors['temp_impact'] * 
                                factors['std_dev_impact'])
            # Apply overhead factor based on mode, direction, and engine
            if pcie_bandwidth_overhead_factor is None:
                # Default overhead factors if not provided
                pcie_bandwidth_overhead_factor = {
                    'compute':{'h2d': (64/(64+20)), 'd2h': (256/(256+20)), 'bidirectional': (64/(64+20))},
                    'copy':{'h2d': (256/(256+20)), 'd2h': (256/(256+20)), 'bidirectional': (256/(256+20))}
                }
            
            # Handle bidirectional vs unidirectional
            if direction and direction.lower() == "bidirectional":
                adjusted_bandwidth *= 2
            
            # Note: engine parameter is currently ignored as requested
            # Future enhancement: Apply engine-specific factors here
            overhead_factor = pcie_bandwidth_overhead_factor.get(engine, {}).get(direction.lower(), 1.0)
            adjusted_bandwidth *= overhead_factor
            
            return round(adjusted_bandwidth, 2)
            
        except Exception as e:
            self.logger.error(f"Failed to calculate PCIe BW pass/fail threshold for speed='{pcie_speed}', width='{pcie_width}', direction='{direction}': {e}")
            return None

    #Function to get static info
    def getStaticInfo(self, verbose=True):
        self.busDict = self.getBusInfo(self.device_manager.gpu_did, verbose)

    #Function to get PCI BUS info    
    def getBusInfo(self, device_id, verbose=True):
        # Create callbacks for platform-specific speed/width methods
        speed_width_callbacks = {
            'get_current_speed': self.get_current_speed,
            'get_current_width': self.get_current_width,
            'get_max_speed': self.get_max_speed,
            'get_max_width': self.get_max_width
        }
        
        return self.utils.get_bus_info(device_id, speed_width_callbacks, verbose)

    def getPsysPL2(self):
        """
        Execute DGDiagTool to get Psys Power Limit 2 value.
        
        Returns:
            float: The Psys Power Limit 2 value as a number (e.g., 200.0) or error message string
        """
        try:
            # Get DGDiag installation path dynamically
            diag_path = self.utils.installDGDiag(verbose=False)
            
            # Use full path to DGDiagTool executable
            dgdiag_tool_path = os.path.join(diag_path, "DGDiagTool")
            
            if not os.path.exists(dgdiag_tool_path):
                return f"DGDiagTool executable not found at: {dgdiag_tool_path}"
            
            # Change to DGDiag directory and execute command
            original_cwd = os.getcwd()
            os.chdir(diag_path)
            
            command_str = "./DGDiagTool -PM.Util.ShowPsysPL"
            
            # Use utils to execute the command
            output = self.utils.run_command_blocking(command_str)
            
            # Parse the output to extract Psys powerLimit2 value
            lines = output.split('\n')
            for line in lines:
                if 'Psys powerLimit2:' in line:
                    # Extract the value after the colon
                    value = line.split('Psys powerLimit2:')[1].strip()
                    
                    # Extract just the numeric part (e.g., "200 W" -> 200)
                    try:
                        numeric_value = value.split()[0]  # Get first part before space
                        result = float(numeric_value)
                        return result
                    except (ValueError, IndexError) as e:
                        return f"Could not parse numeric value from: {value}, error: {e}"
            
            return "Psys powerLimit2 not found in output"
            
        except Exception as e:
            return f"Execution failed: {e}"
        finally:
            # Always restore original working directory
            try:
                os.chdir(original_cwd)
            except Exception as e:
                self.logger.error(f"Failed to restore working directory: {e}")
        
    def get_min_power_threshold(self, default_threshold_watts=196.0):
        """Calculate minimum power threshold as 0.95 of PsysPL2.
        
        Args:
            default_threshold_watts: Default threshold to use if PsysPL2 cannot be determined
            
        Returns:
            float: Minimum power threshold in watts
        """
        try:
            psys_pl2 = self.getPsysPL2()
            
            # Ensure we have a valid numeric value
            if isinstance(psys_pl2, (int, float)) and psys_pl2 > 0:
                return 0.95 * psys_pl2
            else:
                if self.logger:
                    self.logger.warning(f"getPsysPL2() returned invalid value: {psys_pl2} (type: {type(psys_pl2)})")
                    self.logger.warning(f"Falling back to default power threshold of {default_threshold_watts}W")
                return default_threshold_watts
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Error getting PsysPL2 value: {e}")
                self.logger.warning(f"Falling back to default power threshold of {default_threshold_watts}W")
            return default_threshold_watts

    def disablePCIeDowngrade(self, gpu_instance):
        """
        Disable PCIe downgrade configuration for a specific GPU instance using DGDiag.
        
        Args:
            gpu_instance (int): GPU device instance number
            
        Returns:
            int: 0 if successfully disabled, 1 otherwise
        """
        try:
            # Get DGDiag installation path dynamically
            diag_path = self.utils.installDGDiag(verbose=False)
            
            # Use full path to DGDiagTool executable
            dgdiag_tool_path = os.path.join(diag_path, "DGDiagTool")
            
            if not os.path.exists(dgdiag_tool_path):
                self.logger.error(f"DGDiagTool not found at {dgdiag_tool_path}")
                return 1
            
            # Store the original working directory
            original_cwd = os.getcwd()
            
            # Change to DGDiag directory
            os.chdir(diag_path)
            
            # Execute the DGDiag command
            command_str = f"./DGDiagTool -PCIE.UTIL.DowngradeConfig inst={gpu_instance} downgrade=0"
            
            output = self.utils.run_command_blocking(command_str)
            
            # Check if the expected success string is in the output
            success_string = "Successfully set PCIe Downgrade Config Mode to Disabled"
            if success_string in output:
                return 0
            else:
                self.logger.error(f"Failed to disable PCIe downgrade for instance {gpu_instance}")
                return 1
                
        except Exception as e:
            self.logger.error(f"Failed to disable PCIe downgrade: {e}")
            return 1
        finally:
            # Always restore original working directory
            try:
                os.chdir(original_cwd)
            except Exception as e:
                self.logger.error(f"Failed to restore working directory: {e}")

    def getEOMStatus(self):
        """
        Execute DGDiagTool to get EOM (End of Manufacturing) status for all available instances.
        
        Returns:
            dict: Dictionary with instance:EOM_status pairs (0 if EOM not set, 1 if EOM set)
        """
        try:
            # Get DGDiag installation path dynamically
            diag_path = self.utils.installDGDiag(verbose=False)
            
            # Use full path to DGDiagTool executable
            dgdiag_tool_path = os.path.join(diag_path, "DGDiagTool")
            
            if not os.path.exists(dgdiag_tool_path):
                self.logger.error(f"DGDiagTool executable not found at: {dgdiag_tool_path}")
                self.logger.info("EOM status: Unable to determine (DGDiagTool not found)")
                return {}
            
            # Get all available DGDiag instances
            if not hasattr(self, '_dgdiag_mapping'):
                self._dgdiag_mapping = self._get_dgdiag_instance_mapping()
            
            # If no instances found, try default instance 1
            instances = list(self._dgdiag_mapping.values()) if self._dgdiag_mapping else [1]
            
            # Change to DGDiag directory
            original_cwd = os.getcwd()
            os.chdir(diag_path)
            
            eom_status_dict = {}
            
            # Query EOM status for each instance
            for instance in instances:
                command_str = f"./DGDiagTool -SYSINFO.UTIL.EOMInfo inst={instance}"
                
                try:
                    # Use utils to execute the command
                    output = self.utils.run_command_blocking(command_str)
                    
                    # Parse the output to extract EOM status
                    lines = output.split('\n')
                    eom_status = 0  # Default to not set
                    
                    for line in lines:
                        if 'EOM is' in line:
                            if 'not Set' in line:
                                eom_status = 0
                                break
                            elif 'Set' in line and 'not Set' not in line:
                                eom_status = 1
                                break
                    else:
                        # If we couldn't find the status line, log warning
                        self.logger.warning(f"Instance {instance}: EOM status could not be determined from DGDiag output")
                    
                    eom_status_dict[instance] = eom_status
                    
                except Exception as inst_e:
                    self.logger.error(f"Failed to get EOM status for instance {instance}: {inst_e}")
                    eom_status_dict[instance] = 0  # Default to not set on error
            
            return eom_status_dict
            
        except Exception as e:
            self.logger.error(f"Failed to get EOM status: {e}")
            self.logger.info("EOM status: Unable to determine (execution failed)")
            return {}
        finally:
            # Always restore original working directory
            try:
                os.chdir(original_cwd)
            except Exception as e:
                self.logger.error(f"Failed to restore working directory: {e}")

    
