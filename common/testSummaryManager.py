# Copyright (C) 2024-2026 Intel Corporation
import os
import re
import glob
import time
from datetime import datetime

# Handle both relative imports (when used as package) and absolute imports (when used standalone)
try:
    from .common_defs import (REALISTIC_POWER_MIN_W, REALISTIC_POWER_MAX_W,
                              REALISTIC_TEMP_MIN_C, REALISTIC_TEMP_MAX_C,
                              REALISTIC_FREQ_MIN_MHZ, REALISTIC_FREQ_MAX_MHZ,
                              PCI_CLASSES)
except ImportError:
    # Fallback to absolute imports for standalone usage
    from common_defs import (REALISTIC_POWER_MIN_W, REALISTIC_POWER_MAX_W,
                             REALISTIC_TEMP_MIN_C, REALISTIC_TEMP_MAX_C,
                             REALISTIC_FREQ_MIN_MHZ, REALISTIC_FREQ_MAX_MHZ,
                             PCI_CLASSES)


class ResultsSummarizer:
    """
    Generates an HTML summary table for test logs in a given folder.
    Each row contains: Test Name, Test Result (hyperlink to log), Date of Creation.
    Table is sorted by date (descending).
    """
    def __init__(self, logger):
        # Import pandas and matplotlib at runtime with error handling
        try:
            import pandas as pd
            import matplotlib
            import matplotlib.pyplot as plt
            matplotlib.use('Agg')  # Use non-interactive backend for headless environments
            
            self.pd = pd
            self.plt = plt
        except ImportError as e:
            raise ImportError("pandas and matplotlib are required for ResultsSummarizer") from e
        
        self.logger = logger
        project_root = os.path.dirname(os.path.abspath(__file__))
        self.logs_folder = os.path.abspath(os.path.join(project_root, '..', 'logs'))

    def _get_monitoring_type_from_log(self, log_filename):
        """
        Parse the log file to determine if monitoring was enabled for this test execution.
        
        Args:
            log_filename: The basename of the log file (e.g., "Test_Name_execution_timestamp_result.log")
            
        Returns:
            str: The monitoring type ('xpum', 'dgdiag', 'None') or None if not found
        """
        log_path = os.path.join(self.logs_folder, log_filename)
        if not os.path.exists(log_path):
            return None
            
        try:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                
            # Look for the Test Arguments section
            # The format should be: -mt = value | description
            import re
            mt_pattern = r'-mt\s*=\s*(\w+)\s*\|'
            match = re.search(mt_pattern, content)
            
            if match:
                return match.group(1)
            else:
                # Fallback: look for other patterns that might indicate monitoring type
                if 'Monitor Type' in content:
                    # Look for lines containing monitor type information
                    lines = content.split('\n')
                    for line in lines:
                        if '-mt' in line and '=' in line:
                            # Extract value after '=' and before '|'
                            parts = line.split('=', 1)
                            if len(parts) == 2:
                                value_part = parts[1].strip()
                                if '|' in value_part:
                                    value = value_part.split('|')[0].strip()
                                    return value
                                return value_part.strip()
                
        except Exception as e:
            # If we can't parse the log file, return None (unknown)
            pass
            
        return None

    def summarize(self):
        self.logger.subheader('Summarizing results')
        self.logger.info('This might take a few mins...')
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_html = f"Summary_Report_{timestamp}.html"
        pattern = os.path.join(self.logs_folder, "*_execution_*_*.log")
        log_files = glob.glob(pattern)
        summary_rows = []

        for log_file in log_files:
            base = os.path.basename(log_file)
            parts = base.split('_execution_')
            if len(parts) != 2:
                continue
            test_name = parts[0] 
            rest = parts[1]
            try:
                timestamp_str, result_part = rest.rsplit('_', 1)
                test_result = result_part.replace('.log', '').replace('testPass', 'Pass').replace('testFail', 'Fail')
                date_obj = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                date_str = date_obj.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            summary_rows.append({
                "test_name": test_name,
                "test_result": test_result,
                "log_file": base,
                "date": date_obj,
                "date_str": date_str
            })

        summary_rows.sort(key=lambda x: x["date"], reverse=False)

        html = [
            "<html>",
            "<head>",
            "<title>Intel Verification Test Suite Results Summary</title>",
            "<style>",
            "body { font-family: Consolas, monospace; background: #f8f9fa; }",
            ".title { text-align: center; font-size: 2.5em; margin-top: 30px; margin-bottom: 30px; }",
            "table { border-collapse: collapse; margin: auto; font-size: 1.1em; }",
            "th { background: #0071C5; color: white; padding: 10px 20px; }",
            "td { padding: 8px 20px; text-align: center; }",
            "tr:nth-child(even) { background: #f2f2f2; }",
            "tr:nth-child(odd) { background: #ffffff; }",
            "a { color: #0071C5; text-decoration: none; }",
            ".details-section { margin: 40px auto; width: 68%; background: #fff; border: 1px solid #ccc; padding: 20px; }",
            "img { width: 100%; height: auto; max-width: none; }",
            ".back-to-top { text-align: center; margin: 20px 0; }",
            ".back-to-top a { background: #0071C5; color: white; padding: 8px 16px; text-decoration: none; border-radius: 4px; font-weight: bold; }",
            ".back-to-top a:hover { background: #005a9e; }",
            "</style>",
            "</head>",
            "<body>",
            "<div class='title' id='top'>Intel Verification Test Suite Results Summary</div>",
            "<table>",
            "<tr><th>Test Index</th><th>Test Name</th><th>Test Result</th><th>Log File</th><th>Date</th></tr>"
        ]
        for i, row in enumerate(summary_rows):
            result_lower = row['test_result'].lower()
            if result_lower == 'pass':
                color = 'green'
            elif result_lower == 'fail':
                color = 'red'
            else:
                color = 'black'
            # Sanitize test_name for HTML id - ensure consistent sanitization
            anchor_id = "test_" + str(i+1) + "_" + re.sub(r'[^a-zA-Z0-9_-]', '_', row['test_name']).strip('_')
            html.append(
                f"<tr>"
                f"<td>{i+1}</td>"
                f"<td><a href='#{anchor_id}'>{row['test_name']}</a></td>"
                f"<td style='color:{color};font-weight:bold'>{row['test_result']}</td>"
                f"<td><a href='{row['log_file']}'>{row['log_file']}</a></td>"
                f"<td>{row['date_str']}</td>"
                f"</tr>"
            )
        html.append("</table>")

        # Ensure a plots directory exists inside logs
        plots_dir = os.path.join(self.logs_folder, "plots")
        os.makedirs(plots_dir, exist_ok=True)

        # Add details sections
        html.append("<hr>")
        html.append("<div class='title'>Tests Details</div>")
        for i, row in enumerate(summary_rows):
            # Use the same anchor ID generation as in the table links
            anchor_id = "test_" + str(i+1) + "_" + re.sub(r'[^a-zA-Z0-9_-]', '_', row['test_name']).strip('_')
            
            # Check if monitoring was actually enabled for this test execution
            monitoring_type = self._get_monitoring_type_from_log(row['log_file'])
            
            plot_imgs = {}
            csv_data = None
            mon_type = 'xpum'
            
            # Only search for CSV files if monitoring was enabled (not 'None')
            if monitoring_type and monitoring_type != 'None':
                # Find corresponding paramMon CSV - check repetition files first, then single files
                test_name_underscore = row['test_name'].replace(' ', '_')
                timestamp_str = row['date'].strftime("%Y%m%d_%H%M%S")
                
                # First, check for repetition files (new format)
                rep_pattern_xpum = f"paramMon_{test_name_underscore}_{timestamp_str}_rep*.csv"
                rep_files_xpum = glob.glob(os.path.join(self.logs_folder, rep_pattern_xpum))
                
                rep_pattern_dgdiag = f"paramMon_{test_name_underscore}__*_rep*.csv"
                rep_files_dgdiag = glob.glob(os.path.join(self.logs_folder, rep_pattern_dgdiag))
            
                rep_pattern_dgdiag = f"paramMon_{test_name_underscore}__*_rep*.csv"
                rep_files_dgdiag = glob.glob(os.path.join(self.logs_folder, rep_pattern_dgdiag))
                
                if rep_files_xpum:
                    # Found XPUM repetition files, combine them
                    csv_data = self._combine_repetition_csv_files(rep_files_xpum)
                    mon_type = 'xpum'
                elif rep_files_dgdiag:
                    # Found DGDIAG repetition files, combine them
                    csv_data = self._combine_repetition_csv_files(rep_files_dgdiag)
                    mon_type = 'dgdiag'
                else:
                    # No repetition files found, try single file patterns (original format)
                    
                    # Try XPUM format first
                    csv_pattern_xpum = f"paramMon_{test_name_underscore}_{timestamp_str}.csv"
                    csv_path_xpum = os.path.join(self.logs_folder, csv_pattern_xpum)
                    
                    # Try DGDIAG format (with various timestamp patterns)
                    csv_pattern_dgdiag = f"paramMon_{test_name_underscore}__*.csv"
                    dgdiag_files = glob.glob(os.path.join(self.logs_folder, csv_pattern_dgdiag))
                    
                    if os.path.exists(csv_path_xpum):
                        csv_data = csv_path_xpum  # Keep as file path for single file
                        mon_type = 'xpum'
                    elif dgdiag_files:
                        csv_data = dgdiag_files  # Keep as file list for DGDIAG
                        mon_type = 'dgdiag'
                
                if csv_data is not None and (
                    (isinstance(csv_data, self.pd.DataFrame) and not csv_data.empty)
                    or (not isinstance(csv_data, self.pd.DataFrame) and bool(csv_data))
                ):
                    try:
                        plot_imgs = self.generate_param_monitor_plots(csv_data, plots_dir, i+1, mon_type)
                    except Exception as e:
                        plot_imgs = {}
                        self.logger.error(f"Failed to generate plots for {csv_data}: {e}")
            
            html.append(
                f"<div class='details-section' id='{anchor_id}'>"
                f"<h2>Test Index {i+1}: {row['test_name']}</h2>"
            )
            if plot_imgs:
                for param, img_file in plot_imgs.items():
                    html.append(
                        f"<h3>Time vs {param.capitalize()}</h3>"
                        f"<img src='plots/{img_file}' alt='Time vs {param.capitalize()}' style='width:100%;height:auto;display:block;'><br>"
                    )
            elif monitoring_type == 'None':
                html.append("<p><b>No parameter monitor data available - monitoring was disabled for this test.</b></p>")
            else:
                html.append("<p><b>No parameter monitor data available for this test.</b></p>")
            html.append("</div>")
            html.append("<div class='back-to-top'><a href='#top'>⬆️ Back to Top</a></div>")

        html.append("</body></html>")

        output_path = os.path.join(self.logs_folder, output_html)
        with open(output_path, "w") as f:
            f.write('\n'.join(html))
        return output_path

    def _extract_repetition_number(self, filename):
        """
        Extract repetition number from filename pattern.
        
        Args:
            filename (str): The filename or path containing the _repXX pattern
            
        Returns:
            int or None: The repetition number extracted from the filename, or None if not found
        """
        rep_match = re.search(r'_rep(\d+)', os.path.basename(filename))
        if rep_match:
            return int(rep_match.group(1))
        return None

    def _combine_repetition_csv_files(self, csv_file_list):
        """
        Combine multiple repetition CSV files into a single pandas DataFrame.
        
        Args:
            csv_file_list (list): List of CSV file paths from different repetitions
            
        Returns:
            pandas.DataFrame: Combined dataframe with all repetition data and Repetition column
        """
        combined_dfs = []
        
        for csv_file in sorted(csv_file_list):
            try:
                # Extract repetition number from filename (e.g., _rep01 -> 1, _rep02 -> 2)
                rep_number = self._extract_repetition_number(csv_file)
                if rep_number is None:
                    # If no rep pattern found, log warning and skip this file
                    self.logger.warning(f"Could not extract repetition number from filename: {csv_file}")
                    continue
                
                # Use the same CSV reading logic as in generate_param_monitor_plots
                skip_rows = self._find_csv_start(csv_file)
                try:
                    df = self.pd.read_csv(csv_file, skiprows=skip_rows, on_bad_lines='skip')
                except (self.pd.errors.ParserError, UnicodeDecodeError, OSError):
                    try:
                        df = self.pd.read_csv(csv_file, skiprows=skip_rows, sep=None, engine='python', on_bad_lines='skip')
                    except Exception:
                        continue  # Skip this file if it can't be read
                
                # Add repetition column using the actual rep number from filename
                df['Repetition'] = rep_number
                combined_dfs.append(df)
                
            except Exception as e:
                self.logger.warning(f"Failed to read repetition CSV file {csv_file}: {e}")
                continue
        
        if combined_dfs:
            # Concatenate all dataframes
            return self.pd.concat(combined_dfs, ignore_index=True)
        else:
            return None

    def _find_csv_start(self, file_path):
        """Find the line where CSV data starts in DGDiag files."""
        try:
            with open(file_path, 'r') as f:
                for i, line in enumerate(f):
                    # Look for a line that starts with TimeStamp or Timestamp
                    if line.strip().startswith(('TimeStamp', 'Timestamp')):
                        return i
        except Exception:
            pass
        return 0  # Default to start of file for XPUM format

    def generate_param_monitor_plots(self, csv_data, output_dir, test_index, mon_type='xpum'):
        """
        Generates time vs power, temperature, and frequency plots from paramMon CSV data.
        Supports both 'xpum' and 'dgdiag' monitoring formats.
        For DGDiag, can handle multiple CSV files (one per GPU) and combine them.
        Now also handles pre-combined pandas DataFrames from repetition files.
        Returns a dict with plot types as keys and image file paths as values.
        
        Args:
            csv_data: Can be:
                     - String: path to single CSV file
                     - List: list of CSV file paths (for DGDIAG multi-GPU)
                     - pandas.DataFrame: pre-combined dataframe (for repetitions)
        """
        
        def read_single_csv(file_path):
            """Read a single CSV file and return processed DataFrame."""
            skip_rows = self._find_csv_start(file_path)
            try:
                df = self.pd.read_csv(file_path, skiprows=skip_rows, on_bad_lines='skip')
            except (self.pd.errors.ParserError, UnicodeDecodeError, OSError):
                try:
                    df = self.pd.read_csv(file_path, skiprows=skip_rows, sep=None, engine='python', on_bad_lines='skip')
                except Exception:
                    return None
            return df
        
        # Handle different input types
        if isinstance(csv_data, self.pd.DataFrame):
            # Pre-combined DataFrame from repetitions
            df = csv_data.copy()
            device_col = 'DeviceId' if 'DeviceId' in df.columns else None
        elif isinstance(csv_data, list) and mon_type == 'dgdiag':
            # Multiple CSV files for DGDiag (one per GPU)
            all_dfs = []
            for i, file_path in enumerate(csv_data):
                file_df = read_single_csv(file_path)
                if file_df is not None:
                    # Add a device identifier for multi-GPU plotting
                    file_df['DeviceId'] = i
                    all_dfs.append(file_df)
            
            if not all_dfs:
                return {}
            
            # Combine all DataFrames
            df = self.pd.concat(all_dfs, ignore_index=True)
            device_col = 'DeviceId'  # Enable multi-device plotting for DGDiag
        else:
            # Single CSV file (XPUM or single DGDiag)
            file_path = csv_data if isinstance(csv_data, str) else csv_data[0]
            df = read_single_csv(file_path)
            if df is None:
                return {}
            device_col = None  # Will be set based on format detection
        
        # Strip whitespace from column names to handle CSV formatting issues
        df.columns = df.columns.str.strip()
        
        # **Auto-detect monitoring type based on actual columns present**
        xpum_params = {
            'GPU Power (W)': 'Power (W)',
            'GPU Core Temperature (Celsius Degree)': 'Core Temperature (°C)',
            'GPU Memory Temperature (Celsius Degree)': 'Memory Temperature (°C)',
            'GPU Frequency (MHz)': 'Frequency (MHz)'
        }
        
        dgdiag_params = {
            'Psys_Power_W': 'Psys Power (W)', 
            'GPU_Temp_C': 'GPU Temperature (°C)',
            'GT_Effective_Freq_MHz': 'GT Frequency (MHz)'
        }
        
        # Check which format this CSV actually uses
        xpum_matches = sum(1 for col in xpum_params.keys() if col in df.columns)
        dgdiag_matches = sum(1 for col in dgdiag_params.keys() if col in df.columns)
        
        if xpum_matches > dgdiag_matches:
            # This is actually XPUM format
            params = xpum_params
            time_col = 'Timestamp'
            if 'DeviceId' not in df.columns:
                device_col = None
            else:
                device_col = 'DeviceId'
            actual_mon_type = 'xpum'
        else:
            # This is DGDiag format
            params = dgdiag_params
            time_col = 'TimeStamp'
            # For DGDiag, device_col might be set above if we combined multiple files
            if device_col != 'DeviceId':
                device_col = None
            actual_mon_type = 'dgdiag'
        
        # Check if we have the required columns
        available_params = {col: label for col, label in params.items() if col in df.columns}
        if not available_params:
            return {}
        
        plot_files = {}
        
        # **OPTIMIZATION 3**: Process timestamp once, outside the loop
        if time_col in df.columns:
            if actual_mon_type == 'xpum':
                try:
                    # XPUM format: 2025-12-17T22:32:50.503 (ISO format with milliseconds)
                    df[time_col] = self.pd.to_datetime(df[time_col], format='%Y-%m-%dT%H:%M:%S.%f')
                except (ValueError, TypeError):
                    try:
                        # Try ISO format without milliseconds as fallback
                        df[time_col] = self.pd.to_datetime(df[time_col], format='%Y-%m-%dT%H:%M:%S')
                    except (ValueError, TypeError):
                        # Final fallback to auto-detection with explicit format inference disabled to avoid warning
                        df[time_col] = self.pd.to_datetime(df[time_col], infer_datetime_format=False)
            else:  # dgdiag
                try:
                    # Convert milliseconds format to standard format by replacing last colon with dot and padding to 6 digits
                    df_temp = df[time_col].astype(str).str.replace(r':(\d{3})$', r'.\1000', regex=True)
                    df[time_col] = self.pd.to_datetime(df_temp, format='%d/%m/%Y:%H:%M:%S.%f')
                except (ValueError, TypeError, KeyError):
                    try:
                        # Try original microseconds format
                        df[time_col] = self.pd.to_datetime(df[time_col], format='%d/%m/%Y:%H:%M:%S:%f')
                    except (ValueError, TypeError, KeyError):
                        # Fallback without deprecated parameter - let pandas auto-detect
                        df[time_col] = self.pd.to_datetime(df[time_col])
            
            start_time = df[time_col].min()
            df['time_seconds'] = (df[time_col] - start_time).dt.total_seconds()
        else:
            df['time_seconds'] = range(len(df))
        
        # **OPTIMIZATION 4**: Sample data if dataset is very large (>10k points) - COMMENTED OUT
        # Use this as last resort if other optimizations are not sufficient
        # sample_rate = 1
        # if len(df) > 10000:
        #     sample_rate = max(1, len(df) // 5000)  # Reduce to ~5000 points max
        #     df = df.iloc[::sample_rate, :]
            
        # **OPTIMIZATION 5**: Pre-convert all parameter columns to numeric in batch
        for param in available_params.keys():
            # First replace various forms of missing/invalid data
            df[param] = df[param].replace({
                'N/A': self.pd.NA, 
                'FAILED': self.pd.NA, 
                '  N/A': self.pd.NA,
                'nan': self.pd.NA,
                'NaN': self.pd.NA,
                '': self.pd.NA,
                ' ': self.pd.NA
            })
            
            # Convert to numeric, coercing errors to NaN
            df[param] = self.pd.to_numeric(df[param], errors='coerce')
            
            # Apply realistic value filtering to remove sensor errors
            df[param] = self._filter_realistic_plot_values(df[param], param)
            

        
        # **OPTIMIZATION 6**: Use a more efficient matplotlib backend if available
        self.plt.ioff()  # Turn off interactive mode for faster plotting
        
        for param, ylabel in available_params.items():
            # Skip columns with all NaN values (already converted to numeric)
            if df[param].isna().all():
                continue
            
            # Skip if all values are the same (e.g., all zeros) - no meaningful plot
            unique_values = df[param].dropna().unique()
            if len(unique_values) <= 1:
                continue
                
            fig, ax = self.plt.subplots(figsize=(14, 8), dpi=100)  # Wider figure for full-screen display
            
            has_data = False
            
            if device_col and device_col in df.columns:
                # Multi-device plotting (XPUM) - use more efficient groupby
                for device_id in df[device_col].unique():
                    if self.pd.isna(device_id):
                        continue
                    device_data = df[df[device_col] == device_id]
                    y_data = device_data[param].dropna()  # Remove NaN values
                    
                    if len(y_data) > 0:
                        x_data = device_data.loc[y_data.index, 'time_seconds']
                        ax.plot(x_data, y_data, label=f'Device {device_id}', 
                               marker='.' if len(y_data) < 1000 else None, markersize=1)
                        has_data = True
            else:
                # Single device plotting (DGDIAG)
                valid_data = df[param].dropna()
                if len(valid_data) > 0:
                    x_data = df.loc[valid_data.index, 'time_seconds']
                    ax.plot(x_data, valid_data, label='GPU Device', 
                           marker='.' if len(valid_data) < 1000 else None, markersize=1, color='blue')
                    has_data = True
            
            if not has_data:
                self.plt.close(fig)
                continue
                
            ax.set_xlabel('Time (seconds)')
            ax.set_ylabel(ylabel)
            ax.set_title(f'Test {test_index}: Time vs {ylabel}')
            ax.legend()
            ax.grid(True, alpha=0.3)
            
            # **OPTIMIZATION 7**: Save with lower quality and full width
            safe_param = re.sub(r'[^\w\s-]', '_', param).strip()
            plot_filename = f'test{test_index}_{safe_param}.png'
            plot_path = os.path.join(output_dir, plot_filename)
            
            fig.savefig(plot_path, dpi=100, format='png', 
                       facecolor='white', edgecolor='none')
            self.plt.close(fig)
            plot_files[param] = plot_filename
            
        return plot_files

    def _filter_realistic_plot_values(self, series, param_name):
        """
        Filter out unrealistic sensor values for plotting based on parameter type.
        Similar to the filtering used in stress tests but adapted for plot data.
        Uses shared constants from common_defs for consistency.
        
        Args:
            series: pandas Series with numeric values
            param_name: Name of the parameter to determine filtering ranges
            
        Returns:
            pandas Series with unrealistic values set to NaN
        """
        # Define realistic ranges for different parameter types
        power_params = ['GPU Power (W)', 'Psys_Power_W']
        temp_params = ['GPU Core Temperature (Celsius Degree)', 'GPU Memory Temperature (Celsius Degree)', 'GPU_Temp_C']
        freq_params = ['GPU Frequency (MHz)', 'GT_Effective_Freq_MHz']
        
        # Apply filtering based on parameter type using shared constants
        if param_name in power_params:
            return series.where((series >= REALISTIC_POWER_MIN_W) & (series <= REALISTIC_POWER_MAX_W))
        elif param_name in temp_params:
            return series.where((series >= REALISTIC_TEMP_MIN_C) & (series <= REALISTIC_TEMP_MAX_C))
        elif param_name in freq_params:
            return series.where((series >= REALISTIC_FREQ_MIN_MHZ) & (series <= REALISTIC_FREQ_MAX_MHZ))
        else:
            # For unknown parameters, just remove zero and negative values
            return series.where(series > 0)

    def decode_pci_class(self, pci_class):
        """
        Decode PCI class code into human-readable format.
        
        Args:
            pci_class: PCI class code (int or string like "0x030000")
            
        Returns:
            dict: Dictionary with base_class, subclass, prog_interface, 
                  base_class_name, and subclass_name
        """
        
        try:
            # Convert to integer if it's a string
            if isinstance(pci_class, str):
                class_code = int(pci_class, 16)
            else:
                class_code = pci_class
            
            # Extract components: 0xCCSSPP
            base_class = (class_code >> 16) & 0xFF      # CC
            subclass = (class_code >> 8) & 0xFF         # SS  
            prog_interface = class_code & 0xFF          # PP
            
            # Get names
            base_class_info = PCI_CLASSES.get(base_class, {"name": "Unknown", "subclasses": {}})
            base_class_name = base_class_info["name"]
            subclass_name = base_class_info["subclasses"].get(subclass, "Unknown Subclass")
            
            return {
                "base_class": f"0x{base_class:02X}",
                "subclass": f"0x{subclass:02X}",
                "prog_interface": f"0x{prog_interface:02X}",
                "base_class_name": base_class_name,
                "subclass_name": subclass_name,
                "full_description": f"{base_class_name} - {subclass_name}"
            }
            
        except (ValueError, TypeError) as e:
            self.logger.error(f"Error decoding PCI class {pci_class}: {e}")
            return {
                "base_class": "Unknown",
                "subclass": "Unknown", 
                "prog_interface": "Unknown",
                "base_class_name": "Unknown",
                "subclass_name": "Unknown",
                "full_description": "Unknown PCI Class"
            }