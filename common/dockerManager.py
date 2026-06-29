# Copyright (C) 2024-2026 Intel Corporation
import os
import sys
import time
import subprocess # nosec
import requests
from typing import Optional
import re
import threading
import queue
import traceback
from .utils import Utils

class CommandExecutor:
    """Executes system commands with live stdout/stderr streaming and optional input."""
    def __init__(self, logger):
       self.logger = logger
       
    @staticmethod
    def run_cmd_with_live_output(cmd, logger, cwd: Optional[str] = None, input_data: Optional[str] = None, check: bool = True):
        logger.info(f"Executing: {' '.join(cmd)}")

        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            text=True,
            stdin=subprocess.PIPE if input_data else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        if input_data:
            process.stdin.write(input_data)
            process.stdin.close()

        stdout_queue = queue.Queue()
        stderr_queue = queue.Queue()

        def stream_output(pipe, output_queue, prefix=""):
            try:
                for line in iter(pipe.readline, ''):
                    if line:
                        timestamp = time.strftime("%H:%M:%S")
                        output_queue.put(f"[{timestamp}]{prefix} {line}")
                pipe.close()
            except (IOError, OSError, BrokenPipeError) as e:
                output_queue.put(f"Error reading {prefix}: {e}")
                traceback.print_exc()
                

        def is_docker_info(line, cmd):
            """Check if this is normal Docker informational output."""
            if 'docker' in ' '.join(cmd):
                info_patterns = [
                    r'Unable to find image.*locally',
                    r'Pulling from',
                    r'Pull complete',
                    r'Digest:',
                    r'Status:',
                    r'Downloaded newer image',
                    r'Image is up to date'
                ]
                for pattern in info_patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        return True
            return False

        # Start threads for stdout and stderr
        stdout_thread = threading.Thread(target=stream_output, args=(process.stdout, stdout_queue, ""))
        stderr_thread = threading.Thread(target=stream_output, args=(process.stderr, stderr_queue, " [INFO]"))

        stdout_thread.daemon = True
        stderr_thread.daemon = True
        stdout_thread.start()
        stderr_thread.start()

        # Print output as it comes
        while process.poll() is None or not stdout_queue.empty() or not stderr_queue.empty():
            try:

                while not stdout_queue.empty():
                    logger.info(stdout_queue.get_nowait())

                while not stderr_queue.empty():
                    line = stderr_queue.get_nowait()
                    clean_line = re.sub(r'\[INFO\]\s*', '', line)

                    if is_docker_info(clean_line, cmd):
                        # Replace [INFO] with [DOCKER] for Docker messages
                        line = line.replace('[INFO]', '[DOCKER]')
                    elif any(error_word in clean_line.lower() for error_word in ['error', 'failed', 'exception', 'traceback']):

                        line = line.replace('[INFO]', '[ERR]')
                    else:
                        pass

                    logger.info(line)

                time.sleep(0.1)
            except queue.Empty:
                pass

        process.wait()

        while not stdout_queue.empty():
            logger.info(stdout_queue.get_nowait())
        while not stderr_queue.empty():
            line = stderr_queue.get_nowait()
            clean_line = re.sub(r'\[INFO\]\s*', '', line)
            if is_docker_info(clean_line, cmd):
                line = line.replace('[INFO]', '[DOCKER]')
            elif any(error_word in clean_line.lower() for error_word in ['error', 'failed', 'exception', 'traceback']):
                line = line.replace('[INFO]', '[ERR]')
            logger.info(line)

        if check and process.returncode != 0:
            msg = f"Command failed ({' '.join(cmd)}): exited with code {process.returncode}"
            logger.error(msg)
            traceback.print_exc()
            raise RuntimeError(msg)

        return process.returncode
class DockerManager(CommandExecutor):

    def add_arguments(self):
        super().add_arguments()

    def prepareGpuCommands(self):
        pass

    def __init__(self, logger, user, token, env_vars, docker_image, service_name, debug_mode, reuse_containers):
        self.user = user
        self.token = token
        self.env_vars = env_vars
        self.debug_mode = debug_mode
        self.reuse_containers = reuse_containers
        self.logger = logger
        super().__init__(logger)
        
        # SECURITY: Validate Docker parameters to prevent injection attacks
        self.utils = Utils(logger)
        try:
            self.image = self.utils.validate_docker_image(docker_image)
            if logger:
                logger.info(f"SECURITY: Docker image validated: {self.image}")
        except ValueError as e:
            if logger:
                logger.error(f"SECURITY: Docker image validation failed: {e}")
            raise ValueError(f"Invalid Docker image: {e}")
        
        try:
            self.service_name = self.utils.validate_service_name(service_name)
            if logger:
                logger.info(f"SECURITY: Docker service name validated: {self.service_name}")
        except ValueError as e:
            if logger:
                logger.error(f"SECURITY: Docker service name validation failed: {e}")
            raise ValueError(f"Invalid Docker service name: {e}")

    def get_container_status(self):
        """Get the current status of the container."""
        try:
            result = subprocess.run(
                ["docker", "ps", "-a", "--filter", "name=" + self.service_name,
                 "--format", "{{.Status}}\t{{.Names}}\t{{.ID}}"],
                capture_output=True, text=True, timeout=10
            )

            if result.stdout.strip():
                status_line = result.stdout.strip().split('\t')
                return {
                    'exists': True,
                    'status': status_line[0],
                    'name': status_line[1] if len(status_line) > 1 else '',
                    'id': status_line[2] if len(status_line) > 2 else '',
                    'running': 'Up' in status_line[0],
                    'exited': 'Exited' in status_line[0],
                    'restarting': 'Restarting' in status_line[0]
                }
            else:
                return {'exists': False}

        except (subprocess.CalledProcessError, subprocess.SubprocessError, OSError) as e:
            self.logger.warning(f"Could not get container status: {e}")
            traceback.print_exc()
            return {'exists': False}

    def cleanup_existing_container(self, force=False):
        """Clean up existing container if it exists."""
        container_status = self.get_container_status()

        if not container_status['exists']:
            self.logger.info("No existing container found")
            return True

        self.logger.info(f"Found existing container: {container_status['status']}")

        if container_status['running'] and not force:
            if self.reuse_containers:
                self.logger.info("Container is running, attempting to reuse...")
                return self.check_container_health()
            else:
                self.logger.info("Stopping running container...")
                self.stop_existing_container()

        if container_status['exited'] or force:
            self.logger.info("Removing existing container...")
            try:
                subprocess.run(
                    ["docker", "rm", "-f", self.service_name],
                    capture_output=True, text=True, timeout=30
                )
                self.logger.info("Container removed successfully")
                return True
            except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
                self.logger.error(f"Failed to remove container: {e}")
                traceback.print_exc()
                return False

        return True

    def stop_existing_container(self):
        """Stop existing container gracefully."""
        try:
            self.logger.info("Stopping existing container...")
            subprocess.run(
                ["docker", "stop", self.service_name],
                capture_output=True, text=True, timeout=60
            )
            self.logger.info("Container stopped")
        except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
            self.logger.warning(f"Error stopping container: {e}")
            traceback.print_exc()

    def check_container_health(self):
        """Check if existing container is healthy and can be reused."""
        try:
            self.logger.info("Checking container health...")

            container_status = self.get_container_status()
            if not container_status.get('running', False):
                self.logger.info("Container is not running")
                return False

            try:
                response = requests.get("http://localhost:8000/health", timeout=5)
                if response.status_code == 200:
                    self.logger.info("Container is healthy and ready to reuse!")
                    return True
                else:
                    self.logger.info(f"  Container responding but not healthy (status: {response.status_code})")
            except requests.ConnectionError:
                self.logger.info("Container not responding on port 8000")
                traceback.print_exc()
            except (requests.RequestException, requests.Timeout) as e:
                self.logger.info(f"Health check failed: {e}")
                traceback.print_exc()

            return False

        except (subprocess.CalledProcessError, subprocess.SubprocessError, OSError) as e:
            self.logger.warning(f"Could not check container health: {e}")
            traceback.print_exc()
            return False

    def cleanup_port_conflicts(self, port=8000):
        """Clean up port conflicts before starting container."""
        self.logger.info(f"Checking for port {port} conflicts...")

        try:
            result = subprocess.run(
                ["lsof", "-t", f"-i:{port}"],
                capture_output=True, text=True, timeout=10
            )

            if result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                self.logger.warning(f"Port {port} is in use by PIDs: {', '.join(pids)}")

                container_status = self.get_container_status()
                if container_status.get('running') and self.reuse_containers:
                    self.logger.info("Port is used by our container, checking if we can reuse it...")
                    if self.check_container_health():
                        return True

                self.logger.info("Stopping all the containers...")
                try:
                    docker_containers = subprocess.run(
                        ["docker", "ps", "-q", "--filter", "name=ai_workloads"],
                        capture_output=True, text=True, timeout=10
                    ).stdout.strip().split()

                    if docker_containers:
                        subprocess.run(
                            ["docker", "stop"] + docker_containers,
                            capture_output=True, timeout=60
                        )
                        subprocess.run(
                            ["docker", "rm"] + docker_containers,
                            capture_output=True, timeout=30
                        )
                        self.logger.info("Cleaned up the containers")
                except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
                    self.logger.warning(f"Error cleaning up containers: {e}")
                    traceback.print_exc()

                time.sleep(3)
                result = subprocess.run(
                    ["lsof", "-t", f"-i:{port}"],
                    capture_output=True, text=True, timeout=10
                )

                if result.stdout.strip():
                    self.logger.warning(f"Port {port} still in use after cleanup")
                    return False
            else:
                self.logger.info(f"Port {port} is free")

            return True

        except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
            self.logger.warning(f"Could not check port conflicts: {e}")
            traceback.print_exc()
            return True

    def validate_docker_credentials(self):
        if not self.user or not self.token:
            msg = (
                "\nMissing Docker credentials.\n\n"
                "Set Docker Hub credentials in .env file: \n"
                "How to generate Docker Access Token:\n"
                "  - Go to https://hub.docker.com/settings/security\n"
                "  - Click 'New Access Token'\n"
                "  - Name it (e.g., 'ai-benchmark')\n"
                "  - Copy and securely store the token.\n"
            )
            self.logger.error(msg)
            raise EnvironmentError("Missing Docker credentials.")

    def check_docker_engine(self):
        self.logger.info("Checking Docker engine status...")
        try:
            result = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=10)
            if result.returncode != 0 or "Server:" not in result.stdout:
                raise RuntimeError("Docker daemon not responding.")
            self.logger.info("Docker engine is available and responsive.")
        except FileNotFoundError:
            raise EnvironmentError("Docker not installed. Install Docker from https://docs.docker.com/get-docker/")
        except (RuntimeError, subprocess.TimeoutExpired):
            raise EnvironmentError("Docker daemon not running. Start it with: sudo systemctl start docker")

    def configure_docker_proxy(self):
        """Configure Docker daemon proxy settings from .env file."""
        try:
            self.logger.info("Configuring Docker daemon proxy settings...")
           
            http_proxy = self.env_vars.get("HTTP_PROXY") or self.env_vars.get("http_proxy")
            https_proxy = self.env_vars.get("HTTPS_PROXY") or self.env_vars.get("https_proxy")
            no_proxy = self.env_vars.get("NO_PROXY") or self.env_vars.get("no_proxy")
            
            if not http_proxy and not https_proxy:
                self.logger.info("No proxy configuration found in .env file, skipping Docker proxy setup")
                return True
            
            self.logger.info(f"Found proxy configuration:")
            if http_proxy:
                self.logger.info(f"   HTTP_PROXY: {http_proxy}")
            if https_proxy:
                self.logger.info(f"   HTTPS_PROXY: {https_proxy}")
            if no_proxy:
                self.logger.info(f"   NO_PROXY: {no_proxy}")
            
            self.logger.info("Creating systemd directory...")
            mkdir_cmd = ["sudo", "mkdir", "-p", "/etc/systemd/system/docker.service.d"]
            result = subprocess.run(mkdir_cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                self.logger.error(f"Failed to create directory: {result.stderr}")
                return False
            
            self.logger.info("Directory created successfully")
            
            config_content = "[Service]\n"
            
            if http_proxy:
                config_content += f'Environment="HTTP_PROXY={http_proxy}"\n'
            
            if https_proxy:
                config_content += f'Environment="HTTPS_PROXY={https_proxy}"\n'
            
            if no_proxy:
                config_content += f'Environment="NO_PROXY={no_proxy}"\n'
            else:
                config_content += 'Environment="NO_PROXY=localhost,127.0.0.1"\n'
            
            self.logger.info("Proxy configuration content:")
            for line in config_content.strip().split('\n'):
                self.logger.info(f"   {line}")
            
            config_file_path = "/etc/systemd/system/docker.service.d/http-proxy.conf"
            if not os.path.exists(config_file_path):
                self.logger.info(f"Writing configuration to {config_file_path}...")
                
                write_cmd = ["sudo", "tee", config_file_path]
                result = subprocess.run(
                    write_cmd,
                    input=config_content,
                    text=True,
                    capture_output=True,
                    timeout=30
                )
                
                if result.returncode != 0:
                    self.logger.error(f"Failed to write configuration file: {result.stderr}")
                    return False
                
                self.logger.info("Configuration file written successfully")
                
                self.logger.info("Reloading systemd daemon...")
                reload_cmd = ["sudo", "systemctl", "daemon-reload"]
                result = subprocess.run(reload_cmd, capture_output=True, text=True, timeout=30)
                
                if result.returncode != 0:
                    self.logger.error(f"Failed to reload systemd daemon: {result.stderr}")
                    return False
                
                self.logger.info("Systemd daemon reloaded successfully")
                
                self.logger.info("Restarting Docker service...")
                self.logger.warning("This will restart Docker and stop all running containers!")
                
                restart_cmd = ["sudo", "systemctl", "restart", "docker"]
                result = subprocess.run(restart_cmd, capture_output=True, text=True, timeout=60)
                
                if result.returncode != 0:
                    self.logger.error(f"Failed to restart Docker service: {result.stderr}")
                    return False
                
                self.logger.info("Docker service restarted successfully")
                
                self.logger.info("Waiting for Docker service to fully start...")
                time.sleep(5)
            
            if self.verify_docker_after_restart():
                self.logger.info("Docker proxy configuration completed successfully!")
                return True
            else:
                self.logger.error("Docker proxy configuration failed - Docker not responding")
                return False
            
        except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
            self.logger.error(f"Error configuring Docker proxy: {e}")
            traceback.print_exc()
            return False

    def verify_docker_after_restart(self):
        """Verify Docker is running properly after restart."""
        try:
            self.logger.info("Verifying Docker service status...")
            
            status_cmd = ["sudo", "systemctl", "is-active", "docker"]
            result = subprocess.run(status_cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0 or result.stdout.strip() != "active":
                self.logger.error(f"Docker service is not active: {result.stdout.strip()}")
                return False
            
            self.logger.info("Docker service is active")
            
            test_cmd = ["docker", "info"]
            result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=15)
            
            if result.returncode != 0:
                self.logger.error(f"Docker command failed: {result.stderr}")
                return False
            
            self.logger.info("Docker is responding to commands")
            
            if "HTTP Proxy:" in result.stdout or "HTTPS Proxy:" in result.stdout:
                self.logger.info("Proxy settings are active in Docker daemon")
            else:
                self.logger.warning("Proxy settings may not be active (check docker info output)")
            
            return True
        
        except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
            self.logger.error(f"Error verifying Docker: {e}")
            traceback.print_exc()
            return False
    
    def image_exists_locally(self):
        try:
            self.logger.info(f"Image '{self.image}'")
            result = subprocess.run(["docker", "images", "-q", self.image], text=True, capture_output=True, timeout=10)
            image_id = result.stdout.strip()
            if image_id:
                self.logger.info(f"Image '{self.image}' already exists locally (ID: {image_id[:12]}).")
                return True
            return False
        except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
            self.logger.warning(f"Could not verify local image presence: {e}")
            traceback.print_exc()
            return False

    def pull_image(self, force=False):
        """Pulls Docker image with live logs showing download progress."""
        if not force and self.image_exists_locally():
            self.logger.info(f"Skipping image pull. '{self.image}' is already available locally.")
            return

        self.logger.info(f"Pulling Docker image: {self.image}")
        self.logger.info("This may take several minutes for large models...")

        try:
            self.run_cmd_with_live_output(["docker", "pull", self.image], self.logger)
            self.logger.info(f"Docker image '{self.image}' pulled successfully.")
        except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
            self.logger.error(f"Failed to pull Docker image: {e}")
            traceback.print_exc()
            raise

    def build_docker_run_command(self):
        """Build the docker run command with all necessary parameters."""
        cache_dir = os.path.expanduser("~/.cache")
        os.makedirs(cache_dir, exist_ok=True)
        cmd = [
            "docker", "run",
            "-t", "-d", "--rm",
            "--name", self.service_name,
            "--shm-size", "10g",
            "--net=host",
            "--ipc=host",
            "--privileged",
            "--entrypoint=",
            "-v", f"{cache_dir}:/root/.cache/",
            "-v", "/dev/dri/by-path:/dev/dri/by-path",
            "--device", "/dev/dri:/dev/dri"
        ]
        
        for key, value in self.env_vars.items():
            if value:  
                cmd.extend(["-e", f"{key}={value}"])
        '''
        defaults = {
            "MODEL_NAME": "facebook/opt-1.3b",
            "PROMPT_TEXT": "Explain quantum computing in simple terms.",
            "MAX_OUTPUT_TOKENS": "128",
            "INPUT_LENGTH": "64",
            "CONCURRENCY": "2"
        }
        
        for key, default_value in defaults.items():
            if key not in self.env_vars or not self.env_vars[key]:
                cmd.extend(["-e", f"{key}={default_value}"])
       
        cmd.extend([
            "-v", f"{os.path.abspath('./benchmark.py')}:/workspace/benchmark.py",
            "-v", f"{os.path.abspath(self.env_file)}:/workspace/.env:ro"
        ])
        '''
        
        cmd.extend([self.image, "/bin/bash"])
        
        self.logger.info(f"Cache directory: {cache_dir}")
        self.logger.debug(f"Full docker command: {' '.join(cmd)}")
        
        return cmd

    def login(self):
        self.logger.info(f"Logging into Docker Hub... {self.user}")
        self.validate_docker_credentials()
        self.check_docker_engine()


        process = subprocess.run(
            ["docker", "login", "--username", self.user.strip(), "--password-stdin"],
            input=self.token.strip(),
            text=True,
            capture_output=True
        )

        if process.returncode != 0:
            self.logger.warning(f"Docker login warning: {process.stderr}")
        else:
            self.logger.info("Docker login successful.")

    def start_container(self):
        #self.logger.info(f"Starting container using env file: {self.env_file}")
        self.logger.info(f"Debug mode: {'ENABLED' if self.debug_mode else 'DISABLED'}")
        self.logger.info(f"Container reuse: {'ENABLED' if self.reuse_containers else 'DISABLED'}")

        if not self.cleanup_port_conflicts():
            self.logger.warning("Port conflicts detected, forcing cleanup...")
            self.cleanup_existing_container(force=True)

        container_status = self.get_container_status()
        if container_status.get('exists') and self.reuse_containers:
            if self.check_container_health():
                self.logger.info("Reusing existing healthy container!")
                self.show_container_status()
                return
            else:
                self.logger.info("Existing container not healthy, recreating...")
                self.cleanup_existing_container(force=True)

        cmd = self.build_docker_run_command()
        
        self.logger.info(f"Starting container...")
        self.logger.debug(f"Full command: {' '.join(cmd)}")

        try:
            self.run_cmd_with_live_output(cmd, self.logger)
            self.logger.info("Container started successfully")
        except RuntimeError as e:
            if "already in use" in str(e) or "name is already in use" in str(e):
                self.logger.warning("Container name conflict detected, cleaning up...")
                self.cleanup_existing_container(force=True)
                self.logger.info("Retrying container start...")
                self.run_cmd_with_live_output(cmd, self.logger)
                traceback.print_exc()
            else:
                raise

        self.logger.info("Waiting for container to initialize...")
        time.sleep(3)

        container_status = self.get_container_status()
        if container_status.get('running'):
            self.logger.info("Container is running successfully")
        else:
            self.logger.warning("Container may not be running properly")
            self.show_recent_logs()
            
        self.show_container_status()

    def show_recent_logs(self, lines=20):
        """Show recent container logs."""
        try:
            self.logger.info(f"Recent container logs (last {lines} lines):")
            result = subprocess.run(
                ["docker", "logs", "--tail", str(lines), self.service_name],
                capture_output=True, text=True, timeout=30
            )

            logs = result.stdout + result.stderr
            if logs.strip():
                for line in logs.strip().split('\n'):
                    self.logger.info(f"{line}")
            else:
                self.logger.info("No logs available")

        except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
            self.logger.warning(f"Could not retrieve recent logs: {e}")
            traceback.print_exc()

    def show_container_status(self):
        """Show current container status and resource usage."""
        try:
            self.logger.info("Container Status Summary:")

            result = subprocess.run(
                ["docker", "ps", "--filter", "name=" + self.service_name,
                 "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"],
                capture_output=True, text=True, timeout=10
            )
            self.logger.info("Container Info:")
            self.logger.info(result.stdout)

            result = subprocess.run(
                ["docker", "stats", "--no-stream", "--format",
                 "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}",
                 self.service_name],
                capture_output=True, text=True, timeout=10
            )
            self.logger.info("Resource Usage:")
            self.logger.info(result.stdout)

        except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
            self.logger.warning(f"Could not retrieve container status: {e}")
            traceback.print_exc()

    def exec_in_container(self, cmd):
        self.logger.info(f" Executing in container: {' '.join(cmd)}")
        return self.run_cmd_with_live_output(["docker", "exec", self.service_name] + cmd, self.logger)

    def exec_in_container_detached(self, cmd):
        """Execute command in container in detached mode (for long-running processes)."""
        self.logger.info(f"Executing in container (detached): {' '.join(cmd)}")
        full_cmd = ["docker", "exec", "-d", self.service_name] + cmd
        return self.run_cmd_with_live_output(full_cmd, self.logger)

    def stop_container(self):
        self.logger.info("Stopping Docker container...")
        try:
            self.run_cmd_with_live_output(["docker", "stop", self.service_name], self.logger,check=False)
            self.run_cmd_with_live_output(["docker", "rm", self.service_name], self.logger, check=False)
            self.logger.info("Container stopped and removed successfully.")
        except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
            self.logger.warning(f"Error stopping container: {e}")
            traceback.print_exc()
    def cleanup_and_exit(self, exit_code=0):
        try:
            self.logger.info("Starting cleanup process...")

            if getattr(self, "reuse_containers", False):
                self.logger.info("Stopping the container...")
                try:
                    subprocess.run(
                        ["docker", "stop", self.service_name],
                        capture_output=True, text=True, timeout=30
                    )
                    subprocess.run(
                        ["docker", "rm", self.service_name],
                        capture_output=True, text=True, timeout=30
                    )
                    self.logger.info("Container cleaned up")
                except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
                    self.logger.warning(f"Error cleaning up container: {e}")
            else:
                self.logger.info("Leaving container running for reuse")

            if hasattr(self, 'execution_start_time'):
                total_time = time.time() - self.execution_start_time
                self.logger.info(f"Total execution time: {total_time:.1f}s")

            if exit_code == 0:
                self.logger.info("Workload execution completed successfully! Exiting...")
                self.overall_test_result = 'PASS'
                return
            else:
                self.logger.error(f"Workload execution failed with exit code {exit_code}. Exiting...")
                self.overall_test_result = 'FAIL'
                return

        except (subprocess.CalledProcessError, subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
            self.logger.error(f"Error during cleanup: {e}")
            traceback.print_exc()
        finally:
            #sys.exit(exit_code)
            return
