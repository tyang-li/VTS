# Copyright (C) 2024-2026 Intel Corporation
from datetime import datetime
import os
import subprocess # nosec
import shlex
from time import sleep
import re
import zipfile
import glob
import shutil
import sys
import threading
import time
from typing import List, Optional, TextIO, Callable
from pathlib import Path

# Handle both relative imports (when used as package) and absolute imports (when used standalone)
try:
    from .common_defs import (REALISTIC_POWER_MIN_W, REALISTIC_POWER_MAX_W,
                              REALISTIC_TEMP_MIN_C, REALISTIC_TEMP_MAX_C,
                              REALISTIC_FREQ_MIN_MHZ, REALISTIC_FREQ_MAX_MHZ)
except ImportError:
    # Fallback to absolute imports for standalone usage
    from common_defs import (REALISTIC_POWER_MIN_W, REALISTIC_POWER_MAX_W,
                             REALISTIC_TEMP_MIN_C, REALISTIC_TEMP_MAX_C,
                             REALISTIC_FREQ_MIN_MHZ, REALISTIC_FREQ_MAX_MHZ)

class NonBlockingProcess:
    def __init__(self, process, logger=None):
        self.process = process
        self.stdout = process.stdout  # Expose stdout for real-time reading
        self.logger = logger

    def is_running(self):
        return self.process.poll() is None

    def get_output(self):
        stdout, stderr = self.process.communicate()
        return stdout, stderr

    def terminate_process(self):
        self.process.terminate()

    def iter_output(self, stop_event=None):
        """Yield lines from stdout as they become available.
        
        Args:
            stop_event: Optional threading.Event to check for early termination.
                       If set, the iterator will exit promptly.
        """
        if self.stdout is not None:
            try:
                import select
                import time
                
                partial_line = ""
                last_yield_time = time.time()
                
                # Linux: Use select() for real-time output
                while True:
                    # Check stop_event first for prompt termination
                    if stop_event is not None and stop_event.is_set():
                        break
                    
                    # Check if process is still running or has output to read
                    process_running = self.process.poll() is None
                    
                    try:
                        # Use select with short timeout for non-blocking read
                        ready, _, _ = select.select([self.stdout], [], [], 0.1)
                        
                        if ready:
                            # Data is available to read
                            try:
                                char = self.stdout.read(1)
                                if not char:
                                    # EOF reached
                                    if partial_line:
                                        yield partial_line
                                    break
                                
                                if char == '\n':
                                    # Complete line
                                    yield partial_line
                                    partial_line = ""
                                elif char == '\r':
                                    # Carriage return - yield current line and reset
                                    if partial_line:
                                        yield partial_line
                                    partial_line = ""
                                else:
                                    # Regular character
                                    partial_line += char
                                    
                                    # Yield long lines to prevent memory issues
                                    if len(partial_line) > 1000:
                                        yield partial_line
                                        partial_line = ""
                                
                                last_yield_time = time.time()
                                
                            except (OSError, IOError):
                                # Handle broken pipe or other I/O errors
                                if partial_line:
                                    yield partial_line
                                break
                        
                        elif process_running:
                            # No data ready, but process still running
                            current_time = time.time()
                            if current_time - last_yield_time > 3:
                                yield f"[Process running... PID: {self.process.pid}, elapsed: {current_time - last_yield_time:.1f}s]"
                                last_yield_time = current_time
                        else:
                            # Process finished and no more data
                            if partial_line:
                                yield partial_line
                            break
                            
                    except OSError:
                        # select() not available or failed, fall back to blocking read
                        try:
                            # Check stop_event before reading
                            if stop_event is not None and stop_event.is_set():
                                break
                                
                            line = self.stdout.readline()
                            if line:
                                yield line.rstrip('\n\r')
                                last_yield_time = time.time()
                            elif not process_running:
                                break
                            else:
                                time.sleep(0.1)  # Brief pause to avoid busy loop
                        except (OSError, IOError) as e:
                            # Expected I/O errors during process communication
                            if self.logger:
                                self.logger.debug(f"Process I/O error (expected): {e}")
                            break
                        except (AttributeError, ValueError) as e:
                            # Programming errors - let them surface for debugging
                            if self.logger:
                                self.logger.error(f"Programming error in process output handling: {e}")
                            raise
                            
            except (OSError, subprocess.SubprocessError, IOError) as e:
                # Expected process/I/O errors - provide fallback
                yield f"[Process communication error: {e}]"
                try:
                    for line in iter(self.stdout.readline, ''):
                        if line:
                            yield line.rstrip('\n\r')
                        else:
                            break
                except (IOError, BrokenPipeError, OSError):
                    yield "[Failed to read subprocess output]"
    
    def get_return_code(self):
        """
        Check if the process is complete and return the exit code if finished.
        Non-blocking check - returns None if process is still running.
        
        Returns:
            int or None: Exit code from the process if finished, None if still running
        """
        try:
            # Check if process has finished (non-blocking)
            return self.process.poll()
        except (OSError, AttributeError) as e:
            # Process object issues or system errors
            if self.logger:
                self.logger.debug(f"Process poll failed: {e}")
            return None
        except Exception as e:
            # Unexpected errors should be investigated
            if self.logger:
                self.logger.error(f"Unexpected error checking process status: {type(e).__name__}: {e}")
            raise


class DGDiagSysInfoCollector:
    """
    Comprehensive system information collector using DGDiag commands.
    Runs various dgdiag commands silently and extracts key system information.
    """
    
    def __init__(self, utils_instance):
        """
        Initialize the system info collector.
        
        Args:
            utils_instance: Instance of Utils class for DGDiag operations and logging
        """
        self.utils = utils_instance
        self.logger = utils_instance.logger
        self.dgdiag_path = None
        self.gpu_instances = []  # Will be populated by detection
        self.display_instances = []  # Will be populated by detection
        
        # Detect available instances
        self._detect_instances()
        
    def _detect_instances(self):
        """Detect available GPU and display instances"""
        try:
            self._ensure_dgdiag_available()
            original_cwd = os.getcwd()
            os.chdir(self.dgdiag_path)
            
            # Detect GPU instances (0-8 inclusive)
            for i in range(9):
                cmd = ['./DGDiagTool', f'-SYSINFO.UTIL.GetDGSocRev', f'inst={i}']
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)  # nosec
                    if result.returncode == 0:
                        self.gpu_instances.append(i)
                except Exception:
                    continue
            
            # Skip display instance detection - not needed for server systems
            # Display commands are Windows/desktop-specific
            
            os.chdir(original_cwd)
                
        except Exception as e:
            if 'original_cwd' in locals():
                os.chdir(original_cwd)
        
    def _ensure_dgdiag_available(self):
        """Ensure DGDiag is installed and available"""
        if not self.dgdiag_path:
            self.dgdiag_path = self.utils.installDGDiag(verbose=False)
            if not self.dgdiag_path:
                raise RuntimeError("DGDiag installation not available")
        return True
    
    def _run_dgdiag_command_silent(self, command, timeout=30):
        """
        Run a DGDiag command silently and return output.
        
        Args:
            command (str): DGDiag command to run (e.g., "SYSINFO.UTIL.OSInfo")
            timeout (int): Command timeout in seconds
            
        Returns:
            str: Command output or empty string if failed
        """
        try:
            self._ensure_dgdiag_available()
            
            # Change to DGDiag directory and run command
            original_cwd = os.getcwd()
            os.chdir(self.dgdiag_path)
            
            try:
                full_command = ['./DGDiagTool', f'-{command}']
                
                # Add instance parameter for commands that need it
                if 'inst' in command.lower() or any(cmd in command for cmd in ['SYSTEM.TEST', 'TEMP.UTIL', 'PCIE.UTIL', 'SYSINFO.UTIL.GetDGSocRev']):
                    if self.gpu_instances:
                        if not any(f'inst={inst}' in ' '.join(full_command) for inst in self.gpu_instances):
                            full_command.append(f'inst={self.gpu_instances[0]}')  # Use first available GPU instance
                    else:
                        return ""
                
                # Skip display commands - not available on server systems
                elif 'disp' in command.lower() or 'DISPLAY.' in command:
                    return ""
                
                result = subprocess.run(
                    full_command, 
                    capture_output=True, 
                    text=True, 
                    timeout=timeout
                )  # nosec
                
                if result.returncode == 0:
                    return result.stdout
                else:
                    return ""
            finally:
                os.chdir(original_cwd)
                
        except Exception as e:
            # Fail silently for system info collection
            return ""
    
    def get_gpu_configuration(self):
        """Get GPU configuration details - SYSTEM.TEST.SYSConfiguration disabled"""
        # Command disabled: SYSTEM.TEST.SYSConfiguration fails with return code 18
        return {}
    
    def get_opencl_info(self):
        """Get OpenCL device information"""
        output = self._run_dgdiag_command_silent("SYSINFO.UTIL.DGOpenCLInfo")
        opencl_info = {}
        
        if output:
            lines = output.split('\n')
            for line in lines:
                line = line.strip()
                
                # Number of devices
                if 'Number of devices' in line and ':' in line and 'number_of_devices' not in opencl_info:
                    try:
                        opencl_info['number_of_devices'] = int(line.split(':', 1)[1].strip())
                    except (ValueError, IndexError):
                        pass
                
                # Device Name
                elif 'Device Name' in line and ':' in line and 'device_name' not in opencl_info:
                    opencl_info['device_name'] = line.split(':', 1)[1].strip()
                
                # Device Version (OpenCL version)
                elif 'Device Version' in line and ':' in line and 'device_version' not in opencl_info:
                    opencl_info['device_version'] = line.split(':', 1)[1].strip()
                
                # Driver Version
                elif 'Driver Version' in line and ':' in line and 'driver_version' not in opencl_info:
                    opencl_info['driver_version'] = line.split(':', 1)[1].strip()
                
                # Max Compute Units
                elif 'Max Compute Units' in line and ':' in line and 'compute_units' not in opencl_info:
                    try:
                        opencl_info['compute_units'] = int(line.split(':', 1)[1].strip())
                    except (ValueError, IndexError):
                        pass
                
                # Max clock Frequency
                elif 'Max clock Frequency' in line and 'MHz' in line and 'max_clock_mhz' not in opencl_info:
                    match = re.search(r'(\d+)\s*MHz', line)
                    if match:
                        opencl_info['max_clock_mhz'] = int(match.group(1))
                
                # Global memory size (extract both raw bytes and GB)
                elif 'Global memory size' in line and 'Gbytes' in line and 'global_memory_gb' not in opencl_info:
                    # Extract the GB value from parentheses: "32530182144 (30.296) Gbytes"
                    gb_match = re.search(r'\((\d+\.?\d*)\)\s*Gbytes', line)
                    if gb_match:
                        opencl_info['global_memory_gb'] = float(gb_match.group(1))
                    
                    # Also extract raw bytes value
                    bytes_match = re.search(r'(\d+)\s*\(', line)
                    if bytes_match:
                        opencl_info['global_memory_bytes'] = int(bytes_match.group(1))
        
        return opencl_info
    
    def get_host_memory_info(self):
        """Get host memory and system information"""
        output = self._run_dgdiag_command_silent("SYSINFO.UTIL.HostMemoryInfo")
        host_info = {}
        
        if output:
            lines = output.split('\n')
            for line in lines:
                line = line.strip()
                if 'Host Memory Speed' in line:
                    match = re.search(r'(\d+)\s*MHz', line)
                    if match:
                        host_info['memory_speed_mhz'] = int(match.group(1))
                elif 'Host Memory Type' in line:
                    host_info['memory_type'] = line.split(':', 1)[1].strip()
                elif 'Host total memory' in line:
                    match = re.search(r'(\d+\.?\d*)\s*GB', line)
                    if match:
                        host_info['total_memory_gb'] = float(match.group(1))
                elif 'Host available memory' in line:
                    match = re.search(r'(\d+\.?\d*)\s*GB', line)
                    if match:
                        host_info['available_memory_gb'] = float(match.group(1))
                elif 'BIOS Version' in line:
                    host_info['bios_version'] = line.split(':', 1)[1].strip()
                elif 'BIOS Vendor' in line:
                    host_info['bios_vendor'] = line.split(':', 1)[1].strip()
                elif 'BIOS Release date' in line:
                    host_info['bios_release_date'] = line.split(':', 1)[1].strip()
                elif 'Processor Name' in line:
                    host_info['processor_name'] = line.split(':', 1)[1].strip()
                elif 'Hardware Vendor' in line:
                    host_info['hardware_vendor'] = line.split(':', 1)[1].strip()
                elif 'Hardware Model' in line:
                    host_info['hardware_model'] = line.split(':', 1)[1].strip()
                elif 'OS Name' in line:
                    host_info['os_name'] = line.split(':', 1)[1].strip()
                elif 'System Name' in line:
                    host_info['system_name'] = line.split(':', 1)[1].strip()
        
        return host_info
    
    def get_os_info(self):
        """Get operating system and hardware information"""
        output = self._run_dgdiag_command_silent("SYSINFO.UTIL.OSInfo")
        os_info = {}
        
        if output:
            lines = output.split('\n')
            for line in lines:
                line = line.strip()
                if 'Hardware Vendor' in line and ':' in line:
                    os_info['hardware_vendor'] = line.split(':', 1)[1].strip()
                elif 'Hardware Model' in line and ':' in line:
                    os_info['hardware_model'] = line.split(':', 1)[1].strip()
                elif 'Operating System' in line and ':' in line:
                    os_info['operating_system'] = line.split(':', 1)[1].strip()
                elif 'Firmware Version' in line and ':' in line:
                    os_info['firmware_version'] = line.split(':', 1)[1].strip()
                elif 'Firmware Date' in line and ':' in line:
                    os_info['firmware_date'] = line.split(':', 1)[1].strip()
        
        return os_info
    
    def get_power_info(self):
        """Get power limit information"""
        output = self._run_dgdiag_command_silent("PM.UTIL.ShowPsysPL")
        power_info = {}
        
        if output:
            lines = output.split('\n')
            for line in lines:
                line = line.strip()
                if 'Psys powerLimit2:' in line:
                    # Extract power value from "Psys powerLimit2: 230 W"
                    match = re.search(r'Psys powerLimit2:\s*(\d+)\s*W', line)
                    if match:
                        power_info['psys_power_limit2_w'] = int(match.group(1))
                elif 'Psys powerLimit1:' in line:
                    # Extract power value from "Psys powerLimit1: 0 W"
                    match = re.search(r'Psys powerLimit1:\s*(\d+)\s*W', line)
                    if match:
                        power_info['psys_power_limit1_w'] = int(match.group(1))
                elif 'Psys powerLimit2En:' in line:
                    # Extract enable status
                    match = re.search(r'Psys powerLimit2En:\s*(\d+)', line)
                    if match:
                        power_info['psys_power_limit2_enabled'] = bool(int(match.group(1)))
        
        return power_info
    
    def get_pcie_info(self):
        """Get PCIe configuration"""
        speed_output = self._run_dgdiag_command_silent("PCIE.UTIL.GetLinkSpeed")
        width_output = self._run_dgdiag_command_silent("PCIE.UTIL.GetLinkWidth")
        
        pcie_info = {}
        
        if speed_output:
            speed_match = re.search(r'Current Link Speed\s*:\s*(\w+)', speed_output)
            if speed_match:
                pcie_info['link_speed'] = speed_match.group(1)
        
        if width_output:
            width_match = re.search(r'Current Link Width\s*:\s*(\w+)', width_output)
            if width_match:
                pcie_info['link_width'] = width_match.group(1)
        
        return pcie_info
    
    def get_temperature_info(self):
        """Get temperature readings"""
        output = self._run_dgdiag_command_silent("TEMP.UTIL.ShowTemperature")
        temp_info = {}
        
        if output:
            lines = output.split('\n')
            for line in lines:
                line = line.strip()
                if 'Global Temperature' in line:
                    match = re.search(r'(\d+\.?\d*)\s*C', line)
                    if match:
                        temp_info['global_temp_c'] = float(match.group(1))
                elif 'GPU Temperature' in line:
                    match = re.search(r'(\d+\.?\d*)\s*C', line)
                    if match:
                        temp_info['gpu_temp_c'] = float(match.group(1))
                elif 'Memory Temperature' in line:
                    match = re.search(r'(\d+\.?\d*)\s*C', line)
                    if match:
                        temp_info['memory_temp_c'] = float(match.group(1))
        
        return temp_info
    
    def get_soc_info(self):
        """Get SoC revision information"""
        output = self._run_dgdiag_command_silent("SYSINFO.UTIL.GetDGSocRev")
        soc_info = {}
        
        if output:
            rev_match = re.search(r'SoC Revision ID\s*=\s*(\w+)', output)
            if rev_match:
                soc_info['soc_revision'] = rev_match.group(1)
        
        return soc_info
    
    def collect_all_info(self):
        """
        Collect all system information from DGDiag commands.
        
        Returns:
            dict: Comprehensive system information dictionary
        """
        
        system_info = {
            'collection_timestamp': datetime.now().isoformat(),
        }
        
        # Collect information from each category
        try:
            system_info['gpu_config'] = self.get_gpu_configuration()
        except Exception as e:
            self.logger.debug(f"Failed to collect gpu_config: {e}")
            system_info['gpu_config'] = {}
        
        try:
            system_info['opencl'] = self.get_opencl_info()
        except Exception as e:
            self.logger.debug(f"Failed to collect opencl info: {e}")
            system_info['opencl'] = {}
        
        try:
            system_info['host_memory'] = self.get_host_memory_info()
        except Exception as e:
            self.logger.debug(f"Failed to collect host_memory info: {e}")
            system_info['host_memory'] = {}
        
        try:
            system_info['os_info'] = self.get_os_info()
        except Exception as e:
            self.logger.debug(f"Failed to collect os_info: {e}")
            system_info['os_info'] = {}
        
        system_info['display'] = {}  # Disabled for server systems
        
        try:
            system_info['pcie'] = self.get_pcie_info()
        except Exception as e:
            self.logger.debug(f"Failed to collect pcie info: {e}")
            system_info['pcie'] = {}
        
        try:
            system_info['temperatures'] = self.get_temperature_info()
        except Exception as e:
            self.logger.debug(f"Failed to collect temperature info: {e}")
            system_info['temperatures'] = {}
        
        try:
            system_info['power'] = self.get_power_info()
        except Exception as e:
            self.logger.debug(f"Failed to collect power info: {e}")
            system_info['power'] = {}
        
        try:
            system_info['soc'] = self.get_soc_info()
        except Exception as e:
            self.logger.debug(f"Failed to collect soc info: {e}")
            system_info['soc'] = {}
        
        system_info['health'] = {'yellow_bang_detected': False, 'tdr_events_detected': False}  # Disabled for Linux
        
        return system_info
    
    def print_system_summary(self, system_info=None, gpu_instance=1):
        """
        Print a formatted system summary.
        
        Args:
            system_info (dict, optional): Pre-collected system info. If None, will collect fresh info.
            gpu_instance (int): GPU instance number
        """
        if system_info is None:
            system_info = self.collect_all_info(gpu_instance)
        
        self.logger.subheader("SYSTEM INFORMATION SUMMARY")
        
        # GPU Information
        opencl_info = system_info.get('opencl', {})
        if opencl_info.get('device_name'):
            self.logger.info(f"GPU Device: {opencl_info['device_name']}")
        
        if opencl_info.get('number_of_devices'):
            self.logger.info(f"Number of GPU Devices: {opencl_info['number_of_devices']}")
        
        if opencl_info.get('device_version'):
            self.logger.info(f"Device Version: {opencl_info['device_version']}")
        
        if opencl_info.get('driver_version'):
            self.logger.info(f"GPU Driver: {opencl_info['driver_version']}")
        
        if opencl_info.get('max_clock_mhz'):
            self.logger.info(f"Max GPU Clock: {opencl_info['max_clock_mhz']} MHz")
        
        if opencl_info.get('compute_units'):
            self.logger.info(f"Max Compute Units: {opencl_info['compute_units']}")
        
        if opencl_info.get('global_memory_gb'):
            memory_info = f"GPU Memory: {opencl_info['global_memory_gb']:.1f} GB"
            if opencl_info.get('global_memory_bytes'):
                memory_info += f" ({opencl_info['global_memory_bytes']:,} bytes)"
            self.logger.info(memory_info)
        
        # GPU Configuration
        gpu_config = system_info.get('gpu_config', {})
        if gpu_config.get('gpu_memory_gb'):
            memory_info = f"GPU Memory: {gpu_config['gpu_memory_gb']} GB"
            if gpu_config.get('memory_speed_gts'):
                memory_info += f" @ {gpu_config['memory_speed_gts']} GT/s"
            self.logger.info(memory_info)
        
        if gpu_config.get('gfx_version'):
            self.logger.info(f"GFX Version: {gpu_config['gfx_version']}")
        
        # Host System
        host_info = system_info.get('host_memory', {})
        if host_info.get('os_name'):
            self.logger.info(f"Operating System: {host_info['os_name']}")
        
        if host_info.get('system_name'):
            self.logger.info(f"System: {host_info['system_name']}")
        
        # Host Memory
        if host_info.get('total_memory_gb') and host_info.get('available_memory_gb'):
            memory_str = f"Host Memory: {host_info['available_memory_gb']:.1f} GB available / {host_info['total_memory_gb']:.1f} GB total"
            if host_info.get('memory_type') and host_info.get('memory_speed_mhz'):
                memory_str += f" ({host_info['memory_type']} @ {host_info['memory_speed_mhz']} MHz)"
            self.logger.info(memory_str)
        
        # PCIe Configuration
        pcie_info = system_info.get('pcie', {})
        if pcie_info.get('link_speed') and pcie_info.get('link_width'):
            self.logger.info(f"PCIe: {pcie_info['link_speed']} {pcie_info['link_width']}")
        
        # Display Configuration
        display_info = system_info.get('display', {})
        if display_info.get('resolution') and display_info.get('refresh_rate_hz'):
            self.logger.info(f"Display: {display_info['resolution']} @ {display_info['refresh_rate_hz']} Hz")
        
        # Temperatures
        temps = system_info.get('temperatures', {})
        if temps.get('gpu_temp_c') and temps.get('memory_temp_c'):
            self.logger.info(f"Temperatures: GPU {temps['gpu_temp_c']:.0f}°C, Memory {temps['memory_temp_c']:.0f}°C")
        
        # SoC Information
        soc_info = system_info.get('soc', {})
        if soc_info.get('soc_revision'):
            self.logger.info(f"SoC Revision: {soc_info['soc_revision']}")
        
        # Health Status
        health = system_info.get('health', {})
        health_issues = []
        if health.get('yellow_bang_detected'):
            health_issues.append("Device Manager issues detected")
        if health.get('tdr_events_detected'):
            health_issues.append("TDR events detected")
        
        if health_issues:
            self.logger.warning(f"Health Issues: {', '.join(health_issues)}")
        else:
            self.logger.info("System Health: No issues detected")
        
        # BIOS
        if host_info.get('bios_version'):
            self.logger.info(f"BIOS: {host_info['bios_version']}")


class Utils:
    def __init__(self, logger):
        self.logger = logger
        self._bash_manager = None  # Lazy initialization
        self._bash_manager_initialized = False
        self._sysinfo_collector = None  # Lazy initialization
    
    @property
    def bash_manager(self):
        """Lazy initialization of BashScriptManager to avoid circular dependencies"""
        if not self._bash_manager_initialized:
            self._bash_manager_initialized = True
            try:
                import os.path
                self._bash_manager = BashScriptManager(self.logger)
                
                # Register the deviceDetect script
                script_path = os.path.join(os.path.dirname(__file__), 'deviceDetect.sh')
                if os.path.exists(script_path):
                    self._bash_manager.register_script('deviceDetect', script_path)
                else:
                    if self.logger:
                        self.logger.warning(f"deviceDetect.sh not found at: {script_path}")
                    self._bash_manager = None
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"BashScriptManager initialization failed: {e}")
                self._bash_manager = None
        
        return self._bash_manager
    
    @property  
    def sysinfo_collector(self):
        """Lazy initialization of DGDiagSysInfoCollector"""
        if self._sysinfo_collector is None:
            try:
                self._sysinfo_collector = DGDiagSysInfoCollector(self)
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"DGDiagSysInfoCollector initialization failed: {e}")
                self._sysinfo_collector = None
        
        return self._sysinfo_collector

    def run_command_non_blocking(self, command, cwd=None, env=None, use_shell=False):
        """
        Executes a command non-blocking and returns a helper object.
        Args:
            command (str or list): Command to execute.
            cwd (str, optional): Working directory.
            env (dict, optional): Environment variables.
            use_shell (bool): If True, run through shell (needed for pipes/redirects). Default False.
        Returns:
            NonBlockingProcess: Helper for process interaction.
        """
        if use_shell:
            # shell=True requires a string command; join list safely to prevent dropped arguments
            if isinstance(command, list):
                command = shlex.join(command)
        else:
            # Convert string commands to list form for safety (avoids shell injection)
            if isinstance(command, str):
                command = shlex.split(command)
        # Set environment variables to ensure unbuffered output
        if env is None:
            env = os.environ.copy()  # Inherit current environment (includes XDG_RUNTIME_DIR, etc.)
        else:
            env = env.copy()
        
        # Preserve critical environment variables that may be needed by GUI applications
        for critical_var in ['XDG_RUNTIME_DIR', 'DISPLAY', 'WAYLAND_DISPLAY']:
            if critical_var in os.environ and critical_var not in env:
                env[critical_var] = os.environ[critical_var]
        
        env['PYTHONUNBUFFERED'] = '1'
        # Force unbuffered output for bash scripts
        env['BASH_ENV'] = ''  # Disable bash startup files that might affect buffering
        # For scripts that use tee or other utilities
        env['STDBUF'] = '0'  # Unbuffered
        
        # Linux: Use pty for real-time output if available
        try:
            import pty
            # Try to use pty for better real-time output
            master, slave = pty.openpty()
            process = subprocess.Popen(
                command,
                shell=use_shell,
                stdout=slave,
                stderr=slave,
                stdin=slave,
                text=True,
                cwd=cwd,
                env=env,
                preexec_fn=os.setsid
            )
            os.close(slave)  # Close slave end in parent
            # Replace stdout with master end for reading
            process.stdout = os.fdopen(master, 'r')
            process.stderr = None  # Already combined with stdout
        except ImportError:
            # Fallback to regular pipes if pty not available
            process = subprocess.Popen(
                command,
                shell=use_shell,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Combine for simplicity
                text=True,
                bufsize=0,  # Unbuffered
                cwd=cwd,
                env=env
            )
        except (OSError, subprocess.SubprocessError) as e:
            # Expected process creation errors - try fallback
            self.logger.debug(f"Process creation failed, using fallback: {e}")
            process = subprocess.Popen(
                command,
                shell=use_shell,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=0,  # Unbuffered
                cwd=cwd,
                env=env
            )
        return NonBlockingProcess(process, self.logger)

    def run_command_blocking(self, command, use_shell=False):
        """
        Executes a given command on the OS using subprocess.
        Raises an exception if the command fails.
        Args:
            command (str or list): Command to execute.
            use_shell (bool): If True, run through shell (needed for pipes/redirects). Default False.
        Returns:
            str: Standard output from the command.
        Raises:
            RuntimeError: If the command execution fails.
        """
        if use_shell:
            # shell=True requires a string command; join list safely to prevent dropped arguments
            if isinstance(command, list):
                command = shlex.join(command)
        else:
            # Convert string commands to list form for safety (avoids shell injection)
            if isinstance(command, str):
                command = shlex.split(command)
        try:
            result = subprocess.run(
                command,
                shell=use_shell,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Command failed with exit code {e.returncode}: {e.stderr.strip()}")

    def print_command_parts(self, command):
        """Prints environment variables, command, and parameters separately."""
        tokens = shlex.split(command)
        env_vars = []
        cmd = None
        params = []

        # Collect env vars
        for i, token in enumerate(tokens):
            if '=' in token and not token.startswith('=') and token.find('=') > 0:
                env_vars.append(token)
            else:
                cmd = token
                params = tokens[i+1:]
                break

        if env_vars and self.logger:
            self.logger.info('')
            self.logger.info("Environment variables:")
            # Split env vars into key and value
            split_vars = [var.split('=', 1) for var in env_vars]
            max_key_len = max(len(k) for k, v in split_vars)
            for k, v in split_vars:
                self.logger.info(f"    {k.ljust(max_key_len)} = {v}")
        if cmd and self.logger:
            self.logger.info('')
            self.logger.info(f"Command:")
            self.logger.info(f"    {cmd}")
        if params and self.logger:
            self.logger.info('')
            self.logger.info("Parameters: ")
            self.logger.info(f"    {' '.join(params)}")

    def _extract_repetition_number(self, filepath):
        """
        Extract repetition number from filename with _repXX pattern.
        
        Args:
            filepath (str): File path containing repetition pattern (e.g., /path/test_rep01.csv)
            
        Returns:
            (int or None): Repetition number extracted from filename, or None if pattern not found
        """
        filename = os.path.basename(filepath)
        match = re.search(r'_rep(\d+)', filename)
        if match:
            return int(match.group(1))
        return None

    def _add_repetition_column(self, df, filepath, combined_dfs_list):
        """
        Add a Repetition column to the dataframe based on the filename pattern.
        Modifies the dataframe in place.
        
        Args:
            df (pandas.DataFrame): The dataframe to add the column to
            filepath (str): The file path to extract the repetition number from
            combined_dfs_list (list): List of already processed dataframes (for fallback numbering)
        
        Note:
            If the repetition number cannot be extracted from the filename, falls back to
            sequential numbering. This fallback may produce unexpected results if some files
            match the pattern and others don't within the same set.
        """
        rep_num = self._extract_repetition_number(filepath)
        if rep_num is not None:
            df['Repetition'] = rep_num
        else:
            # Fallback to sequential numbering if pattern not found.
            # Determine the next repetition number based on the highest
            # existing "Repetition" value in the already combined dataframes.
            if self.logger:
                self.logger.warning(f"Could not extract repetition number from {filepath}, using sequential numbering")
            max_rep = 0
            for existing_df in combined_dfs_list:
                try:
                    if 'Repetition' in existing_df.columns:
                        current_max = existing_df['Repetition'].max()
                        try:
                            current_max_int = int(current_max)
                        except (TypeError, ValueError):
                            continue
                        if current_max_int > max_rep:
                            max_rep = current_max_int
                except AttributeError:
                    # Skip any objects in combined_dfs_list that do not
                    # behave like a pandas DataFrame
                    continue
            df['Repetition'] = max_rep + 1

    def get_combined_repetition_dataframes(self, logs_dir, test_name, timestamp):
        """
        Load and combine CSV files from multiple repetitions into pandas dataframes.
        Returns combined dataframes in memory without creating additional CSV files.
        
        Args:
            logs_dir (str): Directory containing the CSV files
            test_name (str): Name of the test (with spaces replaced by underscores)
            timestamp (str): Base timestamp of the test run
            
        Returns:
            dict: Dictionary with combined dataframes
                  {'cpu_df': combined_cpu_dataframe, 'gpu_df': combined_gpu_dataframe}
                  Returns None for dataframes that couldn't be created
        """
        import pandas as pd
        import glob
        
        combined_dataframes = {'cpu_df': None, 'gpu_df': None}
        
        try:
            # Pattern matching for CPU stress CSV files
            cpu_pattern = os.path.join(logs_dir, f"cpuStress_{test_name}_{timestamp}_rep*.csv")
            cpu_files = sorted(glob.glob(cpu_pattern))
            
            if cpu_files:
                combined_cpu_dfs = []
                
                for cpu_file in cpu_files:
                    try:
                        df = pd.read_csv(cpu_file)
                        # Add repetition column to track which repetition this data came from
                        self._add_repetition_column(df, cpu_file, combined_cpu_dfs)
                        combined_cpu_dfs.append(df)
                    except Exception as e:
                        if self.logger:
                            self.logger.warning(f"Failed to read CPU CSV file {cpu_file}: {e}")
                
                if combined_cpu_dfs:
                    # Concatenate all dataframes
                    combined_dataframes['cpu_df'] = pd.concat(combined_cpu_dfs, ignore_index=True)
            
            # Pattern matching for GPU monitoring CSV files (paramMon)
            gpu_pattern = os.path.join(logs_dir, f"paramMon_{test_name}_{timestamp}_rep*.csv")
            gpu_files = sorted(glob.glob(gpu_pattern))
            
            if gpu_files:
                combined_gpu_dfs = []
                
                for gpu_file in gpu_files:
                    try:
                        df = pd.read_csv(gpu_file)
                        # Add repetition column to track which repetition this data came from
                        self._add_repetition_column(df, gpu_file, combined_gpu_dfs)
                        combined_gpu_dfs.append(df)
                    except (IOError, OSError, FileNotFoundError, PermissionError) as e:
                        if self.logger:
                            self.logger.warning(f"Failed to read GPU CSV file {gpu_file}: {e}")
                    except (pd.errors.Error, ValueError) as e:
                        if self.logger:
                            self.logger.error(f"Data processing error in {gpu_file}: {e}")
                        # Continue processing other files
                    except Exception as e:
                        if self.logger:
                            self.logger.error(f"Unexpected error processing {gpu_file}: {type(e).__name__}: {e}")
                        # Let programming errors surface in development
                        raise
                if combined_gpu_dfs:
                    # Concatenate all dataframes
                    combined_dataframes['gpu_df'] = pd.concat(combined_gpu_dfs, ignore_index=True)
            
        except (IOError, OSError, FileNotFoundError, PermissionError) as e:
            if self.logger:
                self.logger.warning(f"File system error combining CSV files: {e}")
            return {'cpu_df': None, 'gpu_df': None}
        except (pd.errors.Error, ValueError) as e:
            if self.logger:
                self.logger.error(f"Data processing error: {e}")
            return {'cpu_df': None, 'gpu_df': None}
        except Exception as e:
            if self.logger:
                self.logger.error(f"Unexpected error in CSV processing: {type(e).__name__}: {e}")
            raise  # Let programming errors surface
        
        return combined_dataframes

    def concatenate_repetition_csvs(self, logs_dir, test_name, timestamp):
        """
        Concatenate CSV files from multiple repetitions into single combined files.
        Finds all CSV files for a given test and combines them chronologically.
        
        Args:
            logs_dir (str): Directory containing the CSV files
            test_name (str): Name of the test (with spaces replaced by underscores)
            timestamp (str): Base timestamp of the test run
            
        Returns:
            dict: Dictionary with paths to combined CSV files
                  {'cpu_csv': path_to_combined_cpu_csv, 'gpu_csv': path_to_combined_gpu_csv}
        """
        import pandas as pd
        import glob
        
        combined_files = {}
        
        try:
            # Pattern matching for CPU stress CSV files
            cpu_pattern = os.path.join(logs_dir, f"cpuStress_{test_name}_{timestamp}_rep*.csv")
            cpu_files = sorted(glob.glob(cpu_pattern))
            
            if cpu_files:
                self.logger.info(f"Found {len(cpu_files)} CPU monitoring CSV files to combine")
                combined_cpu_dfs = []
                
                for cpu_file in cpu_files:
                    try:
                        df = pd.read_csv(cpu_file)
                        # Add repetition column to track which repetition this data came from
                        self._add_repetition_column(df, cpu_file, combined_cpu_dfs)
                        combined_cpu_dfs.append(df)
                        # Log the file and its repetition number
                        rep_num = df['Repetition'].iloc[0] if 'Repetition' in df.columns and len(df) > 0 else 'unknown'
                        self.logger.info(f"  Added {cpu_file} (rep {rep_num})")
                    except Exception as e:
                        self.logger.warning(f"Failed to read CPU CSV file {cpu_file}: {e}")
                
                if combined_cpu_dfs:
                    # Concatenate all dataframes
                    combined_cpu_df = pd.concat(combined_cpu_dfs, ignore_index=True)
                    
                    # Create combined filename
                    combined_cpu_path = os.path.join(logs_dir, f"cpuStress_{test_name}_{timestamp}_combined.csv")
                    combined_cpu_df.to_csv(combined_cpu_path, index=False)
                    combined_files['cpu_csv'] = combined_cpu_path
                    self.logger.info(f"Combined CPU CSV created: {combined_cpu_path}")
            
            # Pattern matching for GPU monitoring CSV files (paramMon)
            gpu_pattern = os.path.join(logs_dir, f"paramMon_{test_name}_{timestamp}_rep*.csv")
            gpu_files = sorted(glob.glob(gpu_pattern))
            
            if gpu_files:
                self.logger.info(f"Found {len(gpu_files)} GPU monitoring CSV files to combine")
                combined_gpu_dfs = []
                
                for gpu_file in gpu_files:
                    try:
                        df = pd.read_csv(gpu_file)
                        # Add repetition column to track which repetition this data came from
                        self._add_repetition_column(df, gpu_file, combined_gpu_dfs)
                        combined_gpu_dfs.append(df)
                        # Log the file and its repetition number
                        rep_num = df['Repetition'].iloc[0] if 'Repetition' in df.columns and len(df) > 0 else 'unknown'
                        self.logger.info(f"  Added {gpu_file} (rep {rep_num})")
                    except Exception as e:
                        self.logger.warning(f"Failed to read GPU CSV file {gpu_file}: {e}")
                
                if combined_gpu_dfs:
                    # Concatenate all dataframes
                    combined_gpu_df = pd.concat(combined_gpu_dfs, ignore_index=True)
                    
                    # Create combined filename
                    combined_gpu_path = os.path.join(logs_dir, f"paramMon_{test_name}_{timestamp}_combined.csv")
                    combined_gpu_df.to_csv(combined_gpu_path, index=False)
                    combined_files['gpu_csv'] = combined_gpu_path
                    self.logger.info(f"Combined GPU CSV created: {combined_gpu_path}")
            
            if not cpu_files and not gpu_files:
                self.logger.info("No repetition CSV files found to combine")
            
        except Exception as e:
            self.logger.error(f"Error concatenating repetition CSV files: {e}")
        
        return combined_files

    def zip_logs_folder(self):
        """Zips all files in the logs folder into a single zip file with a timestamped name, saved in the project root.
        After zipping, removes the original files."""
        project_root = os.path.dirname(os.path.abspath(__file__))
        logs_dir = os.path.abspath(os.path.join(project_root, '..', 'logs'))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_name = f"VerificationTestSuiteLogs_{timestamp}.zip"
        zip_path = os.path.join(os.path.dirname(project_root), zip_name)
        files_to_remove = []
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(logs_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, logs_dir)
                    zipf.write(file_path, arcname)
                    files_to_remove.append(file_path)
        # Remove the files after zipping
        for file_path in files_to_remove:
            try:
                os.remove(file_path)
            except Exception as e:
                self.logger.error(f"Failed to remove {file_path}: {e}")
        # Remove empty folders in logs_dir
        for root, dirs, files in os.walk(logs_dir, topdown=False):
            for d in dirs:
                dir_path = os.path.join(root, d)
                try:
                    if not os.listdir(dir_path):
                        os.rmdir(dir_path)
                except Exception as e:
                    self.logger.error(f"Failed to remove directory {dir_path}: {e}")
        self.logger.info('')
        self.logger.info(f"Logs folder zipped to {zip_path}")

    def tn_type(self, value):
        if value in ('c', 'd', 'a'):
            return value
        try:
            ivalue = int(value)
            if 0 <= ivalue <= 20:
                return ivalue
        except ValueError:
            pass
        # Import argparse locally since it's only used here
        import argparse
        raise argparse.ArgumentTypeError("Test Number (-tn) must be an integer or 'c' or 'd' or 'a'.")
    
    def killParentandChildProcesses(self, parentProcessId):
        """
        Kills a parent process and all its child processes recursively using OS commands.
        Args:
            parentProcessId (int): PID of the parent process.
        """
        # SECURITY: Validate process ID to prevent command injection (raises exception on invalid input)
        validated_pid = self.validate_process_id(parentProcessId)
        
        try:
            # First try to kill child processes, then parent
            # Use subprocess with list arguments to prevent command injection
            try:
                # Use subprocess directly instead of run_command_blocking for safety
                import subprocess # nosec
                result = subprocess.run(['pkill', '-TERM', '-P', str(validated_pid)], 
                                     capture_output=True, text=True, timeout=10)
                # pkill returns non-zero if no processes found, which is normal
            except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError):
                # No child processes or pkill failed, continue to kill parent
                pass
            
            # Try to kill the parent process
            try:
                import subprocess # nosec
                result = subprocess.run(['kill', '-TERM', str(validated_pid)], 
                                     capture_output=True, text=True, timeout=10)
            except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError):
                # Process may have already terminated, which is fine
                pass
        except Exception as e:
            # Only catch process killing exceptions, not validation exceptions
            if self.logger:
                self.logger.warning(f"Process cleanup for PID {validated_pid} encountered issues: {e}")

    def print_progress_bar(self, iteration, total, prefix='', suffix='', length=50, fill='█'):
        """
        Call in a loop to create terminal progress bar
        @params:
            iteration   - Required  : current iteration (Int)
            total       - Required  : total iterations (Int)
            prefix      - Optional  : prefix string (Str)
            suffix      - Optional  : suffix string (Str)
            length      - Optional  : length of the bar (Int)
            fill        - Optional  : fill character (Str)
        """
        if not 0 <= iteration <= total:
            raise ValueError("Invalid iteration value.")

        # Calculate the progress bar
        filled_length = int(length * iteration // total)
        bar = fill * filled_length + ' ' * (length - filled_length)

        # Print the progress bar
        print(f'\r\t\t{prefix} |{bar}| {iteration}/{total} {suffix}', end=' ')

    def installPTAT(self):
        """
        Description:
            Checks to see if PTAT is installed. If it is not installed, the script will install it.
        Returns:
            str: The absolute path to the PTAT installation directory.
        """ 
        self.logger.subheader('Checking PTAT installation')
        toolsFolder = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools'))
        
        # Helper function to find PTAT installation
        def find_ptat_installation():
            for root, dirs, files in os.walk(toolsFolder):
                relative_path = os.path.relpath(root, toolsFolder)
                level = len(relative_path.split(os.sep)) if relative_path != '.' else 0
                if level == 1 and 'PTAT' in dirs:
                    return os.path.join(root, 'PTAT')
            return None

        # Check if PTAT is already installed
        self.ptat_extraction_path = find_ptat_installation()
        
        if self.ptat_extraction_path:
            self.logger.info("PTAT already installed")
            return self.ptat_extraction_path

        # Install PTAT if not found
        self.logger.info("PTAT is not installed, installing it now, this will take a few mins...")
        
        # Find PTAT installer
        targz_list = [os.path.join(path, name) for path, subdirs, files in os.walk(toolsFolder) 
                     for name in files if name.endswith('.tar.gz') and '637673' in name]
        
        if not targz_list:
            raise Exception('PTAT could not be installed (no installer found)... EXITING')
            
        targz_path = targz_list[0]
        untar_to = os.path.join(os.path.dirname(targz_path), 'PTAT')
        
        # Create extraction directory
        os.makedirs(untar_to, exist_ok=True)
        self.ptat_extraction_path = untar_to
        
        # Check if build tools are available
        try:
            self.run_command_blocking('which make')
            self.run_command_blocking('which gcc')
        except Exception as e:
            raise Exception(f'PTAT installation requires build tools (make, gcc) which are not available: {e}')
        
        # Extract and install
        cwd = os.getcwd()
        try:
            self.run_command_blocking(f'tar -xvf "{targz_path}" -C "{untar_to}"')
            
            os.chdir(os.path.join(untar_to, 'driver', 'ptusys'))
            self.run_command_blocking('make clean all')
            try:
                # Check if ptusys module is already loaded
                try:
                    self.run_command_blocking('lsmod | grep ptusys', use_shell=True)
                    self.logger.info("PTAT kernel module (ptusys) already loaded, skipping installation")
                except RuntimeError:
                    # Module not loaded, try to install it
                    self.run_command_blocking('make install')
            except Exception as e:
                self.logger.warning(f"Failed to install PTAT driver, continuing anyway: {e}")
            self.startPtatMonitoring(1000)
            sleep(180)
            self.stopPtatMonitoring()
            self.cleanPtatLogs()
        finally:
            os.chdir(cwd)

        # Verify installation
        self.ptat_extraction_path = find_ptat_installation()
        if self.ptat_extraction_path:
            self.logger.info(f"PTAT installed successfully at: {self.ptat_extraction_path}")
            return self.ptat_extraction_path
        else:
            raise Exception('PTAT could not be installed ... EXITING')

    def _copy_dgdiag_config(self, toolsFolder, dgdiag_installation_path, version=None):
        """
        Helper function to copy dgdiagconfig.bin to the DGDiag installation directory.
        
        Args:
            toolsFolder (str): Path to the tools folder
            dgdiag_installation_path (str): Path to the DGDiag installation directory
            version (str): DGDiag version to use for finding config file
        """
        try:
            # Use provided version or fallback to default
            version_folder = f'DGDiag_{version}' if version else 'DGDiag_3.10.2'
            config_source = os.path.join(toolsFolder, version_folder, 'dgdiagconfig.bin')
            if os.path.exists(config_source):
                config_dest = os.path.join(dgdiag_installation_path, 'dgdiagconfig.bin')
                shutil.copy2(config_source, config_dest)
            else:
                self.logger.warning(f"dgdiagconfig.bin not found at: {config_source}")
        except Exception as e:
            self.logger.warning(f"Failed to copy dgdiagconfig.bin: {e}")

    def installDGDiag(self, required_version=None, verbose=True):
        """
        Description:
            Checks to see if DGDiag is installed with the correct version. 
            If not installed or wrong version, uninstalls and installs the correct version.
        Args:
            required_version (str): The required DGDiag version (e.g., '3.10.2')
            verbose (bool): If True, shows detailed installation progress. If False, runs silently.
        Returns:
            str: The absolute path to the DGDiag installation directory.
        """ 
        if verbose:
            self.logger.subheader('Checking DGDiag installation')
        toolsFolder = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools'))
        
        # Helper function to find DGDiag installation
        def find_dgdiag_installation():
            # Check possible DGDiag installation locations
            possible_paths = [
                "/opt/Intel Corporation/DGDiagTool",
                "/opt/Intel Corporation/DGDiagTool_Internal"
            ]
            
            for dgdiag_path in possible_paths:
                dgdiag_binary = os.path.join(dgdiag_path, "DGDiagTool")
                if os.path.exists(dgdiag_binary):
                    return dgdiag_path
                    
            if verbose:
                self.logger.warning("DGDiag not found in any expected location")
            return None
        
        # Helper function to get installed DGDiag version
        def get_installed_dgdiag_version(dgdiag_path):
            try:
                dgdiag_binary = os.path.join(dgdiag_path, "DGDiagTool")
                
                # First check if binary exists and is executable
                if not os.path.exists(dgdiag_binary):
                    if verbose:
                        self.logger.warning(f"DGDiag binary not found at: {dgdiag_binary}")
                    return None
                
                # Change to DGDiag directory and use relative path like command line
                original_cwd = os.getcwd()
                os.chdir(dgdiag_path)
                
                version_output = self.run_command_blocking('./DGDiagTool -v')
              
                # Always restore original directory
                os.chdir(original_cwd)
                
                # Parse version from output (format: "DGDiagTool - X.Y.Z" or "DGDiagTool - Internal Version X.Y.Z")
                version_patterns = [
                    r'DGDiagTool\s*-\s*Internal\s+Version\s+(\d+\.\d+\.\d+)',  # Internal version format
                    r'DGDiagTool\s*-\s*(\d+\.\d+\.\d+)',  # Standard version format
                ]
                
                detected_version = None
                for pattern in version_patterns:
                    version_match = re.search(pattern, version_output)
                    if version_match:
                        detected_version = version_match.group(1)
                        break
                
                if detected_version:
                    return detected_version
                else:
                    if verbose:
                        self.logger.warning(f"Could not parse version from output: {version_output.strip()}")
                    
            except Exception as e:
                self.logger.warning(f"Failed to get DGDiag version: {e}")
            return None
        
        # Helper function to uninstall DGDiag
        def uninstall_dgdiag():
            try:
                if verbose:
                    self.logger.info("Uninstalling existing DGDiag installation...")
                
                # Check both possible installation locations for uninstaller
                uninstaller_paths = [
                    "/opt/Intel Corporation/DGDiagTool/UnInstall/DGDiagUnInstaller.sh",
                    "/opt/Intel Corporation/DGDiagTool_Internal/UnInstall/DGDiagUnInstaller.sh"
                ]
                
                uninstaller_found = False
                for uninstaller_path in uninstaller_paths:
                    if os.path.exists(uninstaller_path):
                        if verbose:
                            self.logger.info(f"Using uninstaller at: {uninstaller_path}")
                        self.run_command_blocking(f'sudo bash "{uninstaller_path}"')
                        uninstaller_found = True
                        break
                
                if not uninstaller_found:
                    # Fallback: remove both possible directories manually
                    if verbose:
                        self.logger.info("No uninstaller found, removing directories manually...")
                    for dir_path in ["/opt/Intel Corporation/DGDiagTool", "/opt/Intel Corporation/DGDiagTool_Internal"]:
                        if os.path.exists(dir_path):
                            if verbose:
                                self.logger.info(f"Removing: {dir_path}")
                            self.run_command_blocking(f'sudo rm -rf "{dir_path}"')
                
                if verbose:
                    self.logger.info("DGDiag uninstalled successfully")
                return True
            except Exception as e:
                self.logger.error(f"Failed to uninstall DGDiag: {e}")
                return False

        # Check if DGDiag is already installed
        self.dgdiag_extraction_path = find_dgdiag_installation()
        
        # Check version compatibility if DGDiag is installed
        if self.dgdiag_extraction_path and required_version:
            installed_version = get_installed_dgdiag_version(self.dgdiag_extraction_path)
            if installed_version == required_version:
                if verbose:
                    self.logger.info(f"DGDiag version {installed_version} already installed (matches required version)")
                # Copy dgdiagconfig.bin to the installation directory
                self._copy_dgdiag_config(toolsFolder, self.dgdiag_extraction_path, required_version)
                return self.dgdiag_extraction_path
            elif installed_version:
                if verbose:
                    self.logger.warning(f"DGDiag version {installed_version} installed, but version {required_version} required")
                if not uninstall_dgdiag():
                    raise Exception(f'Failed to uninstall DGDiag version {installed_version}')
                self.dgdiag_extraction_path = None  # Reset path after uninstall
            else:
                if verbose:
                    self.logger.warning("DGDiag installed but version could not be determined")
                if not uninstall_dgdiag():
                    raise Exception('Failed to uninstall DGDiag with unknown version')
                self.dgdiag_extraction_path = None  # Reset path after uninstall
        elif self.dgdiag_extraction_path:
            if verbose:
                self.logger.info("DGDiag already installed (version check skipped)")
            # Copy dgdiagconfig.bin to the installation directory
            self._copy_dgdiag_config(toolsFolder, self.dgdiag_extraction_path, required_version)
            return self.dgdiag_extraction_path

        # Install DGDiag if not found or after uninstall
        version_msg = f" version {required_version}" if required_version else ""
        if verbose:
            self.logger.info(f"DGDiag{version_msg} is not installed, installing it now, this will take a few mins...")
        
        # Determine installation paths based on required version
        if required_version:
            version_folder = f'DGDiag_{required_version}'
            zip_filename = f'dgdiag_{required_version}.zip'
        else:
            # Fallback to hardcoded version if not specified
            version_folder = 'DGDiag_3.10.2'
            zip_filename = 'dgdiag_3.10.2.zip'
        
        # Find DGDiag zip file
        zip_path = os.path.join(toolsFolder, version_folder, zip_filename)
        
        if not os.path.exists(zip_path):
            raise Exception('DGDiag could not be installed (zip file not found)... EXITING')
            
        unzip_to = os.path.dirname(zip_path)
        
        # Create extraction directory
        os.makedirs(unzip_to, exist_ok=True)
        self.dgdiag_extraction_path = unzip_to
        
        # Extract and install
        cwd = os.getcwd()
        try:
            if verbose:
                self.logger.info(f"Extracting DGDiag from: {zip_path}")
            # Extract zip file
            extract_output = self.run_command_blocking(f'unzip -o "{zip_path}" -d "{unzip_to}"')
            if verbose:
                self.logger.info(f"Extract output: {extract_output.strip()}")
            
            # Path to the installer script (inside the extracted version folder)
            extracted_folder = f'dgdiag_{required_version}' if required_version else 'dgdiag_3.10.2'
            installer_script = os.path.join(unzip_to, extracted_folder, 'Linux', 'DGDiagInstaller.sh')
            
            if verbose:
                self.logger.info(f"Looking for installer script at: {installer_script}")
            if not os.path.exists(installer_script):
                # List contents to debug
                self.logger.error(f"Contents of {unzip_to}:")
                try:
                    ls_output = self.run_command_blocking(f'ls -la "{unzip_to}"')
                    self.logger.error(ls_output)
                except (subprocess.CalledProcessError, OSError):
                    pass
                raise Exception(f'DGDiag installer script not found at: {installer_script}')
            
            # Convert to unix format and make executable
            if verbose:
                self.logger.info("Converting installer script to unix format and making executable")
            try:
                self.run_command_blocking(f'dos2unix "{installer_script}"')
            except Exception as e:
                if verbose:
                    self.logger.warning(f"dos2unix failed (may not be installed): {e}")
                
            # Use helper method to make script executable
            if not self.make_script_executable(installer_script):
                # Fallback to chmod command if helper method fails
                self.run_command_blocking(f'chmod +x "{installer_script}"')
            
            # Change to installer directory and run installation
            installer_dir = os.path.dirname(installer_script)
            if verbose:
                self.logger.info(f"Changing to installer directory: {installer_dir}")
            os.chdir(installer_dir)
            
            # Run the installer with output capture
            if verbose:
                self.logger.info("Running DGDiag installer...")
            install_output = self.run_command_blocking('./DGDiagInstaller.sh')
            if verbose:
                self.logger.info(f"Installer output: {install_output.strip()}")
            
        except Exception as e:
            self.logger.error(f"DGDiag installation failed: {e}")
            raise Exception(f'DGDiag could not be installed: {e}')
        finally:
            os.chdir(cwd)

        # Verify installation
        if verbose:
            self.logger.info("Verifying DGDiag installation...")
        self.dgdiag_extraction_path = find_dgdiag_installation()
        if self.dgdiag_extraction_path:
            if verbose:
                self.logger.info(f"DGDiag found at: {self.dgdiag_extraction_path}")
            
            # Verify the version if required
            if required_version:
                installed_version = get_installed_dgdiag_version(self.dgdiag_extraction_path)
                if installed_version != required_version:
                    if verbose:
                        self.logger.warning(f"Installation verification failed: expected {required_version}, got {installed_version}")
            
            # Copy dgdiagconfig.bin to the installation directory
            self._copy_dgdiag_config(toolsFolder, self.dgdiag_extraction_path, required_version)
            
            if verbose:
                self.logger.info(f"DGDiag installed successfully at: {self.dgdiag_extraction_path}")
            return self.dgdiag_extraction_path
        else:
            # Debug: Check if installation directory exists but binary is missing
            install_dir = "/opt/Intel Corporation/DGDiagTool"
            if os.path.exists(install_dir):
                self.logger.error(f"Installation directory exists but DGDiag not found. Contents:")
                try:
                    ls_output = self.run_command_blocking(f'ls -la "{install_dir}"')
                    self.logger.error(ls_output)
                except (subprocess.CalledProcessError, OSError):
                    pass
            else:
                self.logger.error(f"Installation directory does not exist: {install_dir}")
            
            raise Exception('DGDiag could not be installed ... EXITING')
    
    def is_b70_system(self) -> bool:
        """
        Check if the system has Intel Arc B70 GPUs.
        
        Returns:
            bool: True if B70 GPUs are detected, False otherwise
        """
        try:
            from .deviceManager import DeviceManager
            device_mgr = DeviceManager(self.logger)
            device_mgr.discover_devices(silent=True)
            return device_mgr.gpu_did == 'e223' and device_mgr.gpu_num > 0
        except Exception as e:
            self.logger.warning(f"GPU detection failed: {e}")
            return False
    
    def get_detected_gpu_memory_gb(self) -> Optional[float]:
        """
        Get total GPU memory in GB for all detected Intel Arc B70 GPUs.
        
        Returns:
            Optional[float]: Total GPU memory in GB if B70s detected, None otherwise
        """
        try:
            from .deviceManager import DeviceManager
            device_mgr = DeviceManager(self.logger)
            device_mgr.discover_devices(silent=True)
            
            # Check if we have B70 GPUs (DID e223)
            if device_mgr.gpu_did != 'e223' or device_mgr.gpu_num == 0:
                return None
            
            gpu_count = device_mgr.gpu_num
            
            # Try to get memory info using DGDiag
            try:
                import re
                
                # Install DGDiag if needed  
                dgdiag_dir = self.installDGDiag(verbose=False)
                dgdiag_tool = os.path.join(dgdiag_dir, 'DGDiagTool')
                
                if not os.path.exists(dgdiag_tool):
                    self.logger.warning("DGDiag tool not found, using B70 default memory estimate")
                    return gpu_count * 12.0  # B70 typically has 12GB of memory
                
                # Query memory for each GPU instance using correct syntax
                total_gpu_memory_gb = 0.0
                successful_queries = 0
                
                for instance in range(1, gpu_count + 1):
                    try:
                        # Use correct DGDiag syntax: -MEMORY.UTIL.MemInfo inst=X
                        cmd = [dgdiag_tool, '-MEMORY.UTIL.MemInfo', f'inst={instance}']
                        
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=dgdiag_dir)
                        
                        if result.returncode == 0 and result.stdout:
                            # Parse memory information from DGDiag output - look for "Memory Size : XX GB"
                            memory_match = re.search(r'Memory\s+Size\s*:\s*(\d+)\s*GB', result.stdout, re.IGNORECASE)
                            if memory_match:
                                memory_gb = float(memory_match.group(1))
                                total_gpu_memory_gb += memory_gb
                                successful_queries += 1
                            else:
                                self.logger.warning(f"Could not parse memory from DGDiag GPU {instance} output, using 12 GB fallback")
                                total_gpu_memory_gb += 12.0
                                successful_queries += 1
                        else:
                            self.logger.warning(f"DGDiag GPU {instance} command failed with return code {result.returncode}, using 12 GB fallback") 
                            if result.stderr:
                                self.logger.warning(f"DGDiag GPU {instance} error: {result.stderr}")
                            total_gpu_memory_gb += 12.0
                            
                    except subprocess.TimeoutExpired:
                        self.logger.warning(f"DGDiag GPU {instance} command timed out, using 12 GB fallback")
                        total_gpu_memory_gb += 12.0
                    except Exception as e:
                        self.logger.warning(f"DGDiag GPU {instance} command failed: {e}, using 12 GB fallback")
                        total_gpu_memory_gb += 12.0
                
                if successful_queries > 0:
                    return total_gpu_memory_gb
                else:
                    self.logger.warning("All DGDiag queries failed, using B70 default estimate")
                    return gpu_count * 12.0
                
            except Exception as e:
                # DGDiag approach failed completely, use default calculation
                self.logger.warning(f"DGDiag memory detection failed: {e}, using B70 default estimate")
                return gpu_count * 12.0
                
        except Exception as e:
            # Complete failure - return None
            self.logger.warning(f"GPU memory detection failed: {e}")
            return None
    
    def get_system_memory_speed_mhz(self) -> Optional[float]:
        """
        Get system RAM memory speed in MHz from system information.
        
        Returns:
            Optional[float]: System memory speed in MHz if detected, None otherwise
        """
        try:
            # Method 1: Try dmidecode (most reliable but typically requires root)
            try:
                # If already running as root, call dmidecode directly; otherwise use sudo in non-interactive mode.
                if hasattr(os, "geteuid") and os.geteuid() == 0:
                    dmidecode_cmd = ['dmidecode', '-t', 'memory']
                else:
                    dmidecode_cmd = ['sudo', '-n', 'dmidecode', '-t', 'memory']
                result = subprocess.run(dmidecode_cmd,
                                        capture_output=True, text=True, timeout=10)
                
                if result.returncode == 0 and result.stdout:
                    import re
                    # Look for different speed patterns in dmidecode output
                    speeds_found = []
                    
                    # Pattern 1: "Configured Memory Speed: XXXX MT/s" or "Configured Memory Speed: XXXX MHz"  (most reliable for actual speed)
                    configured_speeds = re.findall(r'Configured\s+Memory\s+Speed:\s*(\d+)\s*(MT/s|MHz)', 
                                                   result.stdout, re.IGNORECASE)
                    for speed_str, unit in configured_speeds:
                        speed = int(speed_str)
                        if 1600 <= speed <= 8000:
                            speeds_found.append(speed)
                    
                    # Pattern 2: "Speed: XXXX MT/s" or "Speed: XXXX MHz" (but only if clear context and reasonable range)
                    if not speeds_found:  # Only try if configured speed not found
                        speed_lines = result.stdout.split('\n')
                        for i, line in enumerate(speed_lines):
                            speed_match = re.search(r'^\s*Speed:\s*(\d+)\s*(MT/s|MHz)', line, re.IGNORECASE)
                            if speed_match:
                                speed = int(speed_match.group(1))
                                unit = speed_match.group(2)
                                # Only accept if in reasonable DDR speed range and not preceded by irrelevant context
                                if 1600 <= speed <= 8000:
                                    # Check if this appears to be memory-related (not CPU or other component)
                                    context_above = ' '.join(speed_lines[max(0, i-5):i]).lower()
                                    if any(keyword in context_above for keyword in ['memory', 'dimm', 'ddr', 'device']):
                                        speeds_found.append(speed)
                    
                    # Pattern 3: Look for DDR type with speed "DDR5-5600" or "DDR5 5600" style (fallback)
                    if not speeds_found:
                        ddr_matches = re.findall(r'DDR[0-9]+-?(\d{4,5})', result.stdout, re.IGNORECASE)
                        for speed_str in ddr_matches:
                            speed = int(speed_str)
                            if 1600 <= speed <= 8000:
                                speeds_found.append(speed)
                    
                    if speeds_found:
                        # Return the most common speed, or max if all different
                        from collections import Counter
                        speed_counts = Counter(speeds_found)
                        most_common_speed = speed_counts.most_common(1)[0][0]
                        return float(most_common_speed)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
                pass  # Try alternative method
                if hasattr(self, 'logger') and self.logger:
                    self.logger.debug(f"dmidecode method failed: {e}")
                pass  # Try alternative method
            
            # Method 2: Try lshw (may not require sudo but less reliable)
            try:
                # Try lshw memory speed detection
                
                # Try multiple lshw approaches to find system RAM (not cache)
                lshw_commands = [
                    ['lshw', '-short', '-C', 'memory'],  # Short format, memory class
                    ['lshw', '-C', 'memory'],            # Full format, memory class  
                    ['lshw'],                            # Full system scan
                ]
                
                speeds_found = []
                
                for cmd_idx, cmd in enumerate(lshw_commands):
                    try:
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                        
                        if result.returncode == 0 and result.stdout:
                            if hasattr(self, 'logger') and self.logger:
                                self.logger.debug(f"DEBUG: lshw command {cmd_idx + 1} ({' '.join(cmd)}) executed successfully")
                                if cmd_idx == 0 and len(result.stdout) < 1000:  # Show short output fully
                                    self.logger.debug(f"DEBUG: lshw short output:\n{result.stdout}")
                                else:
                                    # Show relevant excerpt for full outputs
                                    lines = result.stdout.split('\n')
                                    memory_lines = [line for line in lines if any(keyword in line.lower() for keyword in ['memory', 'dimm', 'bank', 'ram', 'ddr'])]
                                    if memory_lines:
                                        self.logger.debug(f"DEBUG: lshw memory-related lines ({len(memory_lines)} found):\n" + '\n'.join(memory_lines[:10]))
                            
                            import re
                            
                            # Look for different memory speed patterns
                            # Pattern 1: DDR type with speed "DDR5-5600" or "DDR5 5600"
                            ddr_matches = re.findall(r'DDR[0-9]+-?(\d{4,5})', result.stdout, re.IGNORECASE)
                            for speed_str in ddr_matches:
                                speed = int(speed_str)
                                if 1600 <= speed <= 8000:
                                    speeds_found.append(speed)
                                    if hasattr(self, 'logger') and self.logger:
                                        self.logger.debug(f"DEBUG: Found DDR speed: {speed} MHz")
                            
                            # Pattern 2: Look for explicit memory bank information with speeds
                            # lshw sometimes shows memory banks like "*-bank:0", "*-bank:1" with clock info
                            lines = result.stdout.split('\n')
                            in_memory_bank = False
                            current_bank_lines = []
                            
                            for line in lines:
                                # Start of a memory bank section
                                if re.match(r'\s*\*-bank:', line) or 'description: DIMM' in line:
                                    in_memory_bank = True
                                    current_bank_lines = [line]
                                # End of section (next component or empty line after bank info)
                                elif in_memory_bank and (line.strip() == '' or re.match(r'\s*\*-[^:]', line)):
                                    # Process current bank
                                    bank_text = '\n'.join(current_bank_lines)
                                    
                                    # Look for clock information in this bank
                                    clock_matches = re.findall(r'clock:\s*(\d+)MHz', bank_text, re.IGNORECASE)
                                    for clock_str in clock_matches:
                                        speed = int(clock_str)
                                        if 1600 <= speed <= 8000:
                                            speeds_found.append(speed)
                                            if hasattr(self, 'logger') and self.logger:
                                                self.logger.debug(f"DEBUG: Found memory bank clock: {speed} MHz")
                                        elif 800 <= speed <= 3200:  # Base clock - try doubling
                                            doubled_speed = speed * 2
                                            if 1600 <= doubled_speed <= 8000:
                                                speeds_found.append(doubled_speed)
                                                if hasattr(self, 'logger') and self.logger:
                                                    self.logger.debug(f"DEBUG: Doubled memory bank base clock {speed} to {doubled_speed} MHz")
                                    
                                    # Look for size information to confirm this is system RAM
                                    size_matches = re.findall(r'size:\s*(\d+)([KMGT])iB', bank_text, re.IGNORECASE)
                                    bank_size_gb = 0
                                    for size_str, unit in size_matches:
                                        size = int(size_str)
                                        if unit.upper() == 'G':
                                            bank_size_gb = size
                                        elif unit.upper() == 'M':
                                            bank_size_gb = size / 1024
                                        # If we found a reasonable size (≥1GB), this is likely system RAM
                                        if bank_size_gb >= 1:
                                            if hasattr(self, 'logger') and self.logger:
                                                self.logger.debug(f"DEBUG: Confirmed system RAM bank with size {bank_size_gb:.1f} GB")
                                    
                                    in_memory_bank = False
                                    current_bank_lines = []
                                elif in_memory_bank:
                                    current_bank_lines.append(line)
                            
                            # If we found speeds with this command, don't try the others
                            if speeds_found:
                                break
                                
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
                        if hasattr(self, 'logger') and self.logger:
                            self.logger.debug(f"DEBUG: lshw command {cmd_idx + 1} failed: {e}")
                        continue
                
                if speeds_found:
                    # Return the most common valid speed found
                    from collections import Counter
                    speed_counts = Counter(speeds_found)  
                    most_common_speed = speed_counts.most_common(1)[0][0]
                    if hasattr(self, 'logger') and self.logger:
                        self.logger.debug(f"DEBUG: lshw: Selected speed {most_common_speed} MHz (most common from {len(speeds_found)} valid speeds: {speeds_found})")
                    return float(most_common_speed)
                else:
                    if hasattr(self, 'logger') and self.logger:
                        self.logger.debug("DEBUG: lshw: No system RAM speeds found with any command variant")
                        
            except Exception as e:
                if hasattr(self, 'logger') and self.logger:
                    self.logger.debug(f"DEBUG: lshw method failed: {e}")
                pass  # Try alternative method
            
            # Method 3: Check /sys/devices/system/edac/mc/mc0/dimm0/dimm_mem_type (limited info)
            try:
                import glob
                dimm_paths = glob.glob('/sys/devices/system/edac/mc/mc*/dimm*/')
                
                for dimm_path in dimm_paths:
                    try:
                        dimm_type_file = os.path.join(dimm_path, 'dimm_mem_type')
                        if os.path.exists(dimm_type_file):
                            with open(dimm_type_file, 'r') as f:
                                dimm_type = f.read().strip()
                                # Try to extract speed from DIMM type (e.g., "DDR5-5600")
                                import re
                                speed_match = re.search(r'DDR[0-9]+-?(\d+)', dimm_type, re.IGNORECASE)
                                if speed_match:
                                    speed = int(speed_match.group(1))
                                    if 1600 <= speed <= 8000:
                                        return float(speed)
                    except (IOError, OSError):
                        continue
            except Exception as e:
                if hasattr(self, 'logger') and self.logger:
                    self.logger.debug(f"EDAC memory speed detection failed: {e}")
            
            return None
            
        except Exception as e:
            if hasattr(self, 'logger') and self.logger:
                self.logger.warning(f"System memory speed detection failed: {e}")
                # Also provide some debug info about what methods were attempted
                self.logger.debug("Attempted memory speed detection methods: dmidecode, lshw, /sys/devices/system/edac")
            return None
    
    def print_system_info_summary(self, gpu_instance=1):
        """
        Print a comprehensive system information summary using DGDiag commands.
        
        Args:
            gpu_instance (int): GPU instance number to query (default: 1)
        
        Returns:
            dict: The collected system information dict, or None if collection failed
        """
        try:
            if self.sysinfo_collector is not None:
                self.sysinfo_collector.print_system_summary(gpu_instance=gpu_instance)
                # Also return the data for potential use
                return self.sysinfo_collector.collect_all_info(gpu_instance)
            else:
                self.logger.warning("System info collector not available - DGDiag may not be installed")
                return None
        except Exception as e:
            self.logger.warning(f"Failed to collect system information: {e}")
            return None
        
    def cleanPtatLogs(self):
        """
        Delete all CSV files in PTAT extraction path that end with 'ptatmon.csv' or 'ptatmsg.csv'.
        """
        if self.ptat_extraction_path is not None:
            if not os.path.exists(self.ptat_extraction_path):
                self.logger.warning(f"PTAT extraction path does not exist: {self.ptat_extraction_path}")
                return
            
            try:
                # Walk through all directories and subdirectories in PTAT extraction path
                for root, dirs, files in os.walk(self.ptat_extraction_path):
                    for file in files:
                        # Check if file ends with target patterns
                        if file.endswith('ptatmon.csv') or file.endswith('ptatmsg.csv'):
                            file_path = os.path.join(root, file)
                            os.remove(file_path)
            except Exception as e:
                self.logger.error(f"Error during PTAT CSV cleanup: {e}")
        else:
            self.logger.warning("PTAT extraction path is not set. Cannot clean PTAT logs.")
    
    def startPtatMonitoring(self, sampling_rate):
        cwd = os.getcwd()
       
        os.chdir(self.ptat_extraction_path)
        self.logs_folder = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'logs'))
        self.ptatMonProcess = self.run_command_non_blocking(f'./ptat -mon -l 0 -i {sampling_rate * 1000} -ts -wf -nd -y -log -logdir {self.logs_folder} -csv')
        
        os.chdir(cwd)

        return self.ptat_extraction_path 

    def stopPtatMonitoring(self):      
        success = True
        if self.ptatMonProcess:
            self.killParentandChildProcesses(self.ptatMonProcess.process.pid)
            self.ptatMonProcess.terminate_process()
            success = not self.ptatMonProcess.is_running()
        else:
            self.logger.warning('PTAT Monitoring process not found.')
        return success
    
    def make_script_executable(self, script_path):
        """
        Ensure a script file is executable. Checks if file exists and has execute permissions,
        and makes it executable if needed using comprehensive permissions (0o750).
        
        Args:
            script_path (str): Absolute or relative path to the script file
            
        Returns:
            bool: True if script is/was made executable, False if file doesn't exist or error occurred
        """
        try:
            if not os.path.exists(script_path):
                self.logger.warning(f"Script not found: {script_path}")
                return False
                
            # Check if script is already executable for current user
            current_mode = os.stat(script_path).st_mode
            if current_mode & 0o100:  # Check if user execute bit is set
                return True  # Already executable for user
                
            # Make script executable with comprehensive permissions (rwxr-x---)
            # Same permission model as DGDiag tools for consistency
            os.chmod(script_path, 0o750)
            return True
            
        except Exception as e:
            self.logger.warning(f"Failed to make script executable {script_path}: {e}")
            return False

    def ensure_common_tools_executable(self):
        """
        Ensure common VTS tools and scripts are executable.
        This method should be called during VTS initialization to prevent permission issues.
        
        Returns:
            bool: True if all existing tools were made executable or were already executable.
                  Missing tools are counted and logged but do not cause this function to return False.
        """
        success = True
        
        # Project root and tools directory
        current_dir = os.path.dirname(__file__)
        project_root = current_dir
        while not os.path.exists(os.path.join(project_root, 'tools')) and os.path.dirname(project_root) != project_root:
            project_root = os.path.dirname(project_root)
        tools_dir = os.path.join(project_root, 'tools')
        
        # List of common tools and scripts that need to be executable
        executable_tools = [
            # Tools directory
            os.path.join(tools_dir, 'cpuStress.sh'),
            os.path.join(tools_dir, 'runBwTest.sh'),
            os.path.join(tools_dir, 'infoCollect.sh'),
            os.path.join(tools_dir, 'ze_bandwidth'),
            os.path.join(tools_dir, 'memory_benchmark_l0'),
            
            # Common directory
            os.path.join(current_dir, 'deviceDetect.sh'),
            
            # Reset test content
            os.path.join(project_root, 'gpu', 'bmg', 'tests', 'reset', 'systemReboot.sh'),
            os.path.join(project_root, 'gpu', 'bmg', 'tests', 'reset', 'content', 'ze_peak'),
            os.path.join(project_root, 'gpu', 'bmg', 'tests', 'reset', 'content', 'LTSSMtool'),
            
            # XPUM test script
            os.path.join(project_root, 'gpu', 'bmg', 'tests', 'xpum', 'xpumTest.sh'),
            
            # Optional transcode script (may not be used currently)
            os.path.join(project_root, 'gpu', 'bmg', 'tests', 'transcode', 'transcode_test.sh'),
        ]
        
        made_executable_count = 0
        already_executable_count = 0
        missing_count = 0
        missing_tools = []
        chmod_failed_tools = []
        
        for tool_path in executable_tools:
            if os.path.exists(tool_path):
                # Check if already executable
                current_mode = os.stat(tool_path).st_mode
                if current_mode & 0o100:
                    already_executable_count += 1
                elif self.make_script_executable(tool_path):
                    made_executable_count += 1
                else:
                    success = False
                    chmod_failed_tools.append(tool_path)
            else:
                missing_count += 1
                missing_tools.append(tool_path)
        
        # Log summary
        if self.logger:
            self.logger.info(f"Common tools permission check: {already_executable_count} already executable, {made_executable_count} made executable, {missing_count} missing")
            
            if made_executable_count > 0:
                self.logger.pass_msg(f"✓ Made {made_executable_count} tools executable")
            
            if missing_count > 0:
                self.logger.warning(f"⚠ {missing_count} tools not found (may not be needed for current test)")
                # Log which specific tools are missing to aid troubleshooting
                msg = "Missing common tools (not found on filesystem): " + ", ".join(missing_tools)
                if hasattr(self.logger, "debug"):
                    self.logger.debug(msg)
                else:
                    self.logger.warning(msg)
            
            if chmod_failed_tools:
                # Log tools for which chmod failed
                msg = "Failed to make common tools executable (permission or filesystem issue): " + ", ".join(chmod_failed_tools)
                if hasattr(self.logger, "debug"):
                    self.logger.debug(msg)
                else:
                    self.logger.warning(msg)
        
        return success
    
    def checkRequiredToolsInstalled(self, python_packages, linux_libraries):
        """
        Check if all required tools and dependencies are installed for BMG qualification tests.
        Adapted from Gaudi utils for BMG-specific requirements.
        """
        self.logger.subheader('REQUIREMENTS VERIFICATION')
        
        # Check Python3 in use
        self.logger.info("Checking Python3 in use...")
        python3_in_use = self._check_python3()
        
        # Check Linux libraries installation
        self.logger.info("Checking Linux libraries installation...")
        linux_libraries_installed = not self._check_linux_libraries(linux_libraries)
        
        # Install missing Linux libraries if needed
        if not linux_libraries_installed:
            linux_libraries_installed = self._install_missing_linux_libraries(linux_libraries)
        
        # Check Python packages installation
        self.logger.info("Checking Python packages installation...")
        python_packages_installed = not self._check_python_packages(python_packages)
        
        # Install missing Python packages if needed
        if not python_packages_installed:
            python_packages_installed = self._install_missing_python_packages(python_packages)
        
        if not (python3_in_use and linux_libraries_installed and python_packages_installed):
            raise Exception('Requirements not met, exiting out of the verification test suite...')
        
        self.logger.info("All requirements verified successfully!")
    
    def _check_python3(self):
        """Check if Python 3 is in use"""
        import sys
        if not sys.version.startswith('3.'):
            self.logger.fail_msg("\tPython3 is not in use")
            return False
        else:
            self.logger.pass_msg("\tPython3 is in use")
            return True
    
    def _check_python_packages(self, python_packages):
        """Check if required Python packages are installed"""
        check_python_package_flag = False
        
        for python_package in python_packages:
            if self._is_python_package_installed(python_package):
                self.logger.pass_msg(f"\t{python_package} is installed")
            else:
                self.logger.fail_msg(f"\t{python_package} is not installed")
                check_python_package_flag = True
        
        return check_python_package_flag
    
    def _check_linux_libraries(self, linux_libraries):
        """Check if required Linux libraries are installed (Ubuntu only)"""
        check_linux_library_flag = False
        
        for library in linux_libraries:
            if self._is_linux_library_installed(library):
                self.logger.pass_msg(f"\t{library} is installed")
            else:
                self.logger.fail_msg(f"\t{library} is not installed")
                check_linux_library_flag = True
        
        return check_linux_library_flag
    
    def _is_linux_library_installed(self, library_name):
        """Check if a Linux library is installed using dpkg (Ubuntu)"""
        try:
            result = subprocess.run(
                ['dpkg', '-l', library_name],
                capture_output=True,
                text=True,
                check=False
            )
            # Check if package is installed (status 'ii')
            return result.returncode == 0 and 'ii' in result.stdout
        except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError):
            return False
    
    def _install_missing_linux_libraries(self, linux_libraries):
        """Install missing Linux libraries using apt (Ubuntu only)"""
        self.logger.info("Installing missing Linux libraries...")
        
        # First update package list
        try:
            self.logger.info("\tUpdating package list...")
            subprocess.run(['sudo', 'apt', 'update'], 
                         check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to update package list. Error: {e.stderr}")
            return False
        
        # Install missing libraries
        all_installed = True
        for library in linux_libraries:
            if not self._is_linux_library_installed(library):
                self.logger.info(f"\tAttempting to install {library}...")
                try:
                    subprocess.run(['sudo', 'apt', 'install', '-y', library], 
                                 check=True, capture_output=True, text=True)
                    if self._is_linux_library_installed(library):
                        self.logger.pass_msg(f"\t{library} installed successfully.")
                    else:
                        self.logger.fail_msg(f"\t{library} installation verification failed.")
                        all_installed = False
                except subprocess.CalledProcessError as e:
                    self.logger.error(f"\tFailed to install {library}. Error: {e.stderr}")
                    all_installed = False
        
        return all_installed
    
    def _is_python_package_installed(self, package_name):
        """Check if a Python package is installed"""
        try:
            import importlib.util
            spec = importlib.util.find_spec(package_name)
            return spec is not None
        except (ImportError, AttributeError, ValueError, ModuleNotFoundError):
            return False
    
    def _install_missing_python_packages(self,python_packages):
        """Install missing Python packages"""
        import sys
        
        self.logger.info("Installing missing Python packages...")
        
        # First upgrade pip
        try:
            self.logger.info("\tAttempting to upgrade pip...")
            subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "--user"], 
                         check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to upgrade pip. Error: {e.stderr}")
            return False
        
        # Install missing packages
        all_installed = True
        for package in python_packages:
            if not self._is_python_package_installed(package):
                self.logger.info(f"\tAttempting to install {package}...")
                try:
                    subprocess.run([sys.executable, "-m", "pip", "install", package], 
                                 check=True, capture_output=True, text=True)
                    if self._is_python_package_installed(package):
                        self.logger.pass_msg(f"\t{package} installed successfully.")
                    else:
                        self.logger.fail_msg(f"\t{package} installation verification failed.")
                        all_installed = False
                except subprocess.CalledProcessError as e:
                    self.logger.error(f"\tFailed to install {package}. Error: {e.stderr}")
                    all_installed = False
        
        return all_installed

    def read_pci_attr(self, device_path, attr):
        """Helper function to read PCI device attributes from sysfs"""
        try:
            with open(os.path.join(device_path, attr), 'r') as f:
                return f.read().strip()
        except FileNotFoundError:
            return None
    
    def is_valid_pcie_speed(self, speed):
        """Check if speed value is a known good PCIe speed"""
        if not speed:
            return False
        
        # Clean the speed string (remove PCIe suffix and normalize)
        cleaned_speed = speed.strip().lower()
        
        # More precise PCIe suffix removal
        if cleaned_speed.endswith(' pcie'):
            cleaned_speed = cleaned_speed[:-len(' pcie')].strip()  # Remove ' pcie'
        elif cleaned_speed.endswith('pcie'):
            cleaned_speed = cleaned_speed[:-len('pcie')].strip()  # Remove 'pcie'
        
        # Known good PCIe speeds (lowercase for comparison)
        valid_speeds = {
            # Generation format
            'gen1', 'gen2', 'gen3', 'gen4', 'gen5', 'gen6',
            # GT/s format
            '2.5 gt/s', '5.0 gt/s', '8.0 gt/s', '16.0 gt/s', '32.0 gt/s', '64.0 gt/s',
            '2.5gt/s', '5.0gt/s', '8.0gt/s', '16.0gt/s', '32.0gt/s', '64.0gt/s',
            # Alternative formats
            '2.5', '5.0', '8.0', '16.0', '32.0', '64.0'
        }
        
        return cleaned_speed in valid_speeds
    
    def is_valid_pcie_width(self, width):
        """Check if width value is a known good PCIe width"""
        if not width:
            return False
        
        # Known good PCIe widths
        valid_widths = {'x1', 'x2', 'x4', 'x8', 'x16'}
        
        return width.lower().strip() in valid_widths
    
    def get_current_pcie_speed(self, device_path):
        """Get current PCIe speed from sysfs"""
        try:
            speed = self.read_pci_attr(device_path, "current_link_speed")
            return speed if speed else ""
        except Exception:
            return ""
    
    def get_current_pcie_width(self, device_path):
        """Get current PCIe width from sysfs"""
        try:
            width = self.read_pci_attr(device_path, "current_link_width")
            return width if width else ""
        except Exception:
            return ""
    
    def get_max_pcie_speed(self, device_path):
        """Get maximum PCIe speed from sysfs"""
        try:
            speed = self.read_pci_attr(device_path, "max_link_speed")
            return speed if speed else ""
        except Exception:
            return ""
    
    def get_max_pcie_width(self, device_path):
        """Get maximum PCIe width from sysfs"""
        try:
            width = self.read_pci_attr(device_path, "max_link_width")
            return width if width else ""
        except Exception:
            return ""

    def parse_pcie_speed_to_gts(self, pcie_speed):
        """
        Parse PCIe speed string to GT/s value.
        
        Args:
            pcie_speed (str): Speed in formats like "16.0 GT/s", "Gen4", "32.0 GT/s PCIe", or "16.0"
            
        Returns:
            float: Speed in GT/s, or None if invalid
        """
        if not pcie_speed:
            return None
        
        speed_str = pcie_speed.strip().lower()
        
        # Handle generation format (Gen1, Gen2, etc.)
        if speed_str.startswith('gen'):
            gen_mapping = {
                'gen1': 2.5,
                'gen2': 5.0, 
                'gen3': 8.0,
                'gen4': 16.0,
                'gen5': 32.0,
                'gen6': 64.0
            }
            return gen_mapping.get(speed_str)
        
        # Handle GT/s format or plain numeric
        # Remove all common suffixes iteratively until no more matches
        suffixes_to_remove = [' gt/s pcie', 'gt/s pcie', ' gt/s', 'gt/s', ' pcie', 'pcie']
        
        changed = True
        while changed:
            changed = False
            for suffix in suffixes_to_remove:
                if speed_str.endswith(suffix):
                    speed_str = speed_str[:-len(suffix)].strip()
                    changed = True
                    break  # Start over with the shortened string
        
        # Try to parse as float
        try:
            return float(speed_str)
        except ValueError:
            return None
    
    def parse_pcie_width_to_lanes(self, pcie_width):
        """
        Parse PCIe width string to lane count.
        
        Args:
            pcie_width (str): Width in formats like "x16", "16", "X16", or "16 lanes"
            
        Returns:
            int: Number of lanes, or None if invalid
        """
        if not pcie_width:
            return None
        
        width_str = pcie_width.strip().lower()
        
        # Remove common suffixes
        suffixes_to_remove = [' lanes', 'lanes']
        for suffix in suffixes_to_remove:
            if width_str.endswith(suffix):
                width_str = width_str[:-len(suffix)].strip()
        
        # Remove 'x' prefix if present
        if width_str.startswith('x'):
            width_str = width_str[1:]
        
        # Try to parse as integer
        try:
            lanes = int(width_str)
            # Validate common PCIe widths
            if lanes in [1, 2, 4, 8, 16, 32]:
                return lanes
            else:
                return None
        except ValueError:
            return None

    def get_matching_sbdf(self, *args, **kwargs):
        """
        Find matching SBDFs for a given device ID using deviceDetect script.
        
        This method supports both the current and legacy calling conventions:
          - get_matching_sbdf(device_id)
          - get_matching_sbdf(bash_manager, device_id)  # bash_manager argument is ignored
        
        Args:
            device_id (str): PCI device ID to search for
            
        Returns:
            list: List of matching SBDFs (interleaved endpoints and parents)
        """
        # Backward-compatible argument handling:
        # - If called as get_matching_sbdf(device_id), use the single positional arg.
        # - If called as get_matching_sbdf(bash_manager, device_id), ignore the first arg.
        if "device_id" in kwargs:
            device_id = kwargs["device_id"]
        elif len(args) == 1:
            device_id = args[0]
        elif len(args) >= 2:
            # Legacy usage: (bash_manager, device_id)
            device_id = args[1]
        else:
            if self.logger:
                self.logger.error("get_matching_sbdf called without a device_id argument")
            return []

        if not self.bash_manager:
            if self.logger:
                self.logger.error("BashScriptManager not available for device detection")
            return []
            
        try:
            # Use the bash script manager to call the deviceDetect script
            result_ep = self.bash_manager.call_function('deviceDetect', '_findDevices', device_id.upper())
            output_items_ep = [item for item in result_ep.strip().split() if item]
            result_parents = self.bash_manager.call_function('deviceDetect', '_findParents', device_id.upper())
            output_items_parents = [item for item in result_parents.strip().split() if item]
            
            # Create interleaved list: ep[0], parents[0], ep[1], parents[1], etc.
            composed_list = []
            max_len = max(len(output_items_ep), len(output_items_parents))
            
            for i in range(max_len):
                if i < len(output_items_ep):
                    composed_list.append(output_items_ep[i])
                if i < len(output_items_parents):
                    composed_list.append(output_items_parents[i])
            
            return composed_list
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to get matching SBDF for device {device_id}: {e}")
            return []
    
    def check_multi_user_target(self, bash_manager):
        """
        Check if system is in multi-user target (runlevel). If not, offer to switch.
        
        Args:
            bash_manager: BashScriptManager instance with registered deviceDetect script
            
        Returns:
            str: "0" if in multi-user target, "1" if not or on error
        """
        try:
            # First, let's check if the script is registered
            if not bash_manager.has_script('deviceDetect'):
                if self.logger:
                    self.logger.error("deviceDetect script not registered")
                return "1"  # Return 1 (fail) if script not found
            
            script_wrapper = bash_manager.get_script_wrapper('deviceDetect')
            
            # Check if the function exists
            if not script_wrapper.has_function('_checkRunlevel'):
                if self.logger:
                    self.logger.error("Function '_checkRunlevel' not found in deviceDetect script")
                    # List available functions for debugging
                    available_functions = script_wrapper.list_functions()
                    self.logger.info(f"Available functions in deviceDetect script: {available_functions}")
                return "1"  # Return 1 (fail) if function not found
            
            # Call the _checkRunlevel function and capture its return code
            try:
                result = bash_manager.call_function('deviceDetect', '_checkRunlevel')
                # If we get here, the function succeeded (return code 0)
                if self.logger:
                    self.logger.info("System is already in multi-user target mode.")
                return "0"
            except RuntimeError as e:
                # The function failed (return code 1), which means system is in graphical mode
                # Ask user if they want to switch to multi-user target
                try:
                    if self.logger:
                        self.logger.info('')
                        self.logger.warning("VTS requires multi-user target mode but system is in graphical mode.")
                    
                    while True:
                        user_input = input("        Do you want to switch to multi-user target now? (Switching will terminate all GUI applications.) (y/n): ").strip().lower()
                        if user_input in ['y', 'yes']:
                            if self.logger:
                                self.logger.info("User chose to switch to multi-user target...")
                            
                            # Check if _setMultiUserTarget function exists
                            if not script_wrapper.has_function('_setMultiUserTarget'):
                                if self.logger:
                                    self.logger.error("Function '_setMultiUserTarget' not found in deviceDetect script")
                                return "1"
                            
                            # Call the _setMultiUserTarget function
                            try:
                                bash_manager.call_function('deviceDetect', '_setMultiUserTarget')
                                if self.logger:
                                    self.logger.info("Successfully switched to multi-user target.")
                                return "0"  # Success
                            except RuntimeError as switch_error:
                                if self.logger:
                                    self.logger.error("Failed to switch to multi-user target.")
                                return "1"  # Failed to switch
                                
                        elif user_input in ['n', 'no']:
                            if self.logger:
                                self.logger.error("System run level check failed. User declined to switch to multi-user target.")
                            return "1"  # User declined
                        else:
                            print("Please enter 'y' for yes or 'n' for no.")
                            
                except (EOFError, KeyboardInterrupt):
                    if self.logger:
                        self.logger.info("User input interrupted.")
                    return "1"
                except Exception as input_error:
                    if self.logger:
                        self.logger.error(f"Error during user input: {input_error}")
                    return "1"
                        
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to check runlevel: {e}")
            return "1"  # Return 1 (fail) on any error

    def is_gpu_device(self, device_path, dgdiag_mapping=None):
        """Check if device is a GPU using PCI class codes and optional DGDiag mapping.
        
        Args:
            device_path (str): Path to PCI device (e.g., /sys/bus/pci/devices/0000:17:00.0)
            dgdiag_mapping (dict): Optional mapping of SBDF -> DGDiag instances for fallback
            
        Returns:
            bool: True if device appears to be a GPU, False otherwise
        """
        try:
            # Read device class
            device_class = self.read_pci_attr(device_path, "class")
            if not device_class or device_class == "Unknown":
                # If we can't read the device class from sysfs, check if it's in DGDiag mapping
                # This handles the case where sysfs is failing but we still want to test DGDiag
                if dgdiag_mapping:
                    sbdf = device_path.split('/')[-1] if '/' in device_path else device_path
                    is_mapped = sbdf in dgdiag_mapping
                    return is_mapped
                else:
                    # If no mapping available and sysfs failed, assume it might be a GPU for testing
                    return True  # Allow DGDiag fallback to be attempted
            
            # Convert to integer for comparison
            class_code = int(device_class, 16)
            
            # Check if it's a display controller (class 0x03xxxx)
            is_display = (class_code >> 16) == 0x03
            
            # Also check if it's in our DGDiag mapping (means it's a recognized GPU)
            if dgdiag_mapping:
                sbdf = device_path.split('/')[-1] if '/' in device_path else device_path
                is_mapped = sbdf in dgdiag_mapping
            else:
                is_mapped = False
            
            return is_display or is_mapped
            
        except Exception as e:
            # If there's any other error, assume it might be a GPU to allow fallback testing
            return True
    
    def get_bus_info(self, device_id, speed_width_callbacks=None, verbose=True):
        """Get comprehensive PCI bus information for devices matching a device ID.
        
        Args:
            device_id (str): PCI device ID to search for
            speed_width_callbacks (dict): Optional callbacks for getting PCIe speed/width info
                                        Format: {'get_current_speed': func, 'get_current_width': func, 
                                                'get_max_speed': func, 'get_max_width': func}
            verbose (bool): Whether to print header and results
            
        Returns:
            dict: Dictionary mapping SBDF -> device info dictionary
        """
        pcie_sbdf_list = self.get_matching_sbdf(device_id)
        
        if verbose and self.logger: 
            self.logger.subheader('{}'.format("GETTING PCI BUS INFO"))
        
        bus_dict = {}
        index = 0
        
        for device in pcie_sbdf_list:
            device_path = f"/sys/bus/pci/devices/{device}"
            
            # Read device attributes first
            device_did = self.read_pci_attr(device_path, "device")
            
            # Check if device DID matches target device_id and assign index accordingly
            def normalize_hex(val):
                if val is None:
                    return None
                val = val.lower()
                return val[2:] if val.startswith("0x") else val
            
            if device_did and normalize_hex(device_did) == normalize_hex(device_id):
                index = index + 1
                device_index = index
                # For matching devices, show SVID/SDID but don't show link speed/width info
                svid = self.read_pci_attr(device_path, "subsystem_vendor")
                sdid = self.read_pci_attr(device_path, "subsystem_device")
                max_link_speed = ""
                curr_link_speed = ""
                max_link_width = ""
                curr_link_width = ""
            else:
                device_index = ""  # Empty string for non-matching devices
                # For non-matching devices, don't show SVID/SDID but show link speed/width info
                svid = ""
                sdid = ""
                # Get current and max speed/width using callbacks if provided
                if speed_width_callbacks:
                    curr_link_speed = speed_width_callbacks.get('get_current_speed', lambda x: "")(device_path)
                    curr_link_width = speed_width_callbacks.get('get_current_width', lambda x: "")(device_path)
                    max_link_speed = speed_width_callbacks.get('get_max_speed', lambda x: "")(device_path)
                    max_link_width = speed_width_callbacks.get('get_max_width', lambda x: "")(device_path)
                else:
                    curr_link_speed = ""
                    curr_link_width = ""
                    max_link_speed = ""
                    max_link_width = ""
            
            # Create ordered dictionary with Index first
            bus_dict[device] = {
                'Index': device_index,
                'SBDF': device,
                "VID": self.read_pci_attr(device_path, "vendor"),
                "DID": device_did,
                "SVID": svid,
                "SDID": sdid,
                "Class": self.read_pci_attr(device_path, "class"),
                "Revision": self.read_pci_attr(device_path, "revision"),
                "Max Link Speed": max_link_speed,
                "Curr Link Speed": curr_link_speed,
                "Max Link Width": max_link_width,
                "Curr Link Width": curr_link_width
            }
        
        # Display the resulting dictionary if verbose and logger available
        if verbose and self.logger:
            self.print_bus_info(bus_dict)
            
        return bus_dict
    
    def print_bus_info(self, bus_dict):
        """Print PCI bus information dictionary as a formatted table.
        
        Args:
            bus_dict (dict): Dictionary of device info from get_bus_info()
        """
        if self.logger and hasattr(self.logger, 'printTableFromDict'):
            self.logger.printTableFromDict(bus_dict)

    def check_pcie_alignment(self, device_id, verbose=True):
        """
        Check if PCIe speed and width on GPU devices are aligned with their maximum capabilities.
        Uses existing get_bus_info function to retrieve PCIe data from parent devices.
        
        Args:
            device_id (str): PCI device ID to search for (typically GPU device ID)
            verbose (bool): Whether to print detailed results
            
        Returns:
            dict: Dictionary with check results
                  {'success': bool, 'aligned_devices': list, 'misaligned_devices': list, 
                   'error': str or None, 'details': dict}
        """
        result = {
            'success': False,
            'aligned_devices': [],
            'misaligned_devices': [],
            'error': None,
            'details': {}
        }
        
        try:
            if verbose and self.logger:
                self.logger.subheader('PCIe SPEED/WIDTH ALIGNMENT CHECK')
                self.logger.info(f'Checking PCIe alignment for device ID: {device_id}')
            
            # Create callbacks for reading PCIe speed/width from sysfs
            speed_width_callbacks = {
                'get_current_speed': self.get_current_pcie_speed,
                'get_current_width': self.get_current_pcie_width, 
                'get_max_speed': self.get_max_pcie_speed,
                'get_max_width': self.get_max_pcie_width
            }
            
            # Get bus information using existing function with PCIe data callbacks
            bus_dict = self.get_bus_info(device_id, speed_width_callbacks=speed_width_callbacks, verbose=False)
            
            if not bus_dict:
                error_msg = f"No devices found matching device ID: {device_id}"
                result['error'] = error_msg
                if verbose and self.logger:
                    self.logger.warning(error_msg)
                return result
            
            # Find GPU endpoint devices (those with Index numbers) and their corresponding parent devices
            gpu_devices = {}
            parent_devices = {}
            
            for sbdf, info in bus_dict.items():
                if info.get('Index') and str(info.get('Index')).isdigit():
                    gpu_devices[sbdf] = info
                elif info.get('Max Link Speed') or info.get('Curr Link Speed'):
                    # This is likely a parent device with PCIe link information
                    parent_devices[sbdf] = info
            
            if not gpu_devices:
                error_msg = f"No GPU endpoint devices found for device ID: {device_id}"
                result['error'] = error_msg
                if verbose and self.logger:
                    self.logger.warning(error_msg)
                return result
            
            if not parent_devices:
                # If no parent devices have PCIe info, treat the check as skipped/unknown
                result['success'] = False
                result['skipped'] = True
                result['error'] = (
                    f"PCIe alignment check skipped: no parent devices with link information "
                    f"were found for device ID: {device_id}"
                )
                if verbose and self.logger:
                    self.logger.info(result['error'])
                return result
            
            # Check alignment using parent device PCIe information as representative of the GPU's link
            for parent_sbdf, parent_info in parent_devices.items():
                curr_speed = parent_info.get('Curr Link Speed', '').strip()
                max_speed = parent_info.get('Max Link Speed', '').strip()
                curr_width = parent_info.get('Curr Link Width', '').strip()
                max_width = parent_info.get('Max Link Width', '').strip()
                
                # Parse speeds and widths for comparison
                curr_speed_gts = self.parse_pcie_speed_to_gts(curr_speed)
                max_speed_gts = self.parse_pcie_speed_to_gts(max_speed)
                curr_width_lanes = self.parse_pcie_width_to_lanes(curr_width)
                max_width_lanes = self.parse_pcie_width_to_lanes(max_width)
                
                device_results = {
                    'sbdf': parent_sbdf,
                    'current_speed': curr_speed,
                    'max_speed': max_speed,
                    'current_width': curr_width,
                    'max_width': max_width,
                    'speed_aligned': False,
                    'width_aligned': False,
                    'overall_aligned': False,
                    'issues': []
                }
                
                # Skip if no PCIe data available
                if not curr_speed and not max_speed and not curr_width and not max_width:
                    continue
                
                # Check speed alignment
                if curr_speed_gts is not None and max_speed_gts is not None:
                    device_results['speed_aligned'] = curr_speed_gts >= max_speed_gts
                    if not device_results['speed_aligned']:
                        device_results['issues'].append(f"Speed: {curr_speed} < {max_speed}")
                elif not curr_speed or not max_speed:
                    # If speed data is missing, don't treat as an issue - just skip speed check
                    device_results['speed_aligned'] = True
                else:
                    device_results['issues'].append(f"Speed data invalid: current '{curr_speed}', max '{max_speed}'")
                
                # Check width alignment
                if curr_width_lanes is not None and max_width_lanes is not None:
                    device_results['width_aligned'] = curr_width_lanes >= max_width_lanes
                    if not device_results['width_aligned']:
                        device_results['issues'].append(f"Width: {curr_width} < {max_width}")
                elif not curr_width or not max_width:
                    # If width data is missing, don't treat as an issue - just skip width check
                    device_results['width_aligned'] = True
                else:
                    device_results['issues'].append(f"Width data invalid: current '{curr_width}', max '{max_width}'")
                
                # Determine overall alignment
                device_results['overall_aligned'] = (device_results['speed_aligned'] and device_results['width_aligned'])
                
                # Categorize device
                if device_results['overall_aligned'] and not device_results['issues']:
                    result['aligned_devices'].append(parent_sbdf)
                else:
                    result['misaligned_devices'].append(parent_sbdf)
                
                result['details'][parent_sbdf] = device_results
            
            # Print results if verbose
            if verbose and self.logger and result['details']:
                if result['aligned_devices']:
                    self.logger.info(f"✓ {len(result['aligned_devices'])} PCIe link(s) properly aligned:")
                    for sbdf in result['aligned_devices']:
                        device = result['details'][sbdf]
                        # For aligned devices, just show current values since they match max
                        self.logger.info(f"    {sbdf}: Speed {device['current_speed']}, Width {device['current_width']}")
                
                if result['misaligned_devices']:
                    self.logger.warning(f"✗ {len(result['misaligned_devices'])} PCIe link(s) have misalignment:")
                    for sbdf in result['misaligned_devices']:
                        device = result['details'][sbdf]
                        issue_summary = '; '.join(device['issues'])
                        self.logger.warning(f"    {sbdf}: {issue_summary}")
                    self.logger.warning("    RECOMMENDATION: Check PCIe slot configuration, motherboard settings, and ensure GPUs are properly seated in PCIe x16 slots")
            
            # Additional check: PCIe tree link status (check for downgrades)
            tree_link_result = None
            tree_check_performed = False
            if self.bash_manager and result['details']:  # Only check if we found devices
                tree_link_result = self.check_pcie_tree_link_status(device_id, verbose=False)
                tree_check_performed = True
                
                if not tree_link_result.get('success', True):  # Default to True if check unavailable
                    # Add tree link issues to misaligned devices
                    if tree_link_result.get('error'):
                        result['error'] = f"PCIe tree link check failed: {tree_link_result['error']}"
                    else:
                        # Parse output to identify devices with downgrades
                        downgraded_devices = []
                        output = tree_link_result.get('output', '')
                        for line in output.split('\n'):
                            if 'downgrade detected' in line.lower() and '0000:' in line:
                                # Extract device SBDF from line
                                import re
                                sbdf_match = re.search(r'(\b[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]\b)', line)
                                if sbdf_match:
                                    downgraded_devices.append(sbdf_match.group(1))
                        
                        if downgraded_devices:
                            # Add downgrade issues to existing device details or create new entries
                            for sbdf in downgraded_devices:
                                if sbdf in result['details']:
                                    # Add to existing device issues
                                    if 'PCIe link downgrade detected' not in result['details'][sbdf]['issues']:
                                        result['details'][sbdf]['issues'].append('PCIe link downgrade detected')
                                        result['details'][sbdf]['overall_aligned'] = False
                                        # Move to misaligned if was aligned
                                        if sbdf in result['aligned_devices']:
                                            result['aligned_devices'].remove(sbdf)
                                            result['misaligned_devices'].append(sbdf)
                                else:
                                    # Create new entry for device found by link check
                                    result['details'][sbdf] = {
                                        'current_speed': 'Unknown',
                                        'current_width': 'Unknown',
                                        'max_speed': 'Unknown',
                                        'max_width': 'Unknown',
                                        'speed_aligned': False,
                                        'width_aligned': False,
                                        'overall_aligned': False,
                                        'issues': ['PCIe link downgrade detected']
                                    }
                                    result['misaligned_devices'].append(sbdf)
            
            # Set overall success - if we found devices to check and no real issues
            if result['details']:
                result['success'] = len(result['misaligned_devices']) == 0
            else:
                # No devices had PCIe data to check - treat as successful skip
                result['success'] = True
                # Don't set error message - this is a graceful skip, not a failure
            
            # Add formatted device details for systemChecker to use
            result['aligned_device_details'] = []
            result['misaligned_device_details'] = []
            
            for sbdf in result['aligned_devices']:
                device = result['details'][sbdf] 
                # Add tree check status indicator
                tree_status = " (Tree: ✓)" if tree_check_performed else ""
                detail_str = f"    {sbdf}: Speed {device['current_speed']}, Width {device['current_width']}{tree_status}"
                result['aligned_device_details'].append(detail_str)
            
            for sbdf in result['misaligned_devices']:
                device = result['details'][sbdf]
                issue_summary = '; '.join(device['issues'])
                # Add tree check status indicator if performed
                tree_status = " (Tree: ✗)" if tree_check_performed and any('downgrade' in issue.lower() for issue in device['issues']) else (" (Tree: ✓)" if tree_check_performed else "")
                detail_str = f"    {sbdf}: {issue_summary}{tree_status}"
                result['misaligned_device_details'].append(detail_str)
            
        except Exception as e:
            error_msg = f"PCIe alignment check failed: {str(e)}"
            result['error'] = error_msg
            if self.logger:
                self.logger.error(error_msg)
        
        return result
    
    def check_pcie_tree_link_status(self, device_id, verbose=True):
        """Check PCIe tree link status for downgrades using deviceDetect.sh _PCIeTreeLinkCheck function.
        
        Args:
            device_id (str): Device ID to check (e.g., 'e223')
            verbose (bool): Whether to show detailed output
            
        Returns:
            dict: Dictionary with 'success' (bool), 'output' (str), and 'error' (str) keys
        """
        result = {
            'success': False,
            'output': '',
            'error': None
        }
        
        if not self.bash_manager:
            result['error'] = "BashScriptManager not available for PCIe tree link check"
            return result
        
        try:
            if verbose and self.logger:
                self.logger.info(f"Checking PCIe tree link status for device ID {device_id}...")
            
            # Call the bash function - returns output if successful (exit code 0), raises RuntimeError if failed  
            try:
                bash_output = self.bash_manager.call_function('deviceDetect', '_PCIeTreeLinkCheck', device_id)
                # If we get here, the function succeeded (return code 0) - no downgrades detected
                result['success'] = True
                result['output'] = bash_output
                
                if verbose and self.logger:
                    self.logger.info("✓ PCIe tree link check PASSED - no downgrades detected")
                    
            except RuntimeError as e:
                # The function failed (return code 1) - downgrades were detected
                bash_output = str(e)  # Error message contains the output
                result['success'] = False  
                result['output'] = bash_output
                
                if verbose and self.logger:
                    self.logger.warning("✗ PCIe tree link check FAILED - link downgrades detected")
                    # Parse output to show which devices have downgrades
                    if 'downgrade detected' in bash_output:
                        for line in bash_output.split('\n'):
                            if 'downgrade detected' in line:
                                self.logger.warning(f"    {line.strip()}")
                                
        except Exception as e:
            result['error'] = f"PCIe tree link check failed: {str(e)}"
            if self.logger:
                self.logger.error(result['error'])
        
        return result
    
    def unbindXeDevices(self, bash_manager_or_device_id, device_id=None, verbose=True):
        """Unbind devices matching a given PCI device ID from xe driver.
        
        Args:
            bash_manager_or_device_id: For backward compatibility, this may be either:
                - bash_manager (legacy signature: unbindXeDevices(bash_manager, device_id, ...)), or
                - device_id (new signature: unbindXeDevices(device_id, ...)).
            device_id (str, optional): PCI device ID to search for when using the legacy signature.
            verbose (bool): Whether to print header and results
            
        Returns:
            dict: Dictionary with 'success' (bool), 'unbound_devices' (list), and 'error' (str) keys
        """
        # Normalize arguments to support both old and new call signatures.
        # New-style: unbindXeDevices(device_id, verbose=True)
        # Old-style: unbindXeDevices(bash_manager, device_id, verbose=True)
        if device_id is None:
            device_id = bash_manager_or_device_id

        result = {
            'success': False,
            'unbound_devices': [],
            'error': None
        }
        
        if not self.bash_manager:
            result['error'] = "BashScriptManager not available for device unbinding"
            return result
        
        try:
            if verbose and self.logger:
                self.logger.subheader('UNBINDING XE DEVICES')
                self.logger.info(f'Searching for devices with ID: {device_id}')
            
            # Get matching devices
            pcie_sbdf_list = self.get_matching_sbdf(device_id)
            
            if not pcie_sbdf_list:
                msg = f"No devices found matching device ID: {device_id}"
                if verbose and self.logger:
                    self.logger.warning(msg)
                result['error'] = msg
                return result
            
            # Filter to only include devices actually bound to xe driver
            xe_driver_path = '/sys/bus/pci/drivers/xe'
            initially_bound_devices = []
            for sbdf in pcie_sbdf_list:
                device_link_path = os.path.join(xe_driver_path, sbdf)
                if os.path.exists(device_link_path):
                    initially_bound_devices.append(sbdf)
            
            if not initially_bound_devices:
                msg = f"No devices are currently bound to xe driver"
                if verbose and self.logger:
                    self.logger.info(msg)
                result['success'] = True  # Nothing to unbind is success
                result['error'] = msg
                return result
            
            devices_to_unbind = ' '.join(initially_bound_devices)
            
            if verbose and self.logger:
                self.logger.info(f'Found {len(pcie_sbdf_list)} total device(s), {len(initially_bound_devices)} bound to xe driver: {devices_to_unbind}')
            
            # Use the bash script manager to call the deviceDetect script
            bash_result = self.bash_manager.call_function('deviceDetect', 'unbindDevices', devices_to_unbind, 'xe')
            
            if verbose and self.logger:
                self.logger.info('Bash script completed, verifying unbind results...')
                if bash_result.strip():
                    self.logger.info(f'Script output:\n {bash_result.strip()}')
            
            # Verify that devices were actually unbound by checking xe driver directory
            xe_driver_path = '/sys/bus/pci/drivers/xe'
            actually_unbound = []
            still_bound = []
            
            for sbdf in initially_bound_devices:
                device_link_path = os.path.join(xe_driver_path, sbdf)
                if not os.path.exists(device_link_path):
                    actually_unbound.append(sbdf)
                    if verbose and self.logger:
                        self.logger.info(f'  ✓ {sbdf} successfully unbound from xe driver')
                else:
                    still_bound.append(sbdf)
                    if verbose and self.logger:
                        self.logger.warning(f'  ✗ {sbdf} still bound to xe driver')
            
            # Update result based on verification
            if actually_unbound:
                result['unbound_devices'] = actually_unbound
                if not still_bound:
                    result['success'] = True
                    if verbose and self.logger:
                        self.logger.info(f'All {len(actually_unbound)} device(s) successfully unbound from xe driver')
                else:
                    result['success'] = False
                    result['error'] = f"Failed to unbind {len(still_bound)} device(s): {', '.join(still_bound)}"
                    if verbose and self.logger:
                        self.logger.warning(f'Partial success: {len(actually_unbound)} unbound, {len(still_bound)} still bound')
            else:
                result['success'] = False
                result['error'] = f"No devices were successfully unbound. All {len(still_bound)} devices remain bound to xe driver"
            
        except Exception as e:
            error_msg = f"Failed to unbind xe devices: {str(e)}"
            if self.logger:
                self.logger.error(error_msg)
            result['error'] = error_msg
        
        return result

    def bindXeDevices(self, bash_manager, device_id, verbose=True, specific_devices=None):
        """Bind devices matching a given PCI device ID to xe driver.
        
        Args:
            bash_manager: BashScriptManager instance with registered deviceDetect script
            device_id (str): PCI device ID to search for
            verbose (bool): Whether to print header and results
            specific_devices (list): Optional list of specific device SBDFs to bind.
                                   If provided, only these devices will be bound.
                                   If None, will find all unbound endpoint devices.
            
        Returns:
            dict: Dictionary with 'success' (bool), 'bound_devices' (list), and 'error' (str) keys
        """
        result = {
            'success': False,
            'bound_devices': [],
            'error': None
        }
        
        try:
            if verbose and self.logger:
                self.logger.subheader('BINDING XE DEVICES')
                self.logger.info(f'Searching for devices with ID: {device_id}')
            
            # Determine which devices to bind
            if specific_devices:
                # Use the specific list of devices provided (typically from previous unbind operation)
                candidate_devices = specific_devices
                if verbose and self.logger:
                    self.logger.info(f'Using specific devices list: {" ".join(candidate_devices)}')
            else:
                # Fall back to original behavior: find all matching unbound endpoint devices
                pcie_sbdf_list = self.get_matching_sbdf(bash_manager, device_id)
                
                if not pcie_sbdf_list:
                    msg = f"No devices found matching device ID: {device_id}"
                    if verbose and self.logger:
                        self.logger.warning(msg)
                    result['error'] = msg
                    return result
                
                # Filter to only include devices NOT currently bound to xe driver
                # (we only want to bind devices that are currently unbound)
                xe_driver_path = '/sys/bus/pci/drivers/xe'
                candidate_devices = []
                for sbdf in pcie_sbdf_list:
                    device_link_path = os.path.join(xe_driver_path, sbdf)
                    if not os.path.exists(device_link_path):
                        # Check if this is likely an endpoint device (xe driver typically binds endpoints, not parents)
                        # Endpoint devices typically have .0 function and are the actual GPU devices
                        if sbdf.endswith('.0'):
                            candidate_devices.append(sbdf)
            
            # Verify devices are actually unbound and filter out any that are already bound
            xe_driver_path = '/sys/bus/pci/drivers/xe'
            unbound_devices = []
            for sbdf in candidate_devices:
                device_link_path = os.path.join(xe_driver_path, sbdf)
                if not os.path.exists(device_link_path):
                    unbound_devices.append(sbdf)
                elif verbose and self.logger:
                    self.logger.info(f'Skipping {sbdf} - already bound to xe driver')
            
            if not unbound_devices:
                msg = f"No unbound endpoint devices found to bind"
                if verbose and self.logger:
                    self.logger.info(msg)
                result['success'] = True  # Nothing to bind is success
                result['error'] = msg
                return result
            
            devices_to_bind = ' '.join(unbound_devices)
            
            if specific_devices:
                device_count_msg = f'Found {len(candidate_devices)} candidate device(s), {len(unbound_devices)} unbound device(s) to bind: {devices_to_bind}'
            else:
                device_count_msg = f'Found {len(pcie_sbdf_list)} total device(s), {len(unbound_devices)} unbound endpoint(s) to bind: {devices_to_bind}'
            
            if verbose and self.logger:
                self.logger.info(device_count_msg)
            
            # Use the bash script manager to call the deviceDetect script
            # Prefer an explicitly passed-in bash_manager, fall back to self.bash_manager
            manager = bash_manager if bash_manager is not None else self.bash_manager
            if not manager:
                msg = "No bash manager available to bind xe devices"
                if verbose and self.logger:
                    self.logger.error(msg)
                result['error'] = msg
                return result
            
            bash_result = manager.call_function('deviceDetect', 'bindDevices', devices_to_bind, 'xe')
            
            if verbose and self.logger:
                self.logger.info('Bash script completed, verifying bind results...')
                if bash_result.strip():
                    self.logger.info(f'Script output:\n {bash_result.strip()}')
            
            # Verify that devices were actually bound by checking xe driver directory
            # Only check devices that were candidates for binding
            xe_driver_path = '/sys/bus/pci/drivers/xe'
            actually_bound = []
            not_bound = []
            
            for sbdf in unbound_devices:
                device_link_path = os.path.join(xe_driver_path, sbdf)
                if os.path.exists(device_link_path):
                    actually_bound.append(sbdf)
                    if verbose and self.logger:
                        self.logger.info(f'  ✓ {sbdf} successfully bound to xe driver')
                else:
                    not_bound.append(sbdf)
                    if verbose and self.logger:
                        self.logger.warning(f'  ✗ {sbdf} not bound to xe driver')
            
            # Update result based on verification
            if actually_bound:
                result['bound_devices'] = actually_bound
                if not not_bound:
                    result['success'] = True
                    if verbose and self.logger:
                        self.logger.info(f'All {len(actually_bound)} device(s) successfully bound to xe driver')
                else:
                    result['success'] = False
                    result['error'] = f"Failed to bind {len(not_bound)} device(s): {', '.join(not_bound)}"
                    if verbose and self.logger:
                        self.logger.warning(f'Partial success: {len(actually_bound)} bound, {len(not_bound)} not bound')
            else:
                result['success'] = False
                result['error'] = f"No devices were successfully bound. All {len(not_bound)} devices remain unbound from xe driver"
            
        except Exception as e:
            error_msg = f"Failed to bind xe devices: {str(e)}"
            if self.logger:
                self.logger.error(error_msg)
            result['error'] = error_msg
        
        return result

    # Class-level cache for error descriptions to avoid repeated CSV reads
    _error_descriptions_cache = None
    
    def load_error_descriptions(self, csv_path=None, default_csv_path=None):
        """Load error code descriptions from CSV file with caching.
        
        Args:
            csv_path (str): Optional explicit path to CSV file
            default_csv_path (str): Default CSV path to use if csv_path is None
            
        Returns:
            dict: Dictionary mapping error codes to descriptions
        """
        # Return cached value if already loaded
        if Utils._error_descriptions_cache is not None:
            return Utils._error_descriptions_cache
        
        # Load from CSV file on first call
        try:
            import pandas as pd
            # Use provided csv_path or default
            if csv_path is None:
                csv_path = default_csv_path
            
            if csv_path is None:
                raise ValueError("No CSV path provided and no default path specified")
            
            df = pd.read_csv(csv_path)
            # Create dictionary mapping error codes to descriptions
            Utils._error_descriptions_cache = dict(zip(df['Error Code'], df['Description']))
            return Utils._error_descriptions_cache
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to load error descriptions from {csv_path}: {e}")
            # Cache empty dict to avoid repeated failed attempts
            Utils._error_descriptions_cache = {}
            return Utils._error_descriptions_cache
    
    def validate_device_ids(self, device_ids_str):
        """Validate device IDs string for xpu-smi and similar commands."""
        import re
        
        if not isinstance(device_ids_str, str):
            device_ids_str = str(device_ids_str)
        
        device_ids_str = device_ids_str.strip()
        
        # Must match pattern: one or more numbers separated by commas
        if not re.match(r'^\d+(?:,\d+)*$', device_ids_str):
            if self.logger:
                self.logger.warning(f"SECURITY: Invalid device IDs format: {device_ids_str}")
            raise ValueError(f"Invalid device IDs format: {device_ids_str}")
        
        # Check each individual ID is reasonable (0-255)
        ids = device_ids_str.split(',')
        for device_id in ids:
            id_num = int(device_id)
            if id_num < 0 or id_num > 255:
                if self.logger:
                    self.logger.warning(f"SECURITY: Device ID out of range: {id_num}")
                raise ValueError(f"Device ID out of range (0-255): {id_num}")
        
        return device_ids_str
    
    def validate_sampling_rate(self, sampling_rate):
        """Validate sampling rate parameter."""
        try:
            rate = int(sampling_rate)
            if rate <= 0 or rate > 10000:
                if self.logger:
                    self.logger.warning(f"SECURITY: Invalid sampling rate: {rate}")
                raise ValueError(f"Invalid sampling rate (1-10000): {rate}")
            return rate
        except (ValueError, TypeError):
            if self.logger:
                self.logger.warning(f"SECURITY: Non-numeric sampling rate: {sampling_rate}")
            raise ValueError(f"Sampling rate must be a positive integer: {sampling_rate}")
    
    def validate_monitor_file_path(self, file_path, test_name, logs_dir=None):
        """Validate monitoring file path for security.

        Args:
            file_path (str): Path to the monitoring output file.
            test_name (str): Name of the test (used to validate characters in the path).
            logs_dir (str, optional): The configured logs directory to allow. When
                provided this takes precedence over the VTS-root-derived fallback,
                which means the validation works correctly regardless of the current
                working directory or the location of this module.
        """
        if not isinstance(file_path, str):
            raise ValueError("File path must be a string")
        
        if not isinstance(test_name, str):
            raise ValueError("Test name must be a string")
        
        # Check for path traversal in the file path
        if '..' in file_path:
            if self.logger:
                self.logger.warning(f"SECURITY: Path traversal detected in file path: {file_path}")
            raise ValueError(f"Path traversal not allowed in file path: {file_path}")
        
        # Validate test name to prevent injection through file path
        if not re.fullmatch(r'[a-zA-Z0-9_\-]+', test_name):
            if self.logger:
                self.logger.warning(f"SECURITY: Invalid characters in test name: {test_name}")
            raise ValueError(f"Test name contains invalid characters: {test_name}")

        # Ensure the file path is absolute and within expected directories.
        abs_path = os.path.abspath(file_path)

        # Build the list of allowed directories.  When the caller provides the
        # configured logs directory (e.g. self.logs_dir derived from __file__ in
        # the caller), use that directly so the check is independent of os.getcwd().
        # Fall back to deriving the logs directory from this module's location.
        if logs_dir is not None:
            configured_logs_dir = os.path.normpath(os.path.abspath(logs_dir))
        else:
            _vts_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            configured_logs_dir = os.path.normpath(os.path.join(_vts_root, 'logs'))

        allowed_dirs = ['/var/log', '/tmp', configured_logs_dir]

        if not any(Path(abs_path).is_relative_to(Path(allowed_dir)) for allowed_dir in allowed_dirs):
            if self.logger:
                self.logger.warning(f"SECURITY: File path outside allowed directories: {abs_path}")
            raise ValueError(f"File path not in allowed directories: {abs_path}")
        
        return abs_path
    
    def build_safe_xpu_smi_command(self, device_ids, csv_file, sampling_rate):
        """Build a safe xpu-smi command using validated parameters."""
        # Validate all inputs
        validated_ids = self.validate_device_ids(device_ids)
        validated_rate = self.validate_sampling_rate(sampling_rate)
        validated_file = os.path.abspath(csv_file)  # Don't validate path here, assume it's been validated
        
        # Build command as list (prevents shell injection)
        command = [
            'xpu-smi', 'dump',
            '-d', validated_ids,
            '-m', '0,1,2,3,4,5,6,7,8,9,10,11,35',
            '--file', validated_file,
            '--ims', str(validated_rate),
            '--date'
        ]
        
        return command
    
    def build_safe_dgdiag_command(self, device_ids, file_prefix, sampling_rate):
        """Build a safe DGDiag command using validated parameters."""
        # Validate all inputs
        validated_ids = self.validate_device_ids(device_ids)
        validated_rate = self.validate_sampling_rate(sampling_rate)
        validated_file = os.path.abspath(file_prefix)  # Don't validate path here, assume it's been validated
        
        # Build command as list (prevents shell injection)
        command = [
            './DGDiagTool',
            f'-SYSTEM.UTIL.DGMonitor',
            f'inst={validated_ids}',
            'duration=259200',
            f'stime={validated_rate}',
            'domains=ALL',
            f'file={validated_file}'
        ]
        
        return command
    
    def validate_docker_image(self, image_name):
        """Validate Docker image name for security."""
        import re
        
        if not isinstance(image_name, str):
            raise ValueError("Docker image name must be a string")
        
        image_name = image_name.strip()
        
        if not image_name:
            raise ValueError("Docker image name cannot be empty")
        
        # Docker image name validation (official Docker rules)
        # Format: [hostname[:port]/]namespace/repository[:tag]
        # - lowercase letters, digits, underscores, periods, dashes
        # - namespace and repository cannot start with underscore, period, or dash
        # - tags can contain uppercase letters too
        
        # Basic pattern check for common injection attempts
        dangerous_chars = ['&', '|', ';', '$', '`', '>', '<', '(', ')', '{', '}', '"', "'", '\\', '\n', '\r', '\t']
        for char in dangerous_chars:
            if char in image_name:
                if self.logger:
                    self.logger.warning(f"SECURITY: Dangerous character in Docker image name: {image_name}")
                raise ValueError(f"Docker image name contains dangerous character '{char}': {image_name}")
        
        # More lenient pattern - allow most valid Docker image formats including ports
        # Pattern supports: hostname:port/namespace/repository:tag
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9._:/-]*[a-zA-Z0-9](?::[a-zA-Z0-9._-]+)?$', image_name):
            if self.logger:
                self.logger.warning(f"SECURITY: Invalid Docker image name format: {image_name}")
            raise ValueError(f"Invalid Docker image name format: {image_name}")
        
        # Length check (Docker has limits)
        if len(image_name) > 255:
            if self.logger:
                self.logger.warning(f"SECURITY: Docker image name too long: {len(image_name)} chars")
            raise ValueError(f"Docker image name too long (max 255 chars): {image_name}")
        
        return image_name
    
    def validate_service_name(self, service_name):
        """Validate Docker service/container name for security."""
        import re
        
        if not isinstance(service_name, str):
            raise ValueError("Docker service name must be a string")
        
        service_name = service_name.strip()
        
        if not service_name:
            raise ValueError("Docker service name cannot be empty")
        
        # Check for dangerous characters that could lead to injection
        dangerous_chars = ['&', '|', ';', '$', '`', '>', '<', '(', ')', '{', '}', '"', "'", '\\', '\n', '\r', '\t', ' ']
        for char in dangerous_chars:
            if char in service_name:
                if self.logger:
                    self.logger.warning(f"SECURITY: Dangerous character in Docker service name: {service_name}")
                raise ValueError(f"Docker service name contains dangerous character '{char}': {service_name}")
        
        # Docker container name rules:
        # - can contain letters, digits, underscores, periods, dashes
        # - must start with alphanumeric
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]*$', service_name):
            if self.logger:
                self.logger.warning(f"SECURITY: Invalid Docker service name format: {service_name}")
            raise ValueError(f"Invalid Docker service name format: {service_name}")
        
        # Length check
        if len(service_name) > 63:
            if self.logger:
                self.logger.warning(f"SECURITY: Docker service name too long: {len(service_name)} chars")
            raise ValueError(f"Docker service name too long (max 63 chars): {service_name}")
        
        return service_name
    
    def validate_config_file_path(self, file_path):
        """Validate configuration file path for security - prevents path traversal attacks."""
        import re
        
        if not isinstance(file_path, str):
            raise ValueError("Config file path must be a string")
        
        file_path = file_path.strip()
        
        if not file_path:
            raise ValueError("Config file path cannot be empty")
        
        # Must be .json file
        if not file_path.endswith('.json'):
            if self.logger:
                self.logger.warning(f"SECURITY: Config file must be JSON: {file_path}")
            raise ValueError("Config file must be a .json file")
        
        # Check for path traversal sequences
        dangerous_sequences = ['../', '..\\', '/../', '\\..\\', '..', '/..', '\\..']
        for seq in dangerous_sequences:
            if seq in file_path:
                if self.logger:
                    self.logger.warning(f"SECURITY: Path traversal detected in config path: {file_path}")
                raise ValueError(f"Path traversal sequences not allowed in config file path: {file_path}")
        
        # Check for dangerous characters that could lead to injection
        dangerous_chars = ['&', '|', ';', '$', '`', '>', '<', '(', ')', '{', '}', '"', "'", '\n', '\r', '\t']
        for char in dangerous_chars:
            if char in file_path:
                if self.logger:
                    self.logger.warning(f"SECURITY: Dangerous character in config path: {file_path}")
                raise ValueError(f"Config path contains dangerous character '{char}': {file_path}")
        
        # Resolve symlinks and get real absolute path to prevent symlink-based bypasses
        abs_path = os.path.realpath(os.path.abspath(file_path))
        
        # If absolute path, must be in allowed directories
        if os.path.isabs(file_path):
            # Define allowed directories (both Linux and Windows paths)
            allowed_dirs = [
                '/opt/vts/configs/', '/tmp/vts_configs/', '/var/lib/vts/',
                'C:\\Intel\\VTS\\configs\\', 'C:\\temp\\vts_configs\\',
                os.path.join(os.getcwd(), 'configs'),  # Allow configs in current working dir
                os.path.join(os.getcwd(), 'gpu'),      # Allow test configs in gpu dir
                os.getcwd()  # Allow files in current directory
            ]
            
            allowed = False
            for allowed_dir in allowed_dirs:
                try:
                    norm_abs = os.path.normcase(abs_path)
                    norm_dir = os.path.normcase(os.path.realpath(os.path.abspath(allowed_dir)))
                    Path(norm_abs).relative_to(Path(norm_dir))
                    allowed = True
                    break
                except (OSError, ValueError):
                    continue
            
            if not allowed:
                if self.logger:
                    self.logger.warning(f"SECURITY: Config path outside allowed directories: {abs_path}")
                raise ValueError(f"Absolute config path not in allowed directories: {abs_path}")
        
        # Must exist and be readable
        if not os.path.exists(abs_path):
            if self.logger:
                self.logger.warning(f"SECURITY: Config file does not exist: {abs_path}")
            raise ValueError(f"Config file does not exist: {abs_path}")
        
        if not os.path.isfile(abs_path):
            if self.logger:
                self.logger.warning(f"SECURITY: Config path is not a file: {abs_path}")
            raise ValueError(f"Config path is not a regular file: {abs_path}")
        
        return abs_path
    
    def sanitize_test_name(self, test_name):
        """Sanitize test name for safe file path usage - prevents path traversal."""
        import re
        
        if not isinstance(test_name, str):
            test_name = str(test_name)
        
        test_name = test_name.strip()
        
        if not test_name:
            raise ValueError("Test name cannot be empty")
        
        # Remove path traversal sequences
        dangerous_sequences = ['../', '..\\', '/../', '\\..\\', '..', '/..', '\\..']
        for seq in dangerous_sequences:
            if seq in test_name:
                if self.logger:
                    self.logger.warning(f"SECURITY: Path traversal detected in test name: {test_name}")
                test_name = test_name.replace(seq, '_')
        
        # Replace dangerous characters with underscores
        safe_test_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', test_name)
        
        # Ensure it doesn't start with special characters
        safe_test_name = re.sub(r'^[^a-zA-Z0-9]', 'test_', safe_test_name)
        
        if test_name != safe_test_name:
            if self.logger:
                self.logger.info(f"SECURITY: Test name sanitized: '{test_name}' -> '{safe_test_name}'")
        
        return safe_test_name
    
    @staticmethod
    def secure_log_directory(dir_path):
        """
        Set secure permissions on a log directory.
        
        Sets 0o2750 (rwxr-s---) and transfers group ownership to the
        original (non-root) user so that SSH sessions can still read
        logs without granting world access. The setgid bit ensures that
        new log files created in this directory inherit the directory's
        group, allowing access via group permissions.
        """
        os.makedirs(dir_path, exist_ok=True)
        os.chmod(dir_path, 0o2750)

        # When running under sudo, transfer group ownership to the real user
        # so the non-root SSH session can read logs via group permissions
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and os.getuid() == 0:
            try:
                import pwd
                pw = pwd.getpwnam(sudo_user)
                os.chown(dir_path, -1, pw.pw_gid)  # Keep owner, set group
            except (KeyError, OSError, ImportError):
                pass  # Best-effort; non-critical if it fails

    def validate_user_env_var(self, user_var=None):
        """Validate USER environment variable for safe command usage - prevents command injection."""
        import re
        
        # Get user from parameter or environment
        if user_var is None:
            user_var = os.getenv("USER", "compat")
        
        if not isinstance(user_var, str):
            user_var = str(user_var)
        
        user_var = user_var.strip()
        
        if not user_var:
            if self.logger:
                self.logger.warning("SECURITY: Empty USER environment variable, using 'compat'")
            return "compat"
        
        # Strict validation: only alphanumeric, underscore, hyphen, limit length to 32 chars
        if not re.match(r'^[a-zA-Z0-9_-]{1,32}$', user_var):
            if self.logger:
                self.logger.warning(f"SECURITY: Invalid USER environment variable format: {user_var}")
            # Log the dangerous content but return safe default
            safe_chars = re.sub(r'[^a-zA-Z0-9_-]', '', user_var[:32])
            if safe_chars:
                if self.logger:
                    self.logger.info(f"SECURITY: USER sanitized: '{user_var}' -> '{safe_chars}'")
                return safe_chars
            else:
                if self.logger:
                    self.logger.warning("SECURITY: USER completely invalid, using 'compat'")
                return "compat"
        
        # Length check
        if len(user_var) > 32:
            if self.logger:
                self.logger.warning(f"SECURITY: USER environment variable too long: {len(user_var)} chars")
            return user_var[:32]
        
        return user_var
    
    def validate_sudo_user_env_var(self, sudo_user_var=None):
        """Validate SUDO_USER environment variable for safe usage - prevents privilege escalation."""
        import re
        
        # Get user from parameter or environment
        if sudo_user_var is None:
            sudo_user_var = os.getenv("SUDO_USER")
        
        if not sudo_user_var:
            # SUDO_USER not set, this is normal for non-sudo operations
            return None
            
        if not isinstance(sudo_user_var, str):
            sudo_user_var = str(sudo_user_var)
        
        sudo_user_var = sudo_user_var.strip()
        
        # Strict validation: only alphanumeric, underscore, hyphen
        if not re.match(r'^[a-zA-Z0-9_-]{1,32}$', sudo_user_var):
            if self.logger:
                self.logger.warning(f"SECURITY: Invalid SUDO_USER environment variable format: {sudo_user_var}")
            raise ValueError(f"Invalid SUDO_USER format: {sudo_user_var}")
        
        # Length check
        if len(sudo_user_var) > 32:
            if self.logger:
                self.logger.warning(f"SECURITY: SUDO_USER environment variable too long: {len(sudo_user_var)} chars")
            raise ValueError(f"SUDO_USER too long (max 32 chars): {sudo_user_var}")
        
        return sudo_user_var
    
    def validate_process_id(self, pid):
        """Validate process ID for safe command usage - prevents command injection."""
        # Check for float values first (before int conversion)
        if isinstance(pid, float):
            if self.logger:
                self.logger.warning(f"SECURITY: Process ID cannot be a float: {pid}")
            raise ValueError(f"Process ID must be an integer, not float: {pid}")
        
        try:
            pid_int = int(pid)
        except (ValueError, TypeError):
            if self.logger:
                self.logger.warning(f"SECURITY: Invalid process ID format: {pid}")
            raise ValueError(f"Process ID must be an integer: {pid}")
        
        # Valid PID range check (typical Linux limit is 2^22 = 4194304)
        if pid_int <= 0:
            if self.logger:
                self.logger.warning(f"SECURITY: Process ID must be positive: {pid_int}")
            raise ValueError(f"Process ID must be positive: {pid_int}")
        
        if pid_int > 4194304:  # 2^22, typical Linux PID_MAX
            if self.logger:
                self.logger.warning(f"SECURITY: Process ID too large: {pid_int}")
            raise ValueError(f"Process ID too large (max 4194304): {pid_int}")
        
        return pid_int


class BashScriptWrapper:
    """
    A wrapper class to call bash script functions from Python.
    Provides efficient function calling with proper error handling.
    """
    
    def __init__(self, script_path, logger=None):
        self.script_path = os.path.abspath(script_path)
        self.logger = logger
        self.utils = Utils(logger)
        
        if not os.path.exists(self.script_path):
            raise FileNotFoundError(f"Bash script not found: {self.script_path}")
        
        # Ensure script is executable
        if not self.utils.make_script_executable(self.script_path):
            if self.logger:
                self.logger.warning(f"Could not make script executable: {self.script_path}")
    
    def _validate_function_name(self, function_name):
        """Validate function name to prevent command injection."""
        import re
        
        if not isinstance(function_name, str):
            if self.logger:
                self.logger.warning("SECURITY: Function name validation failed - not a string")
            raise ValueError("Function name must be a string")
        
        if not function_name:
            if self.logger:
                self.logger.warning("SECURITY: Function name validation failed - empty name")
            raise ValueError("Function name cannot be empty")
        
        if len(function_name) > 50:
            if self.logger:
                self.logger.warning(f"SECURITY: Function name validation failed - too long: {function_name}")
            raise ValueError("Function name too long (max 50 chars)")
        
        # Only allow valid bash function names
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', function_name):
            if self.logger:
                self.logger.warning(f"SECURITY: Function name validation failed - invalid characters: {function_name}")
            raise ValueError(
                f"Invalid function name '{function_name}'. "
                "Must start with letter/underscore and contain only alphanumeric/underscore characters"
            )
        
        # Check for dangerous characters
        dangerous_chars = [';', '&', '|', '$', '`', '(', ')', '<', '>', '"', "'", '\\', '\n', '\r']
        for char in dangerous_chars:
            if char in function_name:
                if self.logger:
                    self.logger.error(f"SECURITY: Function name contains dangerous character '{char}': {function_name}")
                raise ValueError(f"Function name contains dangerous character: {char}")
    
    def _validate_and_sanitize_arguments(self, args):
        """Validate and sanitize bash function arguments."""
        if not isinstance(args, (list, tuple)):
            if self.logger:
                self.logger.warning("SECURITY: Arguments validation failed - not a list/tuple")
            raise ValueError("Arguments must be a list or tuple")
        
        total_length = 0
        sanitized_args = []
        
        for i, arg in enumerate(args):
            if not isinstance(arg, str):
                if self.logger:
                    self.logger.warning(f"SECURITY: Argument {i} validation failed - not a string")
                raise ValueError(f"Argument {i} must be a string, got {type(arg)}")
            
            if len(arg) > 1000:
                if self.logger:
                    self.logger.warning(f"SECURITY: Argument {i} too long: {len(arg)} chars")
                raise ValueError(f"Argument {i} too long (max 1000 chars)")
            
            total_length += len(arg)
            # Sanitize using shlex.quote for shell safety
            sanitized_args.append(shlex.quote(arg))
        
        if total_length > 5000:
            if self.logger:
                self.logger.warning(f"SECURITY: Total arguments too long: {total_length} chars")
            raise ValueError("Total arguments length too long (max 5000 chars)")
        
        return sanitized_args
    
    def call_function(self, function_name, *args, **kwargs):
        """
        Call a bash function with arguments.
        
        Args:
            function_name (str): Name of the bash function to call
            *args: Positional arguments to pass to the function
            **kwargs: 
                - cwd (str): Working directory (optional)
                - env (dict): Environment variables (optional)
                - timeout (int): Timeout in seconds (optional)
        
        Returns:
            str: Function output (stdout)
            
        Raises:
            subprocess.CalledProcessError: If function fails
        """
        # Validate function name to prevent command injection
        self._validate_function_name(function_name)
        
        # Validate and sanitize arguments (convert *args to list)
        args_list = list(args)
        safe_args = self._validate_and_sanitize_arguments(args_list)
        args_str = ' '.join(safe_args)
        
        # Construct command with validated inputs using shlex.quote for script path
        command = f'source {shlex.quote(self.script_path)} && {function_name} {args_str}'
        
        # Extract kwargs
        cwd = kwargs.get('cwd', None)
        env = kwargs.get('env', None)
        timeout = kwargs.get('timeout', 30)
        
        try:
            result = subprocess.run(
                ['bash', '-c', command],
                capture_output=True,
                text=True,
                cwd=cwd,
                env=env,
                timeout=timeout,
                check=True
            )
            return result.stdout.strip()
            
        except subprocess.CalledProcessError as e:
            # For _checkRunlevel, don't log errors - let the calling code handle messaging
            if function_name == '_checkRunlevel':
                # Just raise RuntimeError without logging - the calling code handles user interaction
                raise RuntimeError(f"Function returned {e.returncode}") from e
            else:
                # For other functions, use the generic error format
                error_msg = f"Bash function '{function_name}' failed: {e.stderr.strip()}"
                if self.logger:
                    self.logger.error(error_msg)
                raise RuntimeError(error_msg) from e
            
        except subprocess.TimeoutExpired as e:
            error_msg = f"Bash function '{function_name}' timed out after {timeout}s"
            if self.logger:
                self.logger.error(error_msg)
            raise RuntimeError(error_msg) from e
    
    def has_function(self, function_name):
        """Check if a function exists in the bash script."""
        try:
            self._validate_function_name(function_name)
            command = f'source {shlex.quote(self.script_path)} && declare -f {function_name} > /dev/null'
            subprocess.run(['bash', '-c', command], check=True, capture_output=True, timeout=10)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
            return False
    
    def list_functions(self):
        """List all functions defined in the bash script."""
        try:
            command = f'source {shlex.quote(self.script_path)} && declare -F | awk "{{print $3}}"'
            result = subprocess.run(['bash', '-c', command], capture_output=True, text=True, check=True, timeout=10)
            
            # Filter to only return valid function names
            functions = []
            for func in result.stdout.split('\n'):
                func = func.strip()
                if func:
                    try:
                        self._validate_function_name(func)
                        functions.append(func)
                    except ValueError:
                        # Skip invalid function names
                        if self.logger:
                            self.logger.warning(f"Skipping invalid function name: {func}")
                        continue
            return functions
            
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return []


class BashScriptManager:
    """
    Manager class for handling multiple bash scripts via BashScriptWrapper instances.
    Provides a registry pattern for organizing and calling functions from different scripts.
    """
    
    def __init__(self, logger=None):
        self.logger = logger
        self._bash_scripts = {}
    
    def register_script(self, script_name, script_path):
        """
        Register a bash script for later use.
        
        Args:
            script_name (str): Name to identify this script (e.g., 'deviceDetect')
            script_path (str): Absolute path to the bash script file
            
        Returns:
            bool: True if registration successful, False otherwise
        """
        try:
            if os.path.exists(script_path):
                # Ensure script is executable before registration
                utils_instance = Utils(self.logger) if not hasattr(self, '_utils_instance') else self._utils_instance
                if not hasattr(self, '_utils_instance'):
                    self._utils_instance = utils_instance
                    
                if not utils_instance.make_script_executable(script_path):
                    if self.logger:
                        self.logger.warning(f"Could not make script executable during registration: {script_path}")
                
                self._bash_scripts[script_name] = BashScriptWrapper(script_path, self.logger)
                return True
            else:
                if self.logger:
                    self.logger.warning(f"Script not found for registration: {script_path}")
                return False
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to register script '{script_name}': {e}")
            return False
    
    def call_function(self, script_name, function_name, *args, **kwargs):
        """
        Call a bash function from a registered script.
        
        Args:
            script_name (str): Name of the registered script
            function_name (str): Name of the function to call
            *args: Arguments to pass to the function
            **kwargs: Additional options (cwd, env, timeout)
            
        Returns:
            str: Function output
            
        Raises:
            ValueError: If script is not registered
        """
        if script_name not in self._bash_scripts:
            raise ValueError(f"Script '{script_name}' not registered. Available: {list(self._bash_scripts.keys())}")
        
        return self._bash_scripts[script_name].call_function(function_name, *args, **kwargs)
    
    def has_script(self, script_name):
        """Check if a script is registered."""
        return script_name in self._bash_scripts
    
    def get_script_wrapper(self, script_name):
        """Get the BashScriptWrapper instance for a registered script."""
        return self._bash_scripts.get(script_name)
    
    def list_registered_scripts(self):
        """Get list of all registered script names."""
        return list(self._bash_scripts.keys())

    # Tool Installation Methods
    def installPTAT(self):
        """
        Install PTAT (Intel Power Thermal Awareness Tool) if not already installed.
        
        Returns:
            str: Path to PTAT installation directory
        """
        def find_ptat_installation():
            # Look for PTAT installation in tools directory
            current_dir = os.path.dirname(os.path.abspath(__file__))
            tools_folder = os.path.join(os.path.dirname(current_dir), 'tools')
            ptat_pattern = os.path.join(tools_folder, 'PTAT_*')
            ptat_dirs = glob.glob(ptat_pattern)
            
            if ptat_dirs:
                # Return the first (and likely only) PTAT directory found
                return os.path.abspath(ptat_dirs[0])
            
            return None
        
        ptat_path = find_ptat_installation()
        
        if ptat_path and os.path.exists(ptat_path):
            if self.logger:
                self.logger.info(f"PTAT found at: {ptat_path}")
            return ptat_path
        
        if self.logger:
            self.logger.error("PTAT installation not found")
            self.logger.info("Please ensure PTAT is installed in tools/PTAT_* directory")
        
        return None
    
    def _copy_dgdiag_config(self, toolsFolder, dgdiag_installation_path, version=None):
        """Copy dgdiagconfig.bin to DGDiag installation directory."""
        try:
            # Determine version folder based on provided version
            if version:
                version_folder = f'DGDiag_{version}'
            else:
                version_folder = 'DGDiag_3.10.2'  # Default fallback
            
            config_source = os.path.join(toolsFolder, version_folder, 'dgdiagconfig.bin')
            if os.path.exists(config_source):
                config_dest = os.path.join(dgdiag_installation_path, 'dgdiagconfig.bin')
                shutil.copy2(config_source, config_dest)
            else:
                if self.logger:
                    self.logger.warning(f"dgdiagconfig.bin not found at: {config_source}")
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to copy dgdiagconfig.bin: {e}")

    def installDGDiag(self, required_version=None, verbose=True):
        """
        Install DGDiag (Intel GPU Diagnostic Tool) with version checking.
        
        Args:
            required_version (str): The required DGDiag version (e.g., '3.10.2')
            verbose (bool): If True, shows detailed installation progress
            
        Returns:
            str: The absolute path to the DGDiag installation directory
        """ 
        
        def find_dgdiag_installation():
            # Check possible DGDiag installation locations - these are the actual executables
            possible_executables = [
                "/opt/Intel Corporation/DGDiagTool",
                "/opt/Intel Corporation/DGDiagTool_Internal"
            ]
            
            for dgdiag_executable in possible_executables:
                if os.path.exists(dgdiag_executable):
                    # Return the directory containing the executable
                    installation_dir = os.path.dirname(dgdiag_executable)
                    return installation_dir
                    
            if verbose:
                self.logger.warning("DGDiag not found in any expected location")
            return None
        
        def get_installed_dgdiag_version(dgdiag_path):
            """Get version of installed DGDiag by reading from executable."""
            try:
                dgdiag_exe = os.path.join(dgdiag_path, "DGDiagTool")
                if not os.path.exists(dgdiag_exe):
                    return None
                
                # Run DGDiag version command
                result = subprocess.run([dgdiag_exe, "-version"], 
                                      capture_output=True, text=True, timeout=10)
                
                if result.returncode == 0:
                    # Parse version from output - typically "DGDiag Version: 3.10.2"
                    for line in result.stdout.split('\\n'):
                        if 'version' in line.lower():
                            # Extract version number using regex
                            import re
                            version_match = re.search(r'(\\d+\\.\\d+(?:\\.\\d+)?)', line)
                            if version_match:
                                return version_match.group(1)
                return None
            except Exception as e:
                if verbose and self.logger:
                    self.logger.warning(f"Failed to get DGDiag version: {e}")
                return None
        
        def uninstall_dgdiag():
            """Uninstall existing DGDiag installation."""
            try:
                if verbose and self.logger:
                    self.logger.info("Uninstalling existing DGDiag installation...")
                
                # Find and remove existing DGDiag installation
                existing_path = find_dgdiag_installation()
                if existing_path and os.path.exists(existing_path):
                    shutil.rmtree(existing_path)
                    if verbose and self.logger:
                        self.logger.info(f"Removed: {existing_path}")
                
                return True
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Failed to uninstall DGDiag: {e}")
                return False
        
        # Find current directory and tools folder
        current_dir = os.path.dirname(os.path.abspath(__file__))
        toolsFolder = os.path.join(os.path.dirname(current_dir), 'tools')
        
        # Check if DGDiag is already installed with correct version
        existing_dgdiag_path = find_dgdiag_installation()
        if existing_dgdiag_path:
            # Ensure the executable has proper permissions
            dgdiag_executable = os.path.join(existing_dgdiag_path, "DGDiagTool")
            if os.path.exists(dgdiag_executable):
                try:
                    os.chmod(dgdiag_executable, 0o750)
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Failed to set DGDiag permissions: {e}")
            
            if required_version:
                installed_version = get_installed_dgdiag_version(existing_dgdiag_path)
                if installed_version == required_version:
                    if verbose and self.logger:
                        self.logger.info(f"DGDiag version {required_version} already installed at: {existing_dgdiag_path}")
                    # Still copy config file in case it's missing
                    self._copy_dgdiag_config(toolsFolder, existing_dgdiag_path, required_version)
                    return existing_dgdiag_path
                else:
                    if verbose and self.logger:
                        self.logger.info(f"DGDiag version mismatch. Installed: {installed_version}, Required: {required_version}")
                    # Uninstall and continue with fresh installation
                    uninstall_dgdiag()
            else:
                # No specific version required, use existing installation
                if verbose and self.logger:
                    self.logger.info(f"DGDiag found at: {existing_dgdiag_path}")
                # Copy config file
                self._copy_dgdiag_config(toolsFolder, existing_dgdiag_path, required_version)
                return existing_dgdiag_path

        # Install DGDiag if not found or after uninstall
        # Set installation path for new installation
        dgdiag_extraction_path = "/opt/Intel Corporation/DGDiagTool"
        version_msg = f" version {required_version}" if required_version else ""
        if verbose and self.logger:
            self.logger.info(f"DGDiag{version_msg} is not installed, installing it now, this will take a few mins...")
        
        # Determine installation paths based on required version
        if required_version:
            version_folder = f'DGDiag_{required_version}'
            zip_filename = f'dgdiag_{required_version}.zip'
        else:
            # Fallback to hardcoded version if not specified
            version_folder = 'DGDiag_3.10.2'
            zip_filename = 'dgdiag_3.10.2.zip'
        
        # Find DGDiag zip file
        zip_path = os.path.join(toolsFolder, version_folder, zip_filename)
        
        if not os.path.exists(zip_path):
            if self.logger:
                self.logger.error(f"DGDiag zip file not found: {zip_path}")
                self.logger.info("Please ensure DGDiag zip file is available in tools directory")
            return None

        try:
            # Create installation directory
            os.makedirs(dgdiag_extraction_path, exist_ok=True)
            
            # Extract DGDiag
            if verbose and self.logger:
                self.logger.info(f"Extracting DGDiag from: {zip_path}")
                self.logger.info(f"Installing to: {dgdiag_extraction_path}")
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(dgdiag_extraction_path)
            
            # Find and make DGDiagTool executable (it might be in a subdirectory)
            dgdiag_tool_candidates = glob.glob(os.path.join(dgdiag_extraction_path, "**/DGDiagTool"), recursive=True)
            
            if dgdiag_tool_candidates:
                dgdiag_tool_path = dgdiag_tool_candidates[0]
                os.chmod(dgdiag_tool_path, 0o750)
                if verbose and self.logger:
                    self.logger.info(f"Made DGDiagTool executable at: {dgdiag_tool_path}")
                
                # If DGDiagTool is in a subdirectory, create a symlink at the root level
                root_dgdiag_path = os.path.join(dgdiag_extraction_path, "DGDiagTool")
                if dgdiag_tool_path != root_dgdiag_path and not os.path.exists(root_dgdiag_path):
                    os.symlink(dgdiag_tool_path, root_dgdiag_path)
                    if verbose and self.logger:
                        self.logger.info(f"Created symlink: {root_dgdiag_path} -> {dgdiag_tool_path}")
            else:
                if self.logger:
                    self.logger.warning("DGDiagTool executable not found in extracted files")
            
            # Copy configuration file
            self._copy_dgdiag_config(toolsFolder, dgdiag_extraction_path, required_version)
            
            if verbose and self.logger:
                self.logger.info("DGDiag installation completed successfully")
            
            return dgdiag_extraction_path
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to install DGDiag: {e}")
            return None



def _safe_is_tty(stream: TextIO) -> bool:
    """
    Best-effort TTY detection that works even when the stream is a Tee-like wrapper
    with no `isatty()`. Returns False if uncertain.
    """
    # 1) has isatty attribute?
    isatty = getattr(stream, "isatty", None)
    if callable(isatty):
        try:
            return bool(isatty())
        except (AttributeError, OSError, IOError):
            pass

    # 2) has fileno -> use os.isatty
    fileno = getattr(stream, "fileno", None)
    if callable(fileno):
        try:
            return os.isatty(fileno())
        except (OSError, ValueError):
            pass

    # Unknown → assume not a TTY (e.g., CI logs, redirected)
    return False


class Spinner:

    """
    Terminal spinner that animates on the same line when TTY is available.
    In non-TTY contexts, can emit a periodic heartbeat (dot or callback).
    """
    def __init__(
        self,
        prefix: str = "Working ",
        frames: Optional[List[str]] = None,
        interval: float = 0.15,
        stream: TextIO = sys.stdout,
        enable_when_not_tty: bool = False,
        heartbeat_interval: float = 1.0,
        heartbeat: Optional[Callable[[TextIO], None]] = None,
        clear_on_stop: bool = True,
    ):

        self.prefix = prefix
        self.frames = frames or ["|", "/", "-", "\\"]
        self.interval = interval
        self.stream = stream
        self.enable_when_not_tty = enable_when_not_tty
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat = heartbeat
        self.clear_on_stop = clear_on_stop

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._active = False

        # Cache TTY decision using safe detection
        self._is_tty = _safe_is_tty(self.stream)

    @property
    def is_running(self) -> bool:
        return self._active

    def _write(self, s: str):
        try:
            self.stream.write(s)
            self.stream.flush()
        except (IOError, OSError, BrokenPipeError):
            # Silently ignore write errors (e.g., broken pipe in some loggers)
            pass

    def _clear_line(self):
        if not self._is_tty:
            return
        # Clear current line and return carriage (simple approach)
        width = max(len(self.prefix) + 8, 16)  # enough to overwrite frames
        self._write("\r" + " " * width + "\r")

    def _animate(self):
        if self._is_tty:
            i = 0
            while not self._stop_event.is_set():
                frame = self.frames[i % len(self.frames)]
                self._write(f"\r{self.prefix}{frame}")
                time.sleep(self.interval)
                i += 1
            if self.clear_on_stop:
                self._clear_line()
        else:
            # Non-TTY mode
            if not self.enable_when_not_tty:
                # Remain silent but responsive to stop
                while not self._stop_event.wait(0.25):
                    pass
                return

            # Heartbeat mode (default: dot)
            hb = self.heartbeat or (lambda st: self._write("."))
            last = 0.0
            while not self._stop_event.is_set():
                now = time.time()
                if now - last >= self.heartbeat_interval:
                    hb(self.stream)
                    last = now
                # Sleep in small chunks to be responsive
                time.sleep(0.05)

    def start(self):
        with self._lock:
            if self._active:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._animate, name="Spinner", daemon=True)
            self._thread.start()
            self._active = True

    def stop(self):
        with self._lock:
            if not self._active:
                return
            self._stop_event.set()
            if self._thread is not None:
                self._thread.join()
            self._thread = None
            self._active = False
