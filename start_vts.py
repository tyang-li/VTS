# Copyright (C) 2024-2026 Intel Corporation
import json
import os
import sys
from datetime import datetime
from common.loggerManager import Logger
from common.testSuiteOrchestrator import TestSuiteOrchestrator
from tools.gpu_usage_popup import start_gpu_usage_popup


def display_help():
    """Display help information from README.md"""
    try:
        readme_path = os.path.join(os.path.dirname(__file__), 'README.md')
        if os.path.exists(readme_path):
            with open(readme_path, 'r', encoding='utf-8') as f:
                print(f.read())
        else:
            display_basic_help()
    except Exception as e:
        print(f"Error reading README.md: {e}")
        display_basic_help()


def display_basic_help():
    help_text = """
Intel GPU Validation Test Suite
==============================

Usage: python start_vts.py [options]

Options:
  -h, --help              Show this help message and exit
  -tn TEST_NUMBER         Test number ('a' for all, 'c' for log collection, 'd' for debug)
  -tc FILE                Test configuration file (JSON format)
  -rep N                  Number of repetitions (default: 1)
  -mt TYPE                Monitor type: xpum, dgdiag, None (default: xpum)
  -cs TOOL                CPU stress tool: None, stress-ng, ptat (default: None)
  -d True/False           Collect debug logs after test execution (default: False)
  -stop_on_error True/False  Stop on first test failure (default: False)
  -live_mon True/False    Enable live monitoring popup during test execution (default: False)
  -pcie_downgrade True/False  Disable PCIe downgrade mode before tests where supported (default: False)
  --skip-system-checks    Skip pre-flight system validation checks

System Pre-flight Checks:
  VTS runs comprehensive system checks before test execution including:
  • OS platform compatibility
  • Python version requirements  
  • Required system commands
  • Available memory and disk space
  • System load analysis
  
  Critical checks must pass for VTS to continue.
  Warning-level checks allow user choice to continue.
  
  Note: System checks are automatically skipped for log collection mode (-tn c).

For detailed information, please refer to the README.md file.
"""
    print(help_text)


def main():
    # Quick help check: handle -h/--help in any argument position
    if any(arg in ('-h', '--help') for arg in sys.argv[1:]):
        display_help()
        return 0

    # Check for system check skip flag (before logger setup for early exit)
    skip_system_checks = '--skip-system-checks' in sys.argv
    if skip_system_checks:
        # Remove the flag so it doesn't interfere with other argument parsing
        sys.argv = [arg for arg in sys.argv if arg != '--skip-system-checks']

    # Setup logger
    try:
        vts_log_file = os.path.join(
            os.getcwd(),
            'logs',
            f"Verification_Test_Suite_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
        )
        logger = Logger(log_file=vts_log_file)
    except Exception as e:
        print(f'Error initializing Logger: {e}')
        return 1

    # Root/sudo check
    if hasattr(os, 'geteuid') and os.geteuid() != 0:
        logger.error('This program must be run as root or with sudo privileges.')
        logger.error('Please run: sudo python3 start_vts.py [options]')
        return 1

    # Initialize orchestrator
    try:
        suite = TestSuiteOrchestrator(logger)
    except SystemExit as se:
        logger.error('Argument parsing failed. Use -h or --help for usage information.')
        return se.code if isinstance(se.code, int) else 1
    except Exception as e:
        logger.error(f'Error initializing TestSuiteOrchestrator: {e}')
        return 1
    
    # Auto-skip system checks for log collection mode
    if hasattr(suite.parsed_args, 'tn') and suite.parsed_args.tn == 'c':
        skip_system_checks = True
        logger.info("Log collection mode detected - skipping system pre-flight checks")
    
    # Ensure common VTS tools are executable (prevent permission denied errors)
    try:
        suite.utils.ensure_common_tools_executable()
    except Exception as e:
        logger.warning(f"Error setting tool permissions: {e}")
        logger.info("Continuing - individual tests will handle tool permissions as needed")
    
    if suite.parsed_args.live_mon:
        start_gpu_usage_popup(logger)
    else:
        logger.info(f"Gpu usage live monitor is not enabled")
    
    # Pre-flight system checks
    if not skip_system_checks:
        try:
            from common.systemChecker import SystemChecker
            system_checker = SystemChecker(logger)
            
            # Run system checks - returns False if critical checks fail
            if not system_checker.run_all_checks():
                logger.error("System pre-flight checks failed. VTS cannot continue.")
                return 1
                
        except (ImportError, AttributeError, TypeError, IOError, OSError) as e:
            logger.error(f"Error running system checks: {e}")
            logger.warning("Continuing without full system validation...")
    else:
        logger.info("System pre-flight checks skipped (--skip-system-checks)")
    
    # Run tests
    if getattr(suite.parsed_args, 'tc', None):
        try:
            tc_path = os.path.realpath(suite.parsed_args.tc)
            vts_root = os.path.realpath(os.path.dirname(__file__))
            if not tc_path.startswith(vts_root + os.sep) and tc_path != vts_root:
                logger.error(f'Test config path must be within the VTS directory: {vts_root}')
                return 1
            if not os.path.isfile(tc_path):
                logger.error(f'Test config file not found: {suite.parsed_args.tc}')
                return 1
            with open(tc_path, 'r') as f:
                test_sequence = json.load(f)
            if not isinstance(test_sequence, list):
                logger.error('Test config must be a JSON array of test objects')
                return 1
            for i, test_cfg in enumerate(test_sequence):
                if not isinstance(test_cfg, dict):
                    logger.error(f'Test config entry {i} is not a JSON object')
                    return 1
                if 'tn' not in test_cfg:
                    logger.error(f'Test config entry {i} missing required "tn" field')
                    return 1
            for test_cfg in test_sequence:
                logger.info('')
                suite.run_by_config(test_cfg)
        except json.JSONDecodeError as e:
            logger.error(f'Invalid JSON in test config file: {e}')
            return 1
        except Exception as e:
            logger.error(f'Error running test sequence from config: {e}')
            return 1

    elif suite.parsed_args.tn == 0:
        try:
            suite.menu()
        except Exception as e:
            logger.error(f'Error running test suite: {e}')
            return 1

    else:
        try:
            
            suite.run_by_option(suite.parsed_args.tn)
        except Exception as e:
            logger.error(f'Error running test suite: {e}')
            return 1

    return 0


if __name__ == '__main__':
    raise SystemExit(main())