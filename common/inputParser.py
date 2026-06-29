# Copyright (C) 2024-2026 Intel Corporation
import argparse
import importlib
from common.utils import Utils

class InputParser:
    _initialization_logged = False  # Class variable to track if initialization messages have been shown
    
    def __init__(self, device_manager, logger, args=None):
        """
        Accepts a variable list of arguments, validates them, and saves them in self.args if valid.
        Raises ValueError if any argument is invalid.
        """
        self.device_manager = device_manager
        self.parser = argparse.ArgumentParser(description="Verification Test Suite Runner.", add_help=False)
        self.logger = logger
        self.utils = Utils(self.logger)

        self.parser.add_argument('-h', '--help', action='store_true', help="Show help message", dest='help')
        self.parser.add_argument('-tn', help="Test Number (int or 'c' or 'd' or 'a')", type=self.utils.tn_type, default=0, dest='tn')
        self.parser.add_argument('-tc', help="JSON file test config path",type=str, dest='tc')
        self.parser.add_argument('-rep', help="Number of repetitions to run the test (1-1000)", type=InputParser._repetitions_type, default=1, dest='repetitions')
        self.parser.add_argument('-d', help="Collect debug logs after each test execution", type=lambda x: x.lower() == 'true' if isinstance(x, str) else bool(x), default=False, dest='debug_collection')
        self.parser.add_argument('-stop_on_error', help="Stop execution on first test failure and wait for user debug (for run_all, config-driven, and repeated tests)", type=lambda x: x.lower() == 'true' if isinstance(x, str) else bool(x), default=False, dest='stop_on_error')
        self.parser.add_argument('-live_mon', help="Enable the GPU usage and temperature live monitor dialog", type=lambda x: x.lower() == 'true' if isinstance(x, str) else bool(x), default=False, dest='live_mon')

        # Load platform-specific modules and definitions
        module_name = f"gpu.{self.device_manager.gpu_family}.input_arguments"
        self.add_arguments = getattr(importlib.import_module(module_name), "add_arguments", None)
        self.parser = self.add_arguments(self.parser)
        
        # Load platform definitions for parameter prompting
        platform_defs_module_name = f"gpu.{self.device_manager.gpu_family}.platform_defs"
        platform_defs_module = importlib.import_module(platform_defs_module_name)
        
        # Create BMGPlatformDefs instance to access test parameter definitions
        platform_defs_instance = platform_defs_module.BMGPlatformDefs(self.logger, self.device_manager)
        self.test_parameter_definitions = platform_defs_instance.test_parameter_definitions
        if not InputParser._initialization_logged:
            self.logger.info(f'Loaded test parameter definitions for {self.device_manager.gpu_family}')
            InputParser._initialization_logged = True  # Set flag to prevent future logging
        
        # If args is None, use sys.argv[1:]
        if args is None:
            import sys
            self.args = sys.argv[1:]
        else:
            self.args = args

    def parse(self):
        # Use parse_known_args to handle unknown arguments gracefully
        self.parsed_args, self.unknown_args = self.parser.parse_known_args(self.args)
        
        # Store unknown args for later processing by test classes
        if self.unknown_args and not InputParser._initialization_logged:
            self.logger.info(f"Found test-specific arguments: {' '.join(self.unknown_args)}")
        
        return self.parsed_args
    
    def reparse_with_test_arguments(self, test_instance):
        """
        Reparse arguments after test-specific arguments have been added.
        
        Args:
            test_instance: Test instance that has added its arguments
            
        Returns:
            Parsed arguments with test-specific parameters
        """
        try:
            # The test instance has already added its arguments to self.input_parser.parser
            # through the add_parser_argument method, so we just need to reparse
            
            # Use only the original arguments, NOT the unknown_args, since the test class
            # has now added those argument definitions to the parser
            all_args = self.args
            
            # Now that test-specific arguments have been added to the parser,
            # we should be able to parse all arguments successfully
            try:
                self.parsed_args = self.parser.parse_args(all_args)
                return self.parsed_args
                
            except SystemExit as se:
                # If strict parsing still fails, provide detailed error information
                import sys
                from io import StringIO
                
                # Capture stderr to get the actual error message
                old_stderr = sys.stderr
                sys.stderr = captured_err = StringIO()
                
                try:
                    self.parser.parse_args(all_args)
                except SystemExit:
                    pass
                finally:
                    sys.stderr = old_stderr
                
                error_msg = captured_err.getvalue()
                
                # Try to identify which specific argument is causing the problem
                if "unrecognized arguments:" in error_msg:
                    # Extract the problematic arguments
                    problem_args = error_msg.split("unrecognized arguments:")[1].strip()
                    
                    # Try to map back to the original argument that caused the issue
                    self.logger.error(f"Argument parsing failed. Unrecognized arguments: {problem_args}")
                    
                    # Analyze the command line to find which parameter is likely causing issues
                    problem_values = problem_args.split()
                    
                    # Track which parameters precede the problematic values
                    for i, arg in enumerate(all_args):
                        if arg.startswith('-') and i+1 < len(all_args):
                            next_arg = all_args[i+1]
                            if next_arg in problem_values:
                                self.logger.error(f"Problem detected with parameter '{arg}' and value '{next_arg}'")
                                
                                # Check if this is a choice validation issue
                                for action in self.parser._actions:
                                    if hasattr(action, 'option_strings') and arg in action.option_strings:
                                        if hasattr(action, 'choices') and action.choices and next_arg not in action.choices:
                                            self.logger.error(f"Value '{next_arg}' is not valid for parameter '{arg}'. Valid choices are: {list(action.choices)}")
                                        elif hasattr(action, 'choices') and action.choices and next_arg in action.choices:
                                            # Value is actually valid, this might be an argparse internal issue
                                            self.logger.error(f"Value '{next_arg}' should be valid for parameter '{arg}' (choices: {list(action.choices)}). This may be an argument ordering issue.")
                                        break
                                break
                else:
                    self.logger.error(f"Argument parsing failed: {error_msg.strip()}")
                
                # Provide additional debugging information
                self.logger.error(f"Command line arguments being parsed: {' '.join(all_args)}")
                self.logger.error("DEBUGGING: Check if test-specific arguments were added properly to parser")
                
                # Debug: List all arguments that the parser knows about
                known_args = []
                for action in self.parser._actions:
                    if hasattr(action, 'option_strings'):
                        known_args.extend(action.option_strings)
                self.logger.debug(f"Parser knows about these arguments: {sorted(set(known_args))}")
                
                raise SystemExit(1)  # Re-raise to maintain original behavior
            
        except SystemExit:
            # Re-raise SystemExit (from argparse errors) to maintain original behavior
            raise
        except Exception as e:
            self.logger.warning(f"Could not reparse with test arguments: {e}")
            return self.parsed_args
    


    @staticmethod
    def _repetitions_type(value):
        try:
            ivalue = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"'{value}' is not a valid integer")
        if ivalue < 1 or ivalue > 1000:
            raise argparse.ArgumentTypeError(f"repetitions must be between 1 and 1000 (got {ivalue})")
        return ivalue

    @staticmethod
    def prompt_test_parameters(test_number, parsed_args, input_func, logger, test_parameter_definitions):
        """
        Generic function to prompt for test-specific parameters.
        
        Args:
            test_number (int): Test number
            parsed_args: Parsed arguments object to update
            input_func: Function to get user input
            logger: Logger instance
            test_parameter_definitions (dict): Parameter definitions from platform
            
        Returns:
            bool: True if parameters were set successfully, False if cancelled
        """
        if test_number not in test_parameter_definitions:
            return True  # No special parameters needed
        
        test_def = test_parameter_definitions[test_number]
        
        print(f"\n--- {test_def['title']} ---")
        
        try:
            for param in test_def['parameters']:
                # Check if parameter should be prompted based on condition
                if 'condition' in param and not param['condition'](parsed_args):
                    continue
                    
                # Handle choice-based parameters
                if 'choices' in param:
                    # Build inline choices display: "1. option1, 2. option2"
                    choices_display = ", ".join([f"{k}. {v}" for k, v in param['choices'].items()])
                    
                    # Find which key corresponds to the default value
                    default_key = None
                    for k, v in param['choices'].items():
                        if v == param['default']:
                            default_key = k
                            break
                    default_key = default_key or list(param['choices'].keys())[0]
                    
                    prompt_text = f"{param['prompt']} ({choices_display}) [default={default_key}]: "
                    choice = input_func(prompt_text).strip()
                    
                    # Accept empty (use default), number key, or actual value
                    if choice == '':
                        setattr(parsed_args, param['name'], param['default'])
                    elif choice in param['choices']:
                        # User entered a number key like "1"
                        setattr(parsed_args, param['name'], param['choices'][choice])
                    elif choice in param['choices'].values():
                        # User entered the actual value
                        setattr(parsed_args, param['name'], choice)
                    else:
                        # Invalid input, use default
                        logger.warning(f"Invalid choice '{choice}', using default '{param['default']}'")
                        setattr(parsed_args, param['name'], param['default'])
                        
                # Handle regular parameters
                else:
                    while True:  # Keep asking until valid input or empty (default)
                        # Build prompt with default value (expand {user} placeholder for display)
                        import getpass, os
                        username = os.environ.get('SUDO_USER') or getpass.getuser()
                        display_default = str(param['default']).replace('{user}', username)
                        prompt_text = f"{param['prompt']} [{display_default}]: "
                        value = input_func(prompt_text).strip()
                        
                        if value == '':
                            # Empty input, use default
                            setattr(parsed_args, param['name'], param['default'])
                            break
                        
                        try:
                            typed_value = param['type'](value)
                            # Validate if validation function provided
                            if 'validation' in param and not param['validation'](typed_value):
                                logger.warning(f"Invalid value '{value}'. Please try again.")
                                continue  # Ask again
                            else:
                                setattr(parsed_args, param['name'], typed_value)
                                break  # Valid input, move to next parameter
                        except ValueError:
                            logger.warning(f"Invalid {param['type'].__name__} '{value}'. Please try again.")
                            continue  # Ask again
                        
            return True
            
        except Exception as e:
            logger.error(f"Error prompting for parameters: {e}")
            return True  # Continue with defaults

