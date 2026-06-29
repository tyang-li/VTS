# Copyright (C) 2024-2026 Intel Corporation
import logging
import os
import sys
import re
from datetime import datetime

class Logger:
    COLORS = {
        'HEADER_M': '\033[96m',      # Cyan
        'HEADER_Y': '\033[93m',      # Yellow
        'HEADER_G': '\033[92m',      # Green
        'SUBHEADER': '\033[94m',     # Blue
        'MAGENTA': '\033[95m',       # Magenta
        'PASS': '\033[92m',          # Green
        'FAIL': '\033[91m',          # Red
        'WARNING': '\033[93m',       # Yellow
        'ERROR': '\033[91m',         # Red
        'INFO': '\033[0m',           # Default
        'ENDC': '\033[0m',           # Reset
    }

    def mask_tokens(self, s: str, mask: str = "#####"):
        keys = ("HUGGING_FACE_HUB_TOKEN", "DOCKER_USER", "DOCKER_TOKEN")

        if not s:
            return s

        keys_alt = "|".join(re.escape(k) for k in keys)

        pattern = re.compile(
            rf"((?:{keys_alt})=)"            # group(1): KEY=
            r"("                             # group(2): value (quoted or not)
            r"(?P<q>['\"]).*?(?P=q)"         # quoted value
            r"|"
            r"[^\s;\\\r\n]+"                 # unquoted value
            r")"
        )

        return pattern.sub(lambda m: m.group(1) + mask, s)

    def __init__(self, log_file, name='VTS Logger'):
        log_dir = os.path.dirname(log_file)
        from common.utils import Utils
        Utils.secure_log_directory(log_dir)
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)

        if not self.logger.handlers:
            fh = logging.FileHandler(log_file)
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s \t- %(message)s'))
            self.logger.addHandler(fh)

    def _strip_tags(self, msg):
        return re.sub(r"\[(HEADER_M|HEADER_Y|HEADER_G|SUBHEADER|PASS|FAIL)\]\s*", "", msg)

    def info(self, msg):
        msg = self.mask_tokens(msg)
        print(f"\t{msg}")
        self.logger.info(f"\t\t{msg}")

    def debug(self, msg):
        msg = self.mask_tokens(msg)
        print(f"{self.COLORS['INFO']}DEBUG: {msg}{self.COLORS['ENDC']}")
        self.logger.warning(msg)

    def log_file_only(self, msg, level='INFO'):
        """
        Log message only to file without printing to console.
        This writes to both the Logger's file handlers AND the current OutputLogger file if active.
        
        Args:
            msg (str): Message to log
            level (str): Log level ('DEBUG', 'INFO', 'WARNING', 'ERROR')
        """
        msg = self.mask_tokens(msg)
        # Write to Logger's file handlers (VTS main log)
        for handler in self.logger.handlers:
            if isinstance(handler, logging.FileHandler):
                # Create a log record manually
                level_num = getattr(logging, level.upper(), logging.INFO)
                record = self.logger.makeRecord(
                    self.logger.name,
                    level_num,
                    "",  # pathname
                    0,   # lineno
                    msg,
                    (),  # args
                    None  # exc_info
                )
                handler.emit(record)
        
        # Also write to OutputLogger's file if it's currently active
        # Check if stdout has been replaced by OutputLogger's Tee
        if hasattr(sys.stdout, 'streams') and len(sys.stdout.streams) > 1:
            # This means OutputLogger is active, write to its file
            file_stream = sys.stdout.streams[1]  # Index 1 is the file stream
            # Write plain text without timestamp for cleaner test-specific log
            file_stream.write(f"{msg}\n")
            file_stream.flush()

    def warning(self, msg):
        msg = self.mask_tokens(msg)
        print(f"\t{self.COLORS['WARNING']}WARNING: {msg}{self.COLORS['ENDC']}")
        self.logger.warning(msg)

    def error(self, msg):
        msg = self.mask_tokens(msg)
        print(f"\t{self.COLORS['ERROR']}ERROR: {msg}{self.COLORS['ENDC']}")
        self.logger.error(f'{msg}')

    def subheader(self, msg):
        print(f"\n{self.COLORS['SUBHEADER']}{msg.upper()}{self.COLORS['ENDC']}")
        self.logger.info(f'\t{msg.upper()}')

    def pass_msg(self, msg):
        print(f"\t{self.COLORS['PASS']}{msg}{self.COLORS['ENDC']}")
        self.logger.info(msg)

    def fail_msg(self, msg):
        print(f"\t{self.COLORS['FAIL']}{msg}{self.COLORS['ENDC']}")
        self.logger.info(msg)

    def print_menu(self, test_option, test_name):
        # Ensure test_option is a string and pad/truncate to 2 characters
        option_str = str(test_option)[:2].rjust(2)
        
        # Use grey color for unavailable tests, white for available tests
        if "Not Available" in test_name:
            text_color = '\033[90m'  # Grey color
        else:
            text_color = self.COLORS['INFO']  # Default/white color
        
        print(f"\t{self.COLORS['WARNING']}{option_str}{self.COLORS['ENDC']} : {text_color}{test_name}{self.COLORS['ENDC']}")
        self.logger.info(f"\t{option_str} : {test_name}")

    def print_menu_2(self, test_name):
        # Ensure test_option is a string and pad/truncate to 2 characters
        print(f"{self.COLORS['HEADER_G']}{test_name}{self.COLORS['ENDC']}")
        self.logger.info(f"\t{test_name}")

    def _header(self, msg, color_key, style='normal'):
        """Unified header method with different styles"""
        msg_upper = msg.upper()
        frame_width = 80
        frame_line = "*" * frame_width
        
        # Center the message within the frame
        padding = (frame_width - len(msg_upper)) // 2
        centered_msg = f"{' ' * padding}{msg_upper}{' ' * (frame_width - len(msg_upper) - padding)}"
        
        # Style variants
        if style == 'spacious':  # old header_m style
            print(f"\n{self.COLORS[color_key]}{frame_line}")
            print("\n")
            print(f"{centered_msg}")
            print("\n")
            print(f"{frame_line}{self.COLORS['ENDC']}")
            
            self.logger.info(frame_line)
            self.logger.info("")
            self.logger.info(centered_msg)
            self.logger.info(frame_line)
            self.logger.info("")
            
        elif style == 'compact':  # old header_y style
            print(f"\n{self.COLORS[color_key]}{frame_line}")
            print(f"{centered_msg}")
            print(f"{frame_line}{self.COLORS['ENDC']}")
            
            self.logger.info(frame_line)
            self.logger.info(centered_msg)
            self.logger.info(frame_line)
            
        else:  # normal - old header_g style
            print(f"\n{self.COLORS[color_key]}{frame_line}")
            print(f"  {centered_msg}  ")
            print(f"{frame_line}{self.COLORS['ENDC']}")
            
            self.logger.info(frame_line)
            self.logger.info(f"  {msg_upper}  ")
            self.logger.info(frame_line)

    def header_m(self, msg):
        """Header with blue star frames and magenta text"""
        msg_upper = msg.upper()
        frame_width = 80
        frame_line = "*" * frame_width
        
        # Center the message within the frame
        padding = (frame_width - len(msg_upper)) // 2
        centered_msg = f"{' ' * padding}{msg_upper}{' ' * (frame_width - len(msg_upper) - padding)}"
        
        # Print with blue stars and magenta text (spacious style)
        print(f"\n{self.COLORS['SUBHEADER']}{frame_line}{self.COLORS['ENDC']}")
        print("\n")
        print(f"{self.COLORS['MAGENTA']}{centered_msg}{self.COLORS['ENDC']}")
        print("\n")
        print(f"{self.COLORS['SUBHEADER']}{frame_line}{self.COLORS['ENDC']}")
        
        # Log to file (without color codes)
        self.logger.info(frame_line)
        self.logger.info("")
        self.logger.info(centered_msg)
        self.logger.info(frame_line)
        self.logger.info("")

    def header_y(self, msg):
        self._header(msg, 'HEADER_Y', 'compact')

    def header_g(self, msg):
        self._header(msg, 'HEADER_G', 'normal')
        
    def header_vts(self, msg):
        """Custom header with blue star frames and yellow text for VTS branding"""
        msg_upper = msg.upper()
        frame_width = 80
        frame_line = "*" * frame_width
        
        # Center the message within the frame
        padding = (frame_width - len(msg_upper)) // 2
        centered_msg = f"{' ' * padding}{msg_upper}{' ' * (frame_width - len(msg_upper) - padding)}"
        
        # Print with blue stars and yellow text (spacious style)
        print(f"\n{self.COLORS['HEADER_M']}{frame_line}{self.COLORS['ENDC']}")
        print("\n")
        print(f"{self.COLORS['HEADER_Y']}{centered_msg}{self.COLORS['ENDC']}")
        print("\n")
        print(f"{self.COLORS['HEADER_M']}{frame_line}{self.COLORS['ENDC']}")
        
        # Log to file (without color codes)
        self.logger.info(frame_line)
        self.logger.info("")
        self.logger.info(centered_msg)
        self.logger.info(frame_line)
        self.logger.info("")

    def printTableFromDict(self, data_dict):
        if not data_dict:
            self.info("No data available to display.")
            return

        # Extract headers from the first dictionary
        headers = list(next(iter(data_dict.values())).keys())
        col_widths = {header: len(header) for header in headers}

        # Calculate maximum width for each column
        for entry in data_dict.values():
            for header in headers:
                col_widths[header] = max(col_widths[header], len(str(entry.get(header, ''))))

        # Print header row (centered)
        header_row = " | ".join(f"{header:^{col_widths[header]}}" for header in headers)
        separator = "-+-".join('-' * col_widths[header] for header in headers)
        print(f'\n\t{header_row}')
        print(f'\t{separator}')

        # Print each row of data (centered)
        for key, entry in data_dict.items():
            row = " | ".join(f"{str(entry.get(header, '')):^{col_widths[header]}}" for header in headers)
            print(f'\t{row}')

    def separator(self,width):
        frame_line = "*" * width
        print(f"\n{self.COLORS['HEADER_M']}{frame_line}{self.COLORS['ENDC']}")

class OutputLogger:
    def __init__(self, log_path):
        self.log_path = log_path
        self._stdout = None
        self._stderr = None
        self.log_file = None

    class Tee:
        def __init__(self, *streams):
            self.streams = streams

        def write(self, data):
            for i, s in enumerate(self.streams):
                if i == 0:
                    # Console: keep color codes
                    s.write(data)
                else:
                    # Log file: strip color codes
                    s.write(self.strip_ansi_codes(data))
                s.flush()

        def flush(self):
            for s in self.streams:
                s.flush()

        def strip_ansi_codes(self, text):
            ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
            return ansi_escape.sub('', text)

    def __enter__(self):
        self.log_file = open(self.log_path, 'w')
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        sys.stdout = self.Tee(self._stdout, self.log_file)
        sys.stderr = self.Tee(self._stderr, self.log_file)

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self._stdout
        sys.stderr = self._stderr
        if self.log_file:
            self.log_file.close()