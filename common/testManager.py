# Copyright (C) 2024-2026 Intel Corporation
# Verification Test Suite Test Manager
import os
import importlib
import zipfile
import glob
import shutil
import subprocess # nosec
from common.utils import Utils

# --- Test Manager ---
class TestManager:
    """Manages test definitions and execution."""

    def __init__(self, logger, device_manager, input_parser):
        self.logger = logger
        self.device_manager = device_manager
        self.input_parser = input_parser
        self.utils = Utils(self.logger)
        self.logger.info(f"Loading {self.device_manager.gpu_family} family definitions...")
        module_name = f"gpu.{self.device_manager.gpu_family}.platform_defs"
        platform_module = importlib.import_module(module_name)
        
        # Create BMGPlatformDefs instance to access tests dictionary
        platform_defs_instance = platform_module.BMGPlatformDefs(self.logger, self.device_manager)
        self.testsDict = platform_defs_instance.tests_dict

    def list_tests(self):
        self.logger.info('')
        self.logger.header_m("Tests Menu")
        self.logger.info('')
        for num, test_info in self.testsDict.items():
            if isinstance(num, int) or num in ['d', 'c', 'q', 'a']:
                self.logger.print_menu(num, test_info.get('name', 'Unknown Test'))
            else:
                self.logger.print_menu_2(test_info.get('name', 'Unknown Test'))
        self.logger.separator(80)
        self.logger.info('')

    def run_test(self, test_number: int, parsed_args=None):
        test_info = self.testsDict.get(test_number,{})
        test_name = test_info.get('name',None)
        test_script = test_info.get('script',None)
        if not test_name:
            self.logger.error(f"Test number {test_number} is invalid.")
            return -1 # Error code for invalid test number
        
        # Dynamically import and run the test module/class
        module_name = f"gpu.{self.device_manager.gpu_family}.tests.{test_script}"
        test_module = importlib.import_module(module_name)
        test_class = getattr(test_module, f"testClass")
        
        # Create a fresh InputParser for this test to avoid argument accumulation
        from common.inputParser import InputParser
        fresh_input_parser = InputParser(self.device_manager, self.logger, self.input_parser.args)
        
        # Parse base arguments first with the fresh parser
        base_parsed_args = fresh_input_parser.parse()
        
        # Create test instance with fresh input parser
        test_instance = test_class(test_number, self.logger, self.device_manager, fresh_input_parser)
        
        # Add test-specific arguments to the fresh parser
        if hasattr(test_instance, 'add_arguments'):
            test_instance.add_arguments()
        
        # Reparse arguments with test-specific arguments included
        updated_parsed_args = fresh_input_parser.reparse_with_test_arguments(test_instance)
        
        # Ensure test number is set
        updated_parsed_args.tn = test_number
        
        # Update test instance with the reparsed arguments
        test_instance.parsed_args = updated_parsed_args
        
        return test_instance.main(parsed_args or updated_parsed_args)
    
    def _wait_for_debug_completion(self, debug_folder, timeout=30):
        """Wait for debug logging to complete by monitoring file activity."""
        import time
        

        
        # Track file modification times
        last_activity_time = time.time()
        stable_period = 5  # Wait 5 seconds of no file activity
        check_interval = 1  # Check every second
        
        start_time = time.time()
        while (time.time() - start_time) < timeout:
            current_time = time.time()
            activity_detected = False
            
            # Check all files in the debug folder for recent modifications
            try:
                for root, dirs, files in os.walk(debug_folder):
                    for file in files:
                        file_path = os.path.join(root, file)
                        if os.path.exists(file_path):
                            mod_time = os.path.getmtime(file_path)
                            # If file was modified within the last 2 seconds, consider it active
                            if (current_time - mod_time) < 2:
                                activity_detected = True
                                last_activity_time = current_time

                                break
                    if activity_detected:
                        break
                        
                # If no activity detected for stable_period seconds, assume completion
                if not activity_detected and (current_time - last_activity_time) >= stable_period:

                    return True
                    
            except (IOError, OSError, FileNotFoundError, PermissionError) as e:
                self.logger.warning(f"Error monitoring debug folder activity: {e}")
                
            time.sleep(check_interval)
        
        self.logger.warning(f"Timeout waiting for debug logging to complete after {timeout} seconds")
        return False
    
    def _zip_and_move_debug_logs(self, tools_dir):
        """Helper method to zip debug logs folder and move it to /logs directory."""
        try:         
            # List all debug folders before processing
            debug_folders = glob.glob(os.path.join(tools_dir, 'debugLogs-*'))
            
            if not debug_folders:
                self.logger.info("No debug logs folder found to process.")
                return
            
            # Use the most recent debug folder
            debug_folder = max(debug_folders, key=os.path.getmtime)
            folder_name = os.path.basename(debug_folder)
            

            
            # Check for running debug processes
            try:
                ps_result = self.utils.run_command_blocking("ps aux | grep infoCollect | grep -v grep")
                if ps_result.strip():
                    self.logger.warning(f"Active infoCollect processes detected: {ps_result.strip()}")

            except (RuntimeError, subprocess.CalledProcessError):
                # Process detection failed (expected if no processes found)
                pass
            except (OSError, FileNotFoundError) as e:
                self.logger.warning(f"Could not check for active processes: {e}")
            except (RuntimeError, SystemError, MemoryError) as e:
                self.logger.error(f"Unexpected error checking processes: {type(e).__name__}: {e}")
            except KeyboardInterrupt:
                self.logger.error("KeyboardInterrupt received while checking processes; aborting as requested.")
                raise
            # Wait for all debug logging to complete
            self._wait_for_debug_completion(debug_folder)
            
            # Create logs directory
            project_root = os.path.dirname(os.path.abspath(__file__))
            logs_dir = os.path.abspath(os.path.join(project_root, '..', 'logs'))
            Utils.secure_log_directory(logs_dir)
            
            # Create zip file in logs directory
            zip_name = f"{folder_name}.zip"
            zip_path = os.path.join(logs_dir, zip_name)
            

            
            # Zip the debug folder and include main VTS logs
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Add debug folder contents
                for root, _, files in os.walk(debug_folder):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, debug_folder)
                        zipf.write(file_path, arcname)
                
                # Add main VTS log files from logs directory
                project_logs_dir = os.path.abspath(os.path.join(project_root, '..', 'logs'))
                if os.path.exists(project_logs_dir):
                    vts_log_added = False
                    # Collect candidate VTS log files
                    candidates = []
                    for log_file in os.listdir(project_logs_dir):
                        if log_file.startswith('Verification_Test_Suite_') and log_file.endswith('.log'):
                            vts_log_path = os.path.join(project_logs_dir, log_file)
                            if os.path.isfile(vts_log_path):
                                candidates.append(log_file)
                    if candidates:
                        # Select the most recent VTS log file by modification time
                        newest_log = None
                        try:
                            newest_log = max(
                                candidates,
                                key=lambda lf: os.path.getmtime(os.path.join(project_logs_dir, lf)),
                            )
                            newest_log_path = os.path.join(project_logs_dir, newest_log)
                            zipf.write(newest_log_path, newest_log)
                            self.logger.info(f"Added main VTS log to archive: {newest_log}")
                            vts_log_added = True
                        except (OSError, IOError, PermissionError) as log_e:
                            log_ref = newest_log if newest_log is not None else project_logs_dir
                            self.logger.warning(f"Could not determine or include VTS log from {log_ref}: {log_e}")
                    if not vts_log_added:
                        self.logger.warning("No main VTS log file found to include in debug archive")
                else:
                    self.logger.warning(f"VTS logs directory not found: {project_logs_dir}")
            

            
            # Check permissions before attempting removal
            import stat
            try:
                folder_stat = os.stat(debug_folder)
                folder_owner = folder_stat.st_uid
                current_user = os.getuid()

                
                # Check if we have write permissions
                if not os.access(debug_folder, os.W_OK):
                    self.logger.warning(f"No write permission to debug folder: {debug_folder}")
                    # Try to change permissions first
                    try:
                        # SECURITY: Validate USER environment variable to prevent command injection
                        safe_user = self.utils.validate_user_env_var()
                        self.logger.info(f"SECURITY: Using validated user: {safe_user}")
                        
                        self.utils.run_command_blocking(f'sudo chmod -R 755 "{debug_folder}"')
                        self.utils.run_command_blocking(f'sudo chown -R {safe_user}:{safe_user} "{debug_folder}"')

                    except (RuntimeError, subprocess.CalledProcessError, subprocess.SubprocessError, OSError, PermissionError) as perm_e:
                        self.logger.error(f"Failed to change permissions: {perm_e}")
                        
            except (OSError, RuntimeError, subprocess.CalledProcessError, subprocess.SubprocessError) as stat_e:
                self.logger.error(f"Failed to check folder permissions: {stat_e}")
            
            # Remove the debug folder after zipping
            try:
                shutil.rmtree(debug_folder)

            except PermissionError as perm_e:
                self.logger.error(f"Permission denied when removing debug folder: {perm_e}")
                # Try using sudo as fallback
                try:
                    self.utils.run_command_blocking(f'sudo rm -rf "{debug_folder}"')

                except (RuntimeError, subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as sudo_e:
                    self.logger.error(f"Failed to remove debug folder even with sudo: {sudo_e}")
            except (OSError, FileNotFoundError, IOError) as rm_e:
                self.logger.error(f"Error removing debug folder: {rm_e}")
            
            # Force filesystem sync and wait a moment
            import time
            try:
                os.sync()  # Force filesystem sync
            except (OSError, AttributeError) as e:
                # sync() might not be available on all systems or permission denied
                self.logger.debug(f"Filesystem sync not available or failed: {e}")
            except (RuntimeError, SystemError, MemoryError) as e:
                self.logger.warning(f"Unexpected error during filesystem sync: {e}")
            
            time.sleep(1)  # Wait 1 second for filesystem operations to complete
            
            # Verify that the debug folder was actually removed using multiple methods

            
            # Check using os.path.exists
            exists_check1 = os.path.exists(debug_folder)
            # Check using os.path.isdir
            exists_check2 = os.path.isdir(debug_folder)
            # Check using ls command as final verification
            try:
                ls_result = self.utils.run_command_blocking(f'ls -la "{debug_folder}"')
                exists_check3 = "No such file or directory" not in ls_result
            except (RuntimeError, subprocess.CalledProcessError):
                # ls failed - directory likely doesn't exist (expected)
                exists_check3 = False
            except (OSError, FileNotFoundError) as e:
                self.logger.debug(f"Directory verification command failed: {e}")
                exists_check3 = False
            except (RuntimeError, SystemError, subprocess.SubprocessError, MemoryError) as e:
                self.logger.error(f"Unexpected error in directory verification: {e}")
                exists_check3 = False
            

            
            if exists_check1 or exists_check2 or exists_check3:
                self.logger.warning(f"Debug folder still exists after removal attempt: {debug_folder}")
                
                # Check for open file handles using lsof
                try:
                    lsof_result = self.utils.run_command_blocking(f'sudo lsof +D "{debug_folder}" 2>/dev/null')
                    if lsof_result.strip():
                        self.logger.warning(f"Open file handles detected in debug folder: {lsof_result}")
                except (RuntimeError, subprocess.CalledProcessError):
                    # lsof failed - expected if no open handles or permission denied
                    pass
                except (OSError, FileNotFoundError) as e:
                    self.logger.debug(f"Could not check file handles: {e}")
                except (RuntimeError, SystemError, subprocess.SubprocessError, MemoryError) as e:
                    self.logger.warning(f"Unexpected error checking file handles: {e}")
                
                # Try one more time with force and sync
                try:

                    self.utils.run_command_blocking(f'sudo rm -rf "{debug_folder}" && sync')
                    time.sleep(2)  # Wait longer after forced removal
                    
                    final_check = os.path.exists(debug_folder)
                    if not final_check:
                        pass  # Debug folder successfully removed
                    else:
                        self.logger.error(f"Debug folder still exists even after forced sync removal: {debug_folder}")
                except (RuntimeError, subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as final_e:
                    self.logger.error(f"Final removal attempt failed: {final_e}")
            else:
                pass  # Debug folder successfully removed on first attempt
                
            # Clean up all remaining debug folders
            remaining_debug_folders = glob.glob(os.path.join(tools_dir, 'debugLogs-*'))
            folders_removed = 0
            
            for remaining_folder in remaining_debug_folders:
                try:
                    # Skip the folder we just processed (in case removal failed)
                    if remaining_folder == debug_folder:
                        continue
                        
                    folder_name = os.path.basename(remaining_folder)
                    
                    try:
                        # Try to remove with sudo since they're likely owned by root
                        self.utils.run_command_blocking(f'sudo rm -rf "{remaining_folder}"')
                        if not os.path.exists(remaining_folder):
                            folders_removed += 1
                        else:
                            self.logger.warning(f"Failed to remove debug folder: {folder_name}")
                    except (RuntimeError, subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as rm_e:
                        self.logger.warning(f"Error removing debug folder {folder_name}: {rm_e}")
                        
                except (OSError, IOError, AttributeError, ValueError) as stat_e:
                    self.logger.warning(f"Could not process debug folder {remaining_folder}: {stat_e}")
            
            # Final verification - check if any debug folders still remain
            final_remaining_folders = glob.glob(os.path.join(tools_dir, 'debugLogs-*'))
            if final_remaining_folders:
                self.logger.warning(f"{len(final_remaining_folders)} debug folders still remain in tools directory")
            elif folders_removed > 0:
                pass  # Successfully cleaned up additional debug folders
            
        except (IOError, OSError, FileNotFoundError, PermissionError, RuntimeError, subprocess.CalledProcessError, subprocess.SubprocessError) as e:
            self.logger.error(f"Failed to zip and move debug logs: {e}")
    
    def run_debug_script(self):
        self.logger.header_m("Running Debug Script...")
        project_root = os.path.dirname(os.path.abspath(__file__))
        tools_dir = os.path.abspath(os.path.join(project_root, '..', 'tools'))
        debug_script_path = os.path.join(tools_dir, 'infoCollect.sh')
        
        # Use our improved make_script_executable method instead of direct chmod
        if not self.utils.make_script_executable(debug_script_path):
            self.logger.warning(f"Could not make infoCollect.sh executable at {debug_script_path}")
            
        test_exec_object = self.utils.run_command_non_blocking(f'{debug_script_path}')
        for line in test_exec_object.iter_output():
            self.logger.info(line)
        test_exec_object.process.wait()
        self.logger.info("Debug Script Completed.")
        
        # Zip and move debug logs to /logs directory
        self._zip_and_move_debug_logs(tools_dir)
        
        return 0
    
    def run_debug_script_silent(self):
        """
        Run debug script silently without any logging output.
        Used for automatic debug collection after test execution.
        """
        try:
            self.logger.info("Collecting debug information in background...")
            
            project_root = os.path.dirname(os.path.abspath(__file__))
            tools_dir = os.path.abspath(os.path.join(project_root, '..', 'tools'))
            debug_script_path = os.path.join(tools_dir, 'infoCollect.sh')
            
            # Make executable and run silently
            if not self.utils.make_script_executable(debug_script_path):
                self.logger.warning(f"Could not make infoCollect.sh executable at {debug_script_path}")
                
            test_exec_object = self.utils.run_command_non_blocking(f'{debug_script_path}')
            
            # Wait for completion but don't log output - consume output to prevent hanging
            for line in test_exec_object.iter_output():
                pass  # Consume output silently to prevent buffer overflow
            test_exec_object.process.wait()  # No timeout - let debug script complete fully
            
            # Zip and move debug logs to /logs directory
            self._zip_and_move_debug_logs(tools_dir)
            
            return 0
        except (IOError, OSError, RuntimeError, subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired):
            # Silently ignore any errors in background debug collection
            return 1
    
    def get_test_class(self, test_number: int):
        """
        Get the test class for a given test number without instantiating it.
        
        Args:
            test_number (int): Test number
            
        Returns:
            class: Test class or None if not found
        """
        test_info = self.testsDict.get(test_number, {})
        test_script = test_info.get('script', None)
        
        if not test_script:
            return None
        
        try:
            # Dynamically import the test module
            module_name = f"gpu.{self.device_manager.gpu_family}.tests.{test_script}"
            test_module = importlib.import_module(module_name)
            test_class = getattr(test_module, "testClass")
            return test_class
        except (ImportError, AttributeError) as e:
            self.logger.warning(f"Could not import test class for test {test_number}: {e}")
            return None