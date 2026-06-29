# Copyright (C) 2024-2026 Intel Corporation
# Verification Test Suite
# System Checker - Pre-test system validation framework

import os
import subprocess # nosec
import sys
import platform
import shutil
from enum import Enum
from typing import List, Tuple, Callable, Optional
from dataclasses import dataclass


class CheckSeverity(Enum):
    """Severity levels for system checks"""
    CRITICAL = "CRITICAL"  # Must pass or VTS stops
    WARNING = "WARNING"    # Can continue with user confirmation
    INFO = "INFO"         # Informational only, always continues


@dataclass
class CheckResult:
    """Result of a system check"""
    name: str
    severity: CheckSeverity
    passed: bool
    message: str
    details: Optional[str] = None
    suggestion: Optional[str] = None


class SystemChecker:
    """
    Pre-test system validation framework.
    Extensible framework for running system checks before VTS execution.
    """
    
    def __init__(self, logger):
        self.logger = logger
        self.checks: List[Tuple[str, CheckSeverity, Callable[[], Tuple[bool, str, str]]]] = []
        self.results: List[CheckResult] = []
        
        # Initialize utils instance for reuse across checks
        try:
            from . import utils
            self.utils = utils.Utils(self.logger)
        except ImportError:
            self.utils = None
        
        # Register default system checks
        self._register_default_checks()
    
    def _register_default_checks(self):
        """Register the default system checks"""
        # Critical checks - VTS cannot continue if these fail
        self.register_check("OS Platform", CheckSeverity.CRITICAL, self._check_os_platform)
        self.register_check("Python Version", CheckSeverity.CRITICAL, self._check_python_version)
        self.register_check("Required Commands", CheckSeverity.CRITICAL, self._check_required_commands)
        self.register_check("Intel GPU Drivers", CheckSeverity.CRITICAL, self._check_intel_gpu_drivers)
        
        # Warning checks - user can choose to continue
        self.register_check("Total Installed Memory", CheckSeverity.WARNING, self._check_memory)
        self.register_check("Available Disk Space", CheckSeverity.WARNING, self._check_disk_space)
        self.register_check("System Load", CheckSeverity.WARNING, self._check_system_load)
        self.register_check("PCIe Alignment", CheckSeverity.WARNING, self._check_pcie_alignment)
        self.register_check("Previous Test Cleanup", CheckSeverity.WARNING, self._check_leftover_processes)
        
        # Info checks - informational only
        self.register_check("System Information", CheckSeverity.INFO, self._check_system_info)
    
    def register_check(self, name: str, severity: CheckSeverity, check_func: Callable[[], Tuple[bool, str, str]]):
        """
        Register a new system check.
        
        Args:
            name: Human-readable name for the check
            severity: CheckSeverity level 
            check_func: Function that returns (passed: bool, message: str, suggestion: str)
        """
        self.checks.append((name, severity, check_func))
    
    def run_all_checks(self) -> bool:
        """
        Run all registered system checks.
        
        Returns:
            bool: True if VTS can continue, False if critical checks failed
        """
        self.logger.subheader("SYSTEM PRE-FLIGHT CHECKS")
        
        self.results = []
        critical_failures = []
        warning_failures = []
        
        # Run all checks
        for name, severity, check_func in self.checks:
            try:
                passed, message, suggestion = check_func()
                result = CheckResult(
                    name=name,
                    severity=severity,
                    passed=passed,
                    message=message,
                    suggestion=suggestion
                )
                self.results.append(result)
                
                # Log the result
                status_icon = "✓" if passed else "✗"
                severity_text = f"[{severity.value}]"
                
                if passed:
                    # Use pass_msg for green color on all passing checks
                    self.logger.pass_msg(f"{status_icon} {name}: PASS")
                    if message:
                        self.logger.info(f"    {message}")
                else:
                    if severity == CheckSeverity.CRITICAL:
                        self.logger.fail_msg(f"{status_icon} {name}: FAIL {severity_text}")
                        self.logger.error(f"    {message}")
                        if suggestion:
                            self.logger.error(f"    SOLUTION: {suggestion}")
                        critical_failures.append(result)
                    elif severity == CheckSeverity.WARNING:
                        self.logger.warning(f"{status_icon} {name}: FAIL {severity_text}")
                        self.logger.warning(f"    {message}")
                        if suggestion:
                            self.logger.warning(f"    RECOMMENDATION: {suggestion}")
                        warning_failures.append(result)
                    else:  # INFO
                        self.logger.info(f"{status_icon} {name}: {message}")
                        
            except Exception as e:
                # Handle check function exceptions
                result = CheckResult(
                    name=name,
                    severity=severity,
                    passed=False,
                    message=f"Check failed with exception: {e}",
                    suggestion="Review system configuration"
                )
                self.results.append(result)
                
                if severity == CheckSeverity.CRITICAL:
                    self.logger.fail_msg(f"✗ {name}: ERROR [CRITICAL]")
                    self.logger.error(f"    Check failed with exception: {e}")
                    critical_failures.append(result)
                else:
                    self.logger.warning(f"✗ {name}: ERROR")
                    self.logger.warning(f"    Check failed with exception: {e}")
        
        # Handle results
        if critical_failures:
            self.logger.info("")
            self.logger.error("CRITICAL SYSTEM CHECKS FAILED")
            self.logger.error("VTS cannot continue with critical system issues.")
            self.logger.error("Please resolve the above issues and try again.")
            self.logger.fail_msg("✗ System pre-flight checks FAILED")
            return False
        
        if warning_failures:
            self.logger.info("")
            self.logger.warning("WARNING: System checks detected potential issues.")
            if not self._handle_warning_failures(warning_failures):
                self.logger.info("User chose to abort due to system warnings.")
                self.logger.warning("⚠ System pre-flight checks ABORTED by user")
                return False
        
        self.logger.info("")
        if warning_failures:
            # Completed with warnings - use warning color
            self.logger.warning("⚠ System pre-flight checks completed with warnings")
        else:
            # Completed successfully - use green color  
            self.logger.pass_msg("✓ System pre-flight checks completed successfully")
        return True
    
    def _handle_warning_failures(self, warning_failures: List[CheckResult]) -> bool:
        """
        Handle warning-level failures with user interaction.
        
        Returns:
            bool: True if user wants to continue, False to abort
        """
        self.logger.warning(f"Found {len(warning_failures)} warning-level issues:")
        for failure in warning_failures:
            self.logger.warning(f"  • {failure.name}: {failure.message}")
        
        self.logger.info("")
        # Avoid blocking on input() in non-interactive environments (e.g., CI)
        if not sys.stdin.isatty():
            self.logger.warning(
                "Non-interactive environment detected; aborting due to warning-level issues "
                "without user confirmation."
            )
            return False

        while True:
            try:
                response = input("Do you want to continue despite these warnings? [y/N]: ").strip().lower()
                if response in ['y', 'yes']:
                    self.logger.info("User chose to continue despite warnings.")
                    return True
                elif response in ['n', 'no', '']:
                    return False
                else:
                    print("Please enter 'y' for yes or 'n' for no.")
            except (KeyboardInterrupt, EOFError):
                print()
                self.logger.info("User interrupted. Aborting.")
                return False
    
    # System Check Methods
    # ====================
    
    def _check_os_platform(self) -> Tuple[bool, str, str]:
        """Check if running on supported OS platform"""
        current_os = platform.system()
        if current_os == "Linux":
            # Get detailed Linux distribution information
            try:
                # Read /etc/os-release for distribution details
                with open('/etc/os-release', 'r') as f:
                    os_info = {}
                    for line in f:
                        if '=' in line:
                            key, value = line.strip().split('=', 1)
                            # Remove quotes from value
                            os_info[key] = value.strip('"')
                
                # Build detailed OS description
                distro_name = os_info.get('NAME', 'Linux')
                version = os_info.get('VERSION', os_info.get('VERSION_ID', ''))
                
                if version:
                    os_description = f"{distro_name} {version}"
                else:
                    os_description = distro_name
                    
                return True, f"Running on {os_description}", ""
                
            except (IOError, OSError, KeyError):
                # Fallback to basic Linux if can't read distribution info
                return True, f"Running on {current_os}", ""
        else:
            return False, f"Unsupported OS: {current_os}", "VTS requires Linux. Consider using WSL on Windows."
    
    def _check_python_version(self) -> Tuple[bool, str, str]:
        """Check Python version compatibility"""
        version = sys.version_info
        if version.major == 3 and version.minor >= 8:
            return True, f"Python {version.major}.{version.minor}.{version.micro} (≥3.8 required)", ""
        else:
            return False, f"Python {version.major}.{version.minor} detected (3.8+ required)", "Install Python 3.8 or newer"
    
    def _check_required_commands(self) -> Tuple[bool, str, str]:
        """Check for required system commands"""
        required_commands = ['sudo', 'pkill', 'pgrep', 'lspci', 'lscpu', 'lsmod', 'modinfo']
        missing_commands = []
        
        for cmd in required_commands:
            if shutil.which(cmd) is None:
                missing_commands.append(cmd)
        
        if not missing_commands:
            return True, f"All required commands available ({len(required_commands)} checked)", ""
        else:
            return False, f"Missing commands: {', '.join(missing_commands)}", "Install missing system utilities"
    
    def _check_memory(self) -> Tuple[bool, str, str]:
        """Check total installed system memory using lsmem"""
        try:
            # Use lsmem to get total installed memory (more accurate than /proc/meminfo)
            result = subprocess.run(['lsmem', '-b', '--summary=only'], 
                                  capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                # Fallback to /proc/meminfo if lsmem fails
                with open('/proc/meminfo', 'r') as f:
                    for line in f:
                        if line.startswith('MemTotal:'):
                            # Extract memory in KB and convert to GB
                            mem_kb = int(line.split()[1])
                            mem_gb = mem_kb / (1024 * 1024)
                            break
                    else:
                        return False, "Could not determine memory status", "lsmem failed and /proc/meminfo unavailable"
            else:
                # Parse lsmem output to get total memory in bytes
                for line in result.stdout.strip().split('\n'):
                    if 'Total online memory:' in line:
                        # Extract memory value (format: "Total online memory: 534762176512")
                        mem_bytes = int(line.split(':')[1].strip())
                        mem_gb = mem_bytes / (1024 * 1024 * 1024)
                        break
                else:
                    return False, "Could not parse lsmem output", "Check lsmem command manually"
            
            # Try to get GPU memory requirements for B70s
            total_gpu_memory_gb = None
            system_memory_speed_mhz = None
            if self.utils:
                try:
                    total_gpu_memory_gb = self.utils.get_detected_gpu_memory_gb()
                    system_memory_speed_mhz = self.utils.get_system_memory_speed_mhz()
                except Exception as e:
                    self.logger.warning(f"GPU/memory detection failed: {e}")
            
            if total_gpu_memory_gb is not None:
                # B70-based memory requirement: 2x total GPU memory
                required_memory_gb = total_gpu_memory_gb * 2.0
                
                # Check system memory speed if available
                speed_check_msg = ""
                speed_meets_requirement = True
                speed_warning = ""
                
                if system_memory_speed_mhz is not None:
                    # Check if system memory speed meets minimum requirement (4800 MHz = DDR5-4800)
                    if system_memory_speed_mhz >= 4800:
                        speed_check_msg = f", System RAM Speed: {system_memory_speed_mhz:.0f} MHz (≥4800 MHz required)"
                        speed_meets_requirement = True
                    else:
                        # Memory speed is below threshold - this is a failure condition
                        speed_check_msg = f", System RAM Speed: {system_memory_speed_mhz:.0f} MHz (≥4800 MHz required)"
                        speed_meets_requirement = False
                        speed_warning = f"System RAM speed {system_memory_speed_mhz:.0f} MHz below required 4800 MHz threshold for optimal performance"
                else:
                    # Speed detection failed - treat as warning, not failure
                    speed_check_msg = ", System RAM Speed: Unable to detect (≥4800 MHz required)"
                    speed_meets_requirement = True  # Don't fail due to detection issues
                    speed_warning = "Unable to verify RAM speed meets 4800 MHz requirement"
                
                # Overall pass/fail based on both capacity AND speed requirements
                capacity_ok = mem_gb >= required_memory_gb
                overall_pass = capacity_ok and speed_meets_requirement
                
                if overall_pass:
                    message = f"Total installed memory: {mem_gb:.1f} GB (≥{required_memory_gb:.1f} GB required for 2x GPU memory: {total_gpu_memory_gb:.1f} GB)\n            {speed_check_msg.lstrip(', ')}"
                    return True, message, speed_warning if speed_warning else ""
                else:
                    # Build failure message based on what failed
                    issues = []
                    if not capacity_ok:
                        issues.append(f"insufficient memory: {mem_gb:.1f} GB installed vs {required_memory_gb:.1f} GB required")
                    if not speed_meets_requirement:
                        issues.append(f"memory speed below 4800 MHz requirement")
                    
                    message = f"Memory validation failed: {'; '.join(issues)} (2x GPU memory: {total_gpu_memory_gb:.1f} GB){speed_check_msg}"
                    suggestion = []
                    if not capacity_ok:
                        suggestion.append("Add more RAM to meet 2x GPU memory requirement")
                    if not speed_meets_requirement:
                        suggestion.append("Upgrade to DDR5-4800 or faster memory for optimal performance")
                    
                    return False, message, "; ".join(suggestion)
            else:
                # Fallback to original logic for non-B70 systems
                if mem_gb >= 16:
                    return True, f"Total installed memory: {mem_gb:.1f} GB (≥16 GB required)", ""
                elif mem_gb >= 8:
                    return False, f"Low memory: {mem_gb:.1f} GB installed (16+ GB recommended, 8+ GB minimum)", "Consider adding more RAM"
                else:
                    return False, f"Very low memory: {mem_gb:.1f} GB installed (8+ GB minimum required)", "VTS requires more RAM to operate properly."

        except (IOError, OSError, ValueError, subprocess.SubprocessError, subprocess.TimeoutExpired) as e:
            return False, f"Memory check failed: {e}", "Verify lsmem command availability and system health"
    
    def _check_disk_space(self) -> Tuple[bool, str, str]:
        """Check available disk space in current directory"""
        try:
            stat = os.statvfs('.')
            # Calculate free space in GB
            free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
            
            if free_gb >= 50:
                return True, f"Available disk space: {free_gb:.1f} GB (≥50 GB recommended)", ""
            elif free_gb >= 20:
                return False, f"Low disk space: {free_gb:.1f} GB available (50+ GB recommended, 20+ GB minimum)", "Clean up disk space or use different directory"
            else:
                return False, f"Very low disk space: {free_gb:.1f} GB available (20+ GB minimum required)", "VTS logs and data require significant space. Free up disk space."
        except (OSError, AttributeError) as e:
            return False, f"Disk space check failed: {e}", "Verify filesystem health"
    
    def _check_system_load(self) -> Tuple[bool, str, str]:
        """Check system load average"""
        try:
            load1, load5, load15 = os.getloadavg()
            cpu_count = os.cpu_count() or 1
            
            # Load average relative to CPU count
            load_ratio = load1 / cpu_count
            
            if load_ratio <= 0.7:
                return True, f"System load: {load1:.2f} ({load_ratio:.1%} of {cpu_count} CPUs, ≤70% recommended)", ""
            elif load_ratio <= 2.0:
                return False, f"High system load: {load1:.2f} ({load_ratio:.1%} of {cpu_count} CPUs, >70% threshold)", "Consider waiting for system load to decrease"
            else:
                return False, f"Very high system load: {load1:.2f} ({load_ratio:.1%} of {cpu_count} CPUs, >200% critical)", "System may be overloaded. Check for runaway processes."
        except (OSError, AttributeError) as e:
            return False, f"Load check failed: {e}", "Check system performance manually"
    
    def _check_system_info(self) -> Tuple[bool, str, str]:
        """Gather comprehensive system information (always passes)"""
        try:
            # Try to get comprehensive system info using DGDiag if available
            if self.utils and hasattr(self.utils, 'sysinfo_collector') and self.utils.sysinfo_collector is not None:
                try:
                    system_info = self.utils.sysinfo_collector.collect_all_info()
                    
                    # Build comprehensive info message
                    info_parts = []
                    
                    # Basic host info
                    hostname = platform.node()
                    kernel = platform.release()
                    arch = platform.machine()
                    cpu_count = os.cpu_count()
                    info_parts.append(f"Host: {hostname}, Kernel: {kernel}, Arch: {arch}, CPUs: {cpu_count}")
                    
                    # Enhanced host system information from DGDiag
                    host_info = system_info.get('host_memory', {})
                    if host_info.get('processor_name'):
                        info_parts.append(f"Processor: {host_info['processor_name']}")
                    
                    if host_info.get('bios_vendor') and host_info.get('bios_version'):
                        bios_info = f"BIOS: {host_info['bios_vendor']} v{host_info['bios_version']}"
                        if host_info.get('bios_release_date'):
                            bios_info += f" ({host_info['bios_release_date']})"
                        info_parts.append(bios_info)
                    
                    # Hardware information from DGDiag OSInfo
                    os_info = system_info.get('os_info', {})
                    if os_info.get('hardware_vendor') and os_info.get('hardware_model'):
                        info_parts.append(f"Hardware: {os_info['hardware_vendor']} {os_info['hardware_model']}")
                    
                    # GPU information with enhanced OpenCL details
                    opencl_info = system_info.get('opencl', {})
                    if opencl_info.get('device_name'):
                        gpu_name = opencl_info['device_name']
                        driver_version = opencl_info.get('driver_version', 'Unknown')
                        info_parts.append(f"GPU: {gpu_name} (Driver: {driver_version})")
                        
                        # Add enhanced OpenCL information
                        if opencl_info.get('number_of_devices'):
                            info_parts.append(f"GPU Devices: {opencl_info['number_of_devices']}")
                        
                        if opencl_info.get('device_version'):
                            info_parts.append(f"OpenCL Version: {opencl_info['device_version']}")
                        
                        if opencl_info.get('max_clock_mhz'):
                            info_parts.append(f"Max GPU Clock: {opencl_info['max_clock_mhz']} MHz")
                        
                        if opencl_info.get('compute_units'):
                            info_parts.append(f"Compute Units: {opencl_info['compute_units']}")
                        
                        if opencl_info.get('global_memory_gb'):
                            gpu_mem_info = f"GPU Memory: {opencl_info['global_memory_gb']:.1f} GB"
                            if opencl_info.get('global_memory_bytes'):
                                gpu_mem_info += f" ({opencl_info['global_memory_bytes']:,} bytes)"
                            info_parts.append(gpu_mem_info)
                    
                    # SoC Revision
                    soc_info = system_info.get('soc', {})
                    if soc_info.get('soc_revision'):
                        info_parts.append(f"SoC: {soc_info['soc_revision']}")
                    
                    # Temperatures
                    temps = system_info.get('temperatures', {})
                    if temps.get('gpu_temp_c') and temps.get('memory_temp_c'):
                        info_parts.append(f"Temps: GPU {temps['gpu_temp_c']:.0f}°C, Memory {temps['memory_temp_c']:.0f}°C")
                    
                    # Power Limits
                    power_info = system_info.get('power', {})
                    if power_info.get('psys_power_limit2_w'):
                        info_parts.append(f"Power Limit: {power_info['psys_power_limit2_w']} W")
                    
                    # Display
                    display_info = system_info.get('display', {})
                    if display_info.get('resolution') and display_info.get('refresh_rate_hz'):
                        info_parts.append(f"Display: {display_info['resolution']} @ {display_info['refresh_rate_hz']} Hz")
                    
                    # Health Status
                    health = system_info.get('health', {})
                    health_issues = []
                    if health.get('yellow_bang_detected'):
                        health_issues.append("Device Manager issues")
                    if health.get('tdr_events_detected'):
                        health_issues.append("TDR events")
                    
                    if health_issues:
                        info_parts.append(f"Health Issues: {', '.join(health_issues)}")
                    
                    # Combine all info parts
                    comprehensive_info = '\n            '.join(info_parts)
                    return True, comprehensive_info, ""
                    
                except Exception as e:
                    self.logger.warning(f"DGDiag system info collection failed: {e}")
                    # Fall back to basic info
                    pass
            
            # Fallback to basic system info if DGDiag not available or failed
            hostname = platform.node()
            kernel = platform.release()
            arch = platform.machine()
            cpu_count = os.cpu_count()
            
            info = f"Host: {hostname}, Kernel: {kernel}, Arch: {arch}, CPUs: {cpu_count}"
            return True, info, ""
            
        except Exception as e:
            return True, f"System info collection failed: {e}", ""
    
    def _check_intel_gpu_drivers(self) -> Tuple[bool, str, str]:
        """Check if Intel GPU drivers are loaded"""
        try:
            # Check for xe or i915 driver
            result = subprocess.run(['lsmod'], capture_output=True, text=True, timeout=5)
            if 'xe' in result.stdout or 'i915' in result.stdout:
                # Determine which drivers are loaded and get their versions
                driver_info = []
                
                if 'xe' in result.stdout:
                    # Get xe driver version
                    xe_version = self._get_driver_version('xe')
                    if xe_version:
                        driver_info.append(f'xe ({xe_version})')
                    else:
                        driver_info.append('xe')
                        
                if 'i915' in result.stdout:
                    # Get i915 driver version
                    i915_version = self._get_driver_version('i915')
                    if i915_version:
                        driver_info.append(f'i915 ({i915_version})')
                    else:
                        driver_info.append('i915')
                
                return True, f"Intel GPU drivers detected: {', '.join(driver_info)}", ""
            else:
                return False, "Intel GPU drivers not found (xe or i915 required)", "Load xe or i915 kernel modules with 'sudo modprobe xe' or 'sudo modprobe i915'"
        except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            return False, f"Driver check failed: {e}", "Verify kernel modules with 'lsmod'"
    
    def _get_driver_version(self, driver_name: str) -> Optional[str]:
        """Get version information for a kernel driver"""
        try:
            # Method 1: Try modinfo for version
            result = subprocess.run(['modinfo', driver_name], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if line.startswith('version:'):
                        version = line.split(':', 1)[1].strip()
                        if version and version != '(null)':
                            return version
            
            # Method 2: Try /sys/module/<driver>/version
            version_file = f'/sys/module/{driver_name}/version'
            if os.path.exists(version_file):
                with open(version_file, 'r') as f:
                    version = f.read().strip()
                    if version and version != '(null)':
                        return version
            
            # Method 3: For xe driver, try kernel version (xe is often built-in to kernel)
            if driver_name == 'xe':
                kernel_version = platform.release()
                if kernel_version:
                    return f"kernel-{kernel_version}"
            
            # Method 4: Try srcversion as last resort (truncated)
            result = subprocess.run(['modinfo', driver_name], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if line.startswith('srcversion:'):
                        srcversion = line.split(':', 1)[1].strip()
                        if srcversion and len(srcversion) >= 8:
                            return f"src:{srcversion[:8]}"
            
            return None
            
        except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, FileNotFoundError, IOError, OSError):
            return None
    
    def _check_leftover_processes(self) -> Tuple[bool, str, str]:
        """Check for leftover processes from previous VTS runs"""
        try:
            # Check for common VTS-related processes
            processes_to_check = ['stress-ng', 'ptat', 'DGDiagTool', 'ze_peak', 'LTSSMtool']
            found_processes = []
            
            for process in processes_to_check:
                try:
                    result = subprocess.run(['pgrep', process], capture_output=True, text=True, timeout=3)
                    if result.returncode == 0 and result.stdout.strip():
                        # Get process count
                        pids = result.stdout.strip().split('\n')
                        found_processes.append(f"{process}({len(pids)})")
                except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired):
                    # pgrep returns non-zero when no processes found, which is expected
                    continue
            
            if not found_processes:
                return True, f"No leftover VTS processes detected ({len(processes_to_check)} checked)", ""
            else:
                return False, f"Leftover processes found: {', '.join(found_processes)}", "Clean up with: sudo pkill -f 'stress-ng|ptat|DGDiagTool|ze_peak|LTSSMtool'"
        except Exception as e:
            return False, f"Process check failed: {e}", "Clean up processes manually with 'ps aux | grep <process_name>'"
    
    def _check_pcie_alignment(self) -> Tuple[bool, str, str]:
        """Check if GPU PCIe speed and width are aligned with their maximum capabilities"""
        try:
            # Check if utils instance is available
            if not self.utils:
                return False, "PCIe alignment check unavailable: utils module not available", "Ensure all VTS modules are properly installed"
            
            # Check if bash manager is available in utils (fallback gracefully if not)
            if not hasattr(self.utils, 'bash_manager') or not self.utils.bash_manager:
                return True, "PCIe alignment check skipped (deviceDetect.sh not available)", ""
            
            # Try to detect GPU device ID (default to e223 for Intel Arc B70)
            device_id = 'e223'  # Intel Arc B70 device ID
            
            # Check PCIe alignment using the utils function
            result = self.utils.check_pcie_alignment(device_id, verbose=False)
            
            if result.get('error'):
                # If no devices found, this might not be a GPU system - treat as informational
                if "No devices found" in result['error'] or "No GPU endpoint devices found" in result['error']:
                    return True, "PCIe alignment check skipped (no Intel Arc GPUs detected)", ""
                else:
                    return False, f"PCIe alignment check failed: {result['error']}", "Verify GPU devices and PCIe configuration"
            
            aligned_count = len(result.get('aligned_devices', []))
            misaligned_count = len(result.get('misaligned_devices', []))
            
            if result.get('success', False):
                # If the check reported success but no devices were evaluated, treat as skipped/unavailable
                if aligned_count == 0 and misaligned_count == 0:
                    return True, "PCIe alignment check skipped (no PCIe link data available for detected GPUs)", ""
                
                # Build message with device details if available
                message_parts = [f"All {aligned_count} GPU device(s) have optimal PCIe configuration"]
                
                # Add aligned device details
                aligned_details = result.get('aligned_device_details', [])
                if aligned_details:
                    message_parts.extend([f"        {detail}" for detail in aligned_details])
                
                # Combine into single message with line breaks
                combined_message = '\n'.join(message_parts) if len(message_parts) > 1 else message_parts[0]
                return True, combined_message, ""
            elif misaligned_count > 0:
                # Build message with device details if available
                message_parts = [f"{misaligned_count} GPU device(s) have PCIe misalignment"]
                
                # Add misaligned device details
                misaligned_details = result.get('misaligned_device_details', [])
                if misaligned_details:
                    message_parts.extend([f"        {detail}" for detail in misaligned_details])
                else:
                    # Fallback to building details from raw result data
                    for sbdf in result.get('misaligned_devices', []):
                        device_info = result.get('details', {}).get(sbdf, {})
                        issues = device_info.get('issues', [])
                        if issues:
                            message_parts.append(f"        {sbdf}: {', '.join(issues)}")
                
                # Combine into single message with line breaks
                combined_message = '\n'.join(message_parts) if len(message_parts) > 1 else message_parts[0]
                suggestion = "Check PCIe slot configuration, motherboard settings, and ensure GPUs are properly seated in PCIe x16 slots"
                return False, combined_message, suggestion
            else:
                return False, "PCIe alignment check found no devices to validate", "Verify GPU device detection"
                
        except ImportError as e:
            return False, f"PCIe alignment check unavailable: missing module {e}", "Ensure all VTS modules are properly installed"
        except Exception as e:
            return False, f"PCIe alignment check failed: {e}", "Check system PCIe configuration manually with 'lspci -vv'"