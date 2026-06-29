# Copyright (C) 2024-2026 Intel Corporation
import os
from . import platform_utils


class BMGPlatformDefs:
    """BMG Platform definitions class that encapsulates all platform-specific configurations, tests, and calculations."""
    
    def __init__(self, logger=None, device_manager=None):
        """
        Initialize BMG platform definitions.
        
        Args:
            logger: Logger instance for logging messages
            device_manager: Device manager instance for hardware access
        """
        self.logger = logger
        self.device_manager = device_manager
        
        # Create platformUtils instance if we have required dependencies
        self.platform_utils = None
        if self.logger and self.device_manager:
            self.platform_utils = platform_utils.platformUtils(self.logger, self.device_manager)
        
        # Initialize all platform-specific data first
        self._init_test_definitions()
        self._init_version_configurations()
        self._init_pcie_bandwidth_factors()
        self._init_package_requirements()
        
        # Initialize lazy-loaded attributes
        self._power_thermal_specs = None

    def _init_test_definitions(self):
        """Initialize test menu and parameter definitions."""
        # Test menu configuration - defines available tests and their corresponding scripts
        # Used by testManager to display menu options and execute selected tests
        self.tests_dict = {
            1  : {'script' : 'xpum.xpum_health', 'name': 'GPU Health Check'},
            2  : {'script' : 'xpum.xpum_diag', 'name': 'GPU Environment and Device Check'},
            3  : {'script' : 'bandwidth.pcie_bw_test', 'name': 'PCIe Bandwidth Test'},
            4  : {'script' : 'lmt.lmt_base', 'name': 'PCIe Lane Margin Test (LMT)'},
            5  : {'script' : 'reset.reset_base', 'name': 'Reset Test'},
            6  : {'script' : 'xpum.memory_bw_test', 'name': 'Memory Bandwidth Test'},
            7  : {'script' : 'dgdiag.memory_stress_test', 'name': 'Memory Stress Test'},
            8  : {'script' : 'dgdiag.power_thermal_stress_test', 'name': 'Power and Thermal Stress Test'},
            9  : {'script' : 'dgdiag.edp_stress_test', 'name': 'Excursion Design Power Stress Test'},
            10 : {'script' : 'dgdiag.functional_test', 'name': 'Functional Test (Not Available Yet)'},
            11 : {'script' : 'ai_wl.vllm_test', 'name': 'vLLM Test'},
            12 : {'script' : 'ai_wl.mlperf_test', 'name': 'ML Perf Test'},
            13 : {'script' : 'oneccl.collective_test', 'name': 'OneCCL Collective Test (Not Available Yet)'},
            14 : {'script' : 'oneccl.p2p_test', 'name': 'OneCCL Point to Point Test (Not Available Yet)'},
            'a': {'script' : 'none', 'name': 'Run All'},
            'd': {'script' : 'none', 'name': 'Debug Script'},
            'c': {'script' : 'none', 'name': 'Collect Logs'},
            'q': {'script' : 'none', 'name': 'Quit'}
        }

        # Test-specific parameter definitions for interactive prompts
        self.test_parameter_definitions = {
            5: {  # Reset Test
                'title': 'Reset Test Parameters',
                'parameters': [
                    {
                        'name': 'rt',
                        'prompt': 'Reset type (warm, cold, soft, flr, sbr, linkdisable, linkchange, retrain, custom, testonly, clean) [default=testonly]',
                        'type': str,
                        'default': 'testonly',
                        'validation': lambda x: x in ['warm', 'cold', 'soft', 'flr', 'sbr', 'linkdisable', 'linkchange', 'retrain', 'custom', 'testonly', 'clean']
                    },
                    {
                        'name': 'iterations',
                        'prompt': 'Number of iterations (default=1)',
                        'type': int,
                        'default': 1,
                        'validation': lambda x: x > 0,
                        'condition': lambda args: getattr(args, 'rt', '') not in ['clean', 'testonly']
                    },
                    {
                        'name': 'custom_script',
                        'prompt': 'Custom script to run (leave empty if none)',
                        'type': str,
                        'default': '',
                        'condition': lambda args: getattr(args, 'rt', '') == 'custom'
                    }
                ]
            },
            8: {  # Power and Thermal Stress Test
                'title': 'Power and Thermal Stress Test Parameters',
                'parameters': [
                    {
                        'name': 'testtime',
                        'prompt': 'Test duration in seconds (default=300)',
                        'type': int,
                        'default': 300,
                        'validation': lambda x: x > 0
                    }
                ]
            },
            9: {  # EDP Stress Test
                'title': 'EDP Stress Test Parameters',
                'parameters': [
                    {
                        'name': 'testtime',
                        'prompt': 'Test duration in seconds (default=300)',
                        'type': int,
                        'default': 300,
                        'validation': lambda x: x > 0
                    }
                ]
            },
            11: {  # vLLM bench mark
                'title': 'vLLM Benchmark Test Parameters',
                'parameters' : [
                    # Tier 0 – Critical (must start & load)
                     {
                        'name': 'env_update',
                        'prompt': '**** Please ensure that the DOCKER, HUGGINGFACE, and PROXY settings are properly updated in the gpu/bmg/tests/ai_wl/.env file before proceeding ****',
                        'type': bool,
                        'default': True,
                        'validation': lambda v: v is None or isinstance(v, bool)
                    },
                    {
                        'name': 'model',
                        'prompt': 'Model to serve',
                        'type': str,
                        'default': 'meta-llama/Meta-Llama-3-8B',
                        'validation': lambda s: s is None or (isinstance(s, str) and len(s) > 0)
                    },                
                    # Tier 2 – Parallelism & scaling
                    {
                        'name': 'tp',
                        'prompt': 'Tensor parallel size',
                        'type': int,
                        'default': 1,
                        'validation': lambda v: isinstance(v, int) and v > 0
                    },
                    
                    # Tier 3 – Runtime throughput & latency
                    {
                        'name': 'max_concurrency',
                        'prompt': 'Maximum concurrency',
                        'type': int,
                        'default': 16,
                        'validation': lambda v: v is None or (isinstance(v, int) and v > 0)
                    },
                    {
                        'name': 'input_len',
                        'prompt': 'Input length',
                        'type': int,
                        'default': 128,
                        'validation': lambda v: v is None or (isinstance(v, int) and v > 0)
                    },
                    {
                        'name': 'output_len',
                        'prompt': 'Output length',
                        'type': int,
                        'default': 128,
                        'validation': lambda v: v is None or (isinstance(v, int) and v > 0)
                    }

                ]
            }, 
            12: {  # MLPerf Test
                'title': 'MLPerf Benchmark Test Parameters',
                'parameters': [
                    {
                        'name': 'docker_image',
                        'prompt': 'Docker image to use for MLPerf testing',
                        'type': str,
                        'default': 'intel/intel-optimized-pytorch:mlperf-inference-6.0-llama_xpu'
                    },
                    {
                        'name': 'data_dir',
                        'prompt': 'Path to dataset directory',
                        'type': str,
                        'default': '/home/{user}/data'
                    },
                    {
                        'name': 'model_dir',
                        'prompt': 'Path to model directory',
                        'type': str,
                        'default': '/home/{user}/model/.llama/checkpoints'
                    },
                    {
                        'name': 'model_type',
                        'prompt': 'Model type',
                        'type': str,
                        'default': 'llama3_1-8b',
                        'choices': {
                            '1': 'llama3_1-8b',
                            '2': 'llama2-70b'
                        }
                    },
                    {
                        'name': 'scenario',
                        'prompt': 'Benchmark scenario',
                        'type': str,
                        'default': 'Offline',
                        'choices': {
                            '1': 'Offline',
                            '2': 'Server',
                            '3': 'SingleStream'
                        }
                    },
                    {
                        'name': 'mode',
                        'prompt': 'Benchmark mode',
                        'type': str,
                        'default': 'Performance',
                        'choices': {
                            '1': 'Performance',
                            '2': 'Accuracy'
                        }
                    }
                ]
            }
        }

    def _init_version_configurations(self):
        """Initialize version and tool configurations."""
        # Platform version configurations
        self.versions_dict = {
                            '0.5':
                            {
                                'Driver Version'            : 'E9151260B8A403E77D5C7D9',
                                'GFX Firmware Version'      : 'BMG__21.1137',
                                'GFX Data Firmware Version' : '0x1',
                            },
                            '0.6':
                            {
                                'Driver Version'            : 'E9151260B8A403E77D5C7D9',
                                'GFX Firmware Version'      : 'BMG__21.1137',
                                'GFX Data Firmware Version' : '0x1',
                            }
                        }

        # Tool versions configuration
        self.tool_versions_dict = {
            'DGDiag': '3.10.2',
            'PTAT': '4.8.1'
        }

        # DGDiag error descriptions CSV file path
        self.diag_error_description_csv_path = os.path.join(os.path.dirname(__file__), 'tests', 'dgdiag', 'Diag_Error_Description.csv')

    def _init_power_thermal_edp_specs(self):
        """Initialize power and thermal specifications."""
        # Default power threshold in watts (used as fallback when PsysPL2 cannot be determined).
        # 190.0W represents 0.95 * 200W, which is the typical PsysPL2 for BMG platforms; using a slightly
        # reduced value provides a conservative safety margin when platform-specific data is unavailable.
        self.default_power_threshold_watts = 190.0

        # Calculate dynamic power threshold if possible, otherwise use fallback
        calculated_power_threshold = self.default_power_threshold_watts
        if self.platform_utils:
            try:
                # Use platform_utils to calculate dynamic power threshold based on PsysPL2
                calculated_power_threshold = self.platform_utils.get_min_power_threshold(self.default_power_threshold_watts)
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Error calculating dynamic power threshold: {e}")
                    self.logger.warning(f"Using default power threshold of {self.default_power_threshold_watts}W")

        # Power and thermal specifications for BMG platform
        self._power_thermal_specs = {
            'power': {
                'min_threshold_watts': calculated_power_threshold,
                'description': 'Minimum average power consumption during stress test'
            },
            'thermal': {
                'core_temp_max_celsius': 105.0,  # Maximum core temperature for PASS
                'memory_temp_max_celsius': 85.0, # Maximum memory temperature for PASS
                'description': 'Maximum temperature thresholds for thermal validation'
            }
        }

        # Test timeout and buffer configurations
        self.test_timeout_dict = {
            'EDP_STRESS_TEST': {
                'dgdiag_duration_buffer': 35,  # Additional seconds for DGDiag duration (workaround for potential DGDiag bug)
                'timeout_buffer': 60  # Additional seconds for test timeout threshold
            }
        }

    def _init_pcie_bandwidth_factors(self):
        """Initialize PCIe bandwidth calculation factors."""
        # PCIe bandwidth calculation factors for pass/fail threshold determination
        # These factors are applied to theoretical bandwidth to calculate realistic thresholds
        self.pcie_bandwidth_factors = {
            'achievable': 0.95,      # 95% achievable bandwidth factor (realistic PCIe efficiency)
            'part2part': 0.95,       # Part-to-part variation factor (component manufacturing tolerance)
            'temp_impact': 0.99,     # Temperature impact factor (thermal effects on performance)
            'std_dev_impact': 0.99   # Standard deviation impact factor (measurement variation)
        }
        # Overall overhead factor for final bandwidth threshold calculation
        self.pcie_bandwidth_overhead_factor = {'compute':{'h2d': (256/(256+20)), 'd2h': (64/(64+20)), 'bidirectional': (256/(256+20))},
                                               'copy':{'h2d': (256/(256+20)), 'd2h': (256/(256+20)), 'bidirectional': (256/(256+20))}}
    
    @property
    def power_thermal_specs(self):
        """Lazy-loaded power thermal specifications.
        
        This property initializes power thermal specs only when first accessed,
        which is particularly useful for power stress and EDP stress tests.
        """
        if self._power_thermal_specs is None:
            self._init_power_thermal_edp_specs()
        return self._power_thermal_specs

    def _init_package_requirements(self):
        """Initialize package and library requirements."""
        self.python_packages = [
            'pandas',
            'matplotlib',
            'requests'
        ]

        self.linux_libraries = [
            'xpu-smi',
            'dos2unix',
            'ipmitool',
            'unzip',
            'python3-pandas',
            'python3-matplotlib',
            'python3-dotenv',
            'python3-tk',
            'docker.io',
            'libpciaccess0',
            'build-essential',
            'stress-ng',
            'lm-sensors',
            'software-properties-common',
            'libze-intel-gpu1',
            'libze1',
            'intel-metrics-discovery',
            'intel-opencl-icd',
            'clinfo',
            'intel-gsc',
            'intel-media-va-driver-non-free',
            'libmfx-gen1.2',
            'libvpl2',
            'libvpl-tools',
            'libva-glx2',
            'va-driver-all',
            'vainfo',
            'libze-dev',
            'intel-ocloc',
            'libze-intel-gpu-raytracing',
            'libvulkan1',
            'vulkan-tools',
            'x11-utils',
            'xauth',
            'x11-apps'
        ]

