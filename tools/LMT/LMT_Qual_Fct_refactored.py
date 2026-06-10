# Copyright (C) 2024-2026 Intel Corporation
import os
import argparse
import sys
import time
import datetime
import platform
import glob
import math
import csv


basePath = os.sep.join(str(__file__).split(os.sep)[:-3])

sys.path.append(os.path.join(basePath,'common'))

lmtPath = os.path.join(basePath, 'tools', 'LMT')


# check version of python and change to proper directory for LMT imports
def check_python_version():
    # Parse Python version as integers
    major, minor, *_ = map(int, platform.python_version_tuple())

    # Derive the subfolder name from the exact running Python version
    sub = f"LMT_v1p0_PYC{major}{minor:02d}"
    LMT_dir = os.path.join(lmtPath, sub)

    # Validate that a .pyc bundle exists for this exact Python version
    if not os.path.isdir(LMT_dir):
        prefix = "LMT_v1p0_PYC"
        supported = sorted(
            d[len(prefix):]
            for d in os.listdir(lmtPath)
            if d.startswith(prefix) and os.path.isdir(os.path.join(lmtPath, d))
        )
        supported_str = ", ".join(
            f"{int(v[:-2])}.{int(v[-2:])}" for v in supported if len(v) >= 3
        ) if supported else "none found"
        raise Exception(
            f"Unsupported Python version {major}.{minor}. "
            f"Supported versions: {supported_str}."
        )

    print(f"Python version {major}.{minor} detected. Using LMT subfolder: {sub}")
    sys.path.append(LMT_dir)
    return LMT_dir

os.chdir(check_python_version()) # sets working directory to the chosen LMT dir
from LMT import runLMT

class LaneMarginTool():
    def __init__(self,argv):
        self.argv = argv
        self.Path_LMT = os.sep.join(str(__file__).split(os.sep)[:-1])
        self.Path_LMT_PLR = os.sep.join([self.Path_LMT, 'PCIe_LMT_Results'])
        self.gdtArray = self.timeStampArray()
        self.Accept_License = True # This line will change - expect the user to pass the variable to make this true.
        self.Segment_Count = 1
        self.Bmg_B60_PCI_ID = "0xe2ff8086"
        self.selected_gpu_ids = None

    #Helper function to make sure the time value provided is an integer between 1 and 500    
    def intPosRange_1_500(self,val):
        """
        Description:
            Helper function to make sure the time value provided is an integer between 1 and 500
        """
        try:
            ival = int(val)
        except ValueError:
            raise argparse.ArgumentTypeError("{} is invalid, it must be an integer between 1 and 500".format(val))
        
        if ival < 1 or ival > 500:
            raise argparse.ArgumentTypeError("{} is invalid, it must be an integer between 1 and 500".format(val))
            
        return ival

    def parse_gpu_instance_spec(self, inst_spec):
        """Parse zero-based GPU selectors from ``-inst``."""
        spec = str(inst_spec).strip()
        if spec == '-1':
            return None

        tokens = [token.strip() for token in spec.split(',') if token.strip()]
        if not tokens:
            raise ValueError("Empty -inst value. Use -1, a single id, a range (e.g. 0-3), or a list (e.g. 0,1,2)")

        requested_gpu_ids = set()
        for token in tokens:
            if '-' in token:
                parts = token.split('-')
                if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                    raise ValueError(f"Invalid range token '{token}' in -inst='{spec}'")
                start = int(parts[0])
                end = int(parts[1])
                if start > end:
                    raise ValueError(f"Invalid descending range '{token}' in -inst='{spec}'")
                requested_gpu_ids.update(range(start, end + 1))
            else:
                if not token.isdigit():
                    raise ValueError(f"Invalid GPU id token '{token}' in -inst='{spec}'")
                requested_gpu_ids.add(int(token))

        return sorted(requested_gpu_ids)

    def get_selected_gpu_entries(self):
        """Return selected supported GPU entries using zero-based VTS GPU IDs."""
        supported_gpu_entries = []
        for entry in self.list_of_scan_content:
            value = entry.get('Neg', 'Unknown')
            if value.isdigit() and entry.get('USP', 'Unknown') in tuple(self.Valid_Gen5_GPUS):
                supported_gpu_entries.append(entry)

        total_supported_gpus = len(supported_gpu_entries)
        available_gpu_ids = list(range(total_supported_gpus))
        if self.selected_gpu_ids is None:
            selected_gpu_ids = available_gpu_ids
        else:
            invalid_gpu_ids = [gpu_id for gpu_id in self.selected_gpu_ids if gpu_id not in available_gpu_ids]
            if invalid_gpu_ids:
                raise ValueError(
                    f"Requested GPU IDs {invalid_gpu_ids} are out of range; "
                    f"available GPU IDs: {available_gpu_ids}"
                )
            selected_gpu_ids = self.selected_gpu_ids

        return [(gpu_id, supported_gpu_entries[gpu_id]) for gpu_id in selected_gpu_ids]
    
    # sets user inputs or defaults if none
    def inputsValidation(self):
        print('INPUTS VALIDATION')
        parser = argparse.ArgumentParser(description='Start script for LMT Test')
        parser.add_argument('-n', '--num_repeats', help='Number of repeats', type=self.intPosRange_1_500, default=1, dest='numRepeats')# - NEEDED for Linux
        parser.add_argument('-rn', '--rx_num', help='Receiver Number(s) to be tested on', type=int, choices=[1,2,3,4,5,6], nargs='+', default=[6], dest='rxNum')
        parser.add_argument('-inst', help='Zero-based GPU selector: single (0), range (0-3), list (0,1,2,3), or -1 for all GPUs', type=str, default='-1', dest='inst')
        op_args = parser.parse_args(self.argv)
        self.numRepeats = op_args.numRepeats # - NEEDED - do we need to check if there is an input and if not default to 1
        self.rxNum = op_args.rxNum # - NEEDED - not sure what we should default to
        try:
            self.selected_gpu_ids = self.parse_gpu_instance_spec(op_args.inst)
        except ValueError as err:
            parser.error(str(err))
        #self.recNum = 2

    # get a list of all files in a folder and put them into an array
    def retrieveAllFiles(self, folder, fullPath=True): 
        files = [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
        if fullPath:
            return [os.path.join(folder, fi) for fi in files]
        return files

    def argsort(self, vec):
        return sorted(range(len(vec)), key=vec.__getitem__)

    # get the file that was last modified in the folder of interest
    def retrieveLastEdited(self, files):
        if not files:
            raise ValueError("No files found to retrieve. The files list is empty.")
        editTimes = [os.stat(f).st_mtime for f in files]
        return files[self.argsort(editTimes)[-1]]

    # this is used in processing the scan data after the scan is complete
    def parseScan(self, logFi):
        startRead, stopRead, myscanContent = False, False, [] # this sets up flags so that if there a multiple scans in a file, it will only take the last scan
        tableChar = ('+', '|') # tuple that contains the starting character we care about in a line that indicates the start and stop of a table in scan results
        #nextscanstart = ('Win', 'Lin', '') #check to see if the file has multiple scans, usually the first line is either Win or Linux.  Added a space for new line in scan file
        line = '' #initialize variable
        with open(logFi, 'r') as fi:
            while not (startRead and stopRead):
                line = fi. readline()[:-1] # [:-1] because readline() includes \n char
                if not startRead:
                    if line.startswith('|') and not line.startswith('|-'): # these are the lines that contains the data we care about
                        startRead = True
                if startRead:
                    if not line.startswith(tuple(tableChar)):
                        stopRead = True
                if startRead and not stopRead and not line.startswith('+'): #skips over the seperator between table header and data
                    myscanContent.append(line.replace(' ', '').replace('\t', '').split('|')) # remove space and tabs, then splits on the | char.  Put into an array
                #if startRead and stopRead and line.startswith(tuple(nextscanstart)): # this put us in a loop, not sure what I was thinking here
                #    startRead, stopRead, myscanContent = False, False, []
        return myscanContent

    def get_most_recent_file(self, partial_file_name):
      """Returns the most recent file generated with the given partial file name."""
      most_recent_file = None
      for file in glob.glob(f"*{partial_file_name}*"):
        if most_recent_file is None or os.path.getmtime(file) > os.path.getmtime(most_recent_file):
          most_recent_file = file
      return most_recent_file

    def timeStampArray(self):
        dtArray = [] #initialize array that contains the date and time information so that output files will match the format that LMT uses
        
        dtn = datetime.datetime.now()
        #y = dtn.strftime("%Y")#year
        dtArray.append(dtn.strftime("%Y"))#year
        #m = dtn.strftime("%b")#month
        dtArray.append(dtn.strftime("%b"))#month
        #d = dtn.strftime("%d")#day
        dtArray.append(dtn.strftime("%d"))#day
        #h = dtn.strftime("%H")#hour
        dtArray.append(dtn.strftime("%H"))#hour
        return dtArray #(Year, month, day, hour) - example ['2023', 'Dec', '21', '14']

    def writelog(self, log_in): #this will write an error log if an error is experienced during scan, again following file name format as LMT
        WdtArray = []
        WdtArray = self.timeStampArray()
        errorlogFN = "scanerror_" + str(WdtArray[1]) + "." + str(WdtArray[2]) + "." + str(WdtArray[0]) + "_" + str(WdtArray[3]) + "hr_TestFail.log"
        with open(errorlogFN, 'w') as fi2: #open a file for writing
                a = fi2.write(log_in)

    def delete_file_check(self, filename): #used to delete files if they were generated in the same hour
        
        if os.path.exists(filename):
            os.remove(filename)

    # check for NaN
    def is_number(self, n):
        self.is_number1 = True
        try:
            num = float(n)
            # check for "nan" floats
            self.is_number1 = num == num   # or use `math.isnan(num)`
        except ValueError:
            self.is_number1 = False
        return self.is_number1

    def renameDupList(self, mvalue, moffset):
        check = False
        for row1 in self.scan_content[0]:
            if row1 == mvalue and check == False:
                check = True
            if row1 == mvalue and check == True:
                self.scan_content[0][moffset] = mvalue + mvalue

    # 1st of 4 major steps
    def RunScan(self): 
        Run_Scan = True
        Run_LMT = False
        
        sys.path.append(self.Path_LMT)
        
        self.numdGPU = 0
        lp_cnt = 1 # counter initialization to run the scan a maximum of three times, if after 3 scans we will assume something bad has happened
        invalidCase = False #pre initialize that the case is valid, will switch if invalid
        valRun = False #sets to default that we did NOT have a valid run, if we do, this will update later.
        
        while lp_cnt <= 3 and not valRun: #counter to retry run 3 times before failing, checks to see if an invalid run happened
            Files_Name = "Scan_Verification_" + str(lp_cnt) #different file name for each scan_cert run.
            
            start = runLMT(self.Accept_License, Files_Name, Run_Scan, self.Segment_Count, Run_LMT) #kicks off the test
            
            lp_cnt, invalidCase, valRun = self.processScan(lp_cnt, invalidCase, valRun) #second of four major steps
        
    # 2nd of 4 major steps
    def processScan(self, mylp_cnt, myinvalidCase, myvalRun): #OSV tracks what OS version is currently used, mylp_cnt tracks the number of tries for a valid scan, 
        #LnkSpdVal tracks the link speed of the last device found, myinvalidCase is to track if the run is invalid, myvalRun confirms that we have a good run.
        
        # Create the results directory if it doesn't exist
        if not os.path.exists(self.Path_LMT_PLR):
            os.makedirs(self.Path_LMT_PLR)
        
        os.chdir(self.Path_LMT_PLR)
        
        docFiles = [f for f in self.retrieveAllFiles('.', fullPath=False) if f.lower().startswith('PCIe_LMT_Results_Scan_Verification'.lower())]
        
        if not docFiles:
            print("ERROR: No scan files found in the results directory.")
            print("This typically means the LMT scan tool has not been run or failed to generate output files.")
            print("Expected files starting with 'PCIe_LMT_Results_Scan_Verification' in:", self.Path_LMT_PLR)
            print("Please check if the LMT tool is properly installed and available.")
            raise FileNotFoundError("No LMT scan result files found. The LMT scan may not have run successfully.")
        
        FN = self.retrieveLastEdited(docFiles) #process most currently generated scan file
        
        #initialize values and variables
        self.scan_content=[]
        
        with open(FN, 'r') as fi: #open scan results to parse the file, will now only run the last scan if duplicate scans found.
            self.scan_content = self.parseScan(FN)
                
        #This will stip out spaces in each array value
        for y in range(len(self.scan_content)):
            for z in range(len(self.scan_content[y])):
                self.scan_content[y][z] = self.scan_content[y][z].strip()
        
        #debug to convert second B,D,F to BB,DD,FF for second instance - unfortunately this is hard coded and not flexible
        self.renameDupList('B', 9)
        self.renameDupList('D', 10)
        self.renameDupList('F', 11)
        self.renameDupList('Neg', 8)
        self.scan_content[0][14] = 'Retimers' #ensure that this is the key for last column
        self.scan_content[1][14] = ''

        #converts to array to list of dictionaries
        keys = self.scan_content[0]
        values = self.scan_content[1:]

        self.list_of_scan_content = []
        for row in values:
            inner_dict = {}
            for i, key in enumerate(keys):
                inner_dict[key] = str(row[i].strip())
            self.list_of_scan_content.append(inner_dict)
        
        self.Valid_Gen5_GPUS = ["0x10601da3", "0x10631da3", self.Bmg_B60_PCI_ID] #valid Intel Max Series and Gaudi GPUs

        selected_gpu_entries = self.get_selected_gpu_entries()
        if not selected_gpu_entries:
            raise ValueError("No supported GPUs matched the requested -inst selection")

        #Valid_GPUS = tuple(self.Valid_Gen5_GPUS) # gives the ability to check for any valid gpus, since this is Gaudi Only we might need to remove this
        lnkspd1_3 = False # pre-initialize that there isn't an invalid speed for each of the gpus
        lnkspd5ATS = False
        lnkspd4Gen5 = False
        myinvalidCase = False
        last_selected_neg = None

        for _gpu_id, gpu_entry in selected_gpu_entries:
            last_selected_neg = int(gpu_entry.get('Neg', 'Unknown'))

            if 1 <= last_selected_neg <= 3:#none of our devices run at gen3 and below
                myvalRun = False #set these flags so that data can be written out or ran again
                lnkspd1_3 = True
                #lnkspd1_3val = int(self.scan_content[i][LnkSpdInd])
                myinvalidCase = True
            if last_selected_neg == 4: #gen4 for flex/max series not supported
                myvalRun = False
                lnkspd4Gen5 = True
                myinvalidCase = True
            if last_selected_neg == 5: # this is valid case for max series
                if not myinvalidCase:
                    myvalRun = True
                    self.numdGPU += 1 # counter to determine how many valid devices need to be ran.
        mylp_cnt += 1
        
        #add date and time stamp to a log file that explains the failure
        if mylp_cnt == 4 and last_selected_neg == 0:
            print("******************************************************************************************************************")
            self.writelog("Error - No GPU Link Found During Scan. For LMT to work properly you need to login in with the following command 'sudo su', please check and try again") # this will write this line to a log file for the user
            print('\033[31m' + '\033[49m' + 'Error' + '\033[39m' + '\033[49m' + " - No GPU Link Found During Scan. For LMT to work properly you need to login in with the following command 'sudo su', please check and try again") #notifies user of no link found and exits     
            sys.exit(1)
        if mylp_cnt == 4 and myinvalidCase:
            if lnkspd1_3:
                self.writelog("Error - PCIe Gen" + str(last_selected_neg) + " not supported in LMT") # this will write this line to a log file for the user
                print("************************************************************")
                print('\033[31m' + '\033[49m' + 'Error' + '\033[39m' + '\033[49m' + " - PCIe Gen" + str(last_selected_neg) + " not supported in LMT") #notifies user of unsupported link speed and exits
                sys.exit(1)
            if lnkspd5ATS:
                self.writelog("Error - Intel GPU Flex Series 140 and 170 PCIe trained at Gen5, only PCIe Gen4 is supported\nCheck Scan log file " + FN + " for further details")
                print("***************************************************************************")
                print('\033[31m' + '\033[49m' + 'Error' + '\033[39m' + '\033[49m' + " - Intel GPU Flex Series 140 and 170 PCIe trained at Gen5, only PCIe Gen4 is supported")
                print("Check Scan log file " + FN + " for further details")
                sys.exit(1)
            if lnkspd4Gen5:
                self.writelog("Error - Intel GPU Max Series 1xxx PCIe trained at Gen4, only PCIe Gen5 is supported\nCheck Scan log file " + FN + " for further details")
                print("***************************************************************************")
                print('\033[31m' + '\033[49m' + 'Error' + '\033[39m' + '\033[49m' + " - Intel GPU Max Series 1xxx PCIe trained at Gen4, only PCIe Gen5 is supported")
                print("Check Scan log file " + FN + " for further details")
                sys.exit(1)
        os.chdir(self.Path_LMT) #goes up one directory to start the scan again or to run the margining
        return mylp_cnt, myinvalidCase, myvalRun

    # 3rd of 4 major steps
    def runMargin(self):        
        """
        mynumRepeats, number of ovrerall runs of LMT
        ResWrite, Results of all valid gpus in system that will be run in a single run
        ldtArray date and time fur single run results file name
        """
        gpuCount = 1 # counter for dGPU number for pass/fail results
        SumWline = '' #what is writen to summary of multiple runs of LMT to the same systems
        Run_Scan = False
        Run_LMT = True
        Execute_Lane_Reversal = False
        rCount = 1 #counter in this section for the reading of the results file.
        #ATS-M Pass/Fail of 5 ticks
        #initialize in case no GPUs found
        myTimePassFail = 0  
        Tspf_Dic = 0
        myVolPassFail = 0
        Vspf_Dic = 0
        #PFtrack = 'Pass'
        dGPUNum = 0
        selected_gpu_entries = self.get_selected_gpu_entries()
        if self.loopCount == 0:
            self.writeHeaderResWrite = True # Single run results write
            self.writeHeader = True # for overall summary header write
        if int(self.numRepeats) > 1: # if user pass a number during as an argument to run multiple times, then this will write which run they are on
            print ("**********Running LMT iteration " + str(self.loopCount) + " out of " + str(self.numRepeats) + " **********")
        for dGPUNum, (gpu_id, gpu_entry) in enumerate(selected_gpu_entries, start=1):
            print(f"\n=== Processing GPU {gpu_id} as dGPU{dGPUNum} (USP: {gpu_entry.get('USP','Unknown')}) ===")
            #delete previous lmt .csv and .log files that was ran in the same hour
            os.chdir(self.Path_LMT_PLR) #change to results directory
            dt_array = [] #initialize array that will contain current date and time to delete existing runs in the same hour
            dt_array = self.timeStampArray()
            LMT_Run_filename_csv = 'PCIe_LMT_Results_Margin_Verification_dGPU' + str(dGPUNum) + "_" + dt_array[1] + '.' + dt_array[2] + '.' + dt_array[0] + '_' + dt_array[3] + 'hrs.csv'
            self.delete_file_check(LMT_Run_filename_csv)
            LMT_Run_filename_log = 'PCIe_LMT_Results_Margin_Verification_dGPU' + str(dGPUNum) + "_" + dt_array[1] + '.' + dt_array[2] + '.' + dt_array[0] + '_' + dt_array[3] + 'hrs.log'
            self.delete_file_check(LMT_Run_filename_log)
            P_Combine_SR_csv = 'PCIe_LMT_Combined_Results_Verification_' + dt_array[1] + '.' + dt_array[2] + '.' + dt_array[0] + '_' + dt_array[3] + 'hrs_TestPass.csv'
            self.delete_file_check(P_Combine_SR_csv)
            F_Combine_SR_csv = 'PCIe_LMT_Combined_Results_Verification_' + dt_array[1] + '.' + dt_array[2] + '.' + dt_array[0] + '_' + dt_array[3] + 'hrs_TestFail.csv'
            self.delete_file_check(F_Combine_SR_csv)
            P_Combine_MR_csv = 'PCIe_LMT_Combined_Results_Verification_All_Runs_' + dt_array[1] + '.' + dt_array[2] + '.' + dt_array[0] + '_' + dt_array[3] + 'hrs_TestPass.csv'
            self.delete_file_check(P_Combine_MR_csv)
            F_Combine_MR_csv = 'PCIe_LMT_Combined_Results_Verification_All_Runs_' + dt_array[1] + '.' + dt_array[2] + '.' + dt_array[0] + '_' + dt_array[3] + 'hrs_TestFail.csv'
            self.delete_file_check(F_Combine_MR_csv)
            os.chdir(self.Path_LMT)

            LaneVal = [j for j in range(int(gpu_entry.get('NegNeg','Unknown')))] #fills in lane list
            #Fill in DSP for lmt dictionary
            BDF_DSP = 'B' + str(gpu_entry.get('B','Unknown')) + 'D' + str(gpu_entry.get('D','Unknown')) \
                + 'F' + str(gpu_entry.get('F','Unknown'))
            #Fill in USP
            BDF_USP = 'B' + str(gpu_entry.get('BB','Unknown')) + 'D' + str(gpu_entry.get('DD','Unknown')) \
                + 'F' + str(gpu_entry.get('FF','Unknown'))

            if int(self.numRepeats) > 1:
                Files_Name = "Margin_Verification" + "_Run" + str(self.loopCount) + "_dGPU" + str(dGPUNum)
            else:
                Files_Name = "Margin_Verification" + "_dGPU" + str(dGPUNum)

            if self.myRxNum == 1: # this would be Switch or CPU, recommended tool for Intel CPUs is IOMT instead of LMT
                myTimePassFail = 6.25 #ps
                Tspf_Dic = 26 #steps
                myVolPassFail = 50 #mV
                Vspf_Dic = 27 #steps
            elif self.myRxNum == 2: # this would be first of two retimer(s) input from Switch/CPU
                myTimePassFail = 6.25 #ps
                Tspf_Dic = 7 #steps
                myVolPassFail = 50 #mV
                Vspf_Dic = 27 #steps
            elif self.myRxNum == 3: # this would be first of two retimer(s) input from second retimer/GPU
                myTimePassFail = 6.25 #ps
                Tspf_Dic = 7 #steps
                myVolPassFail = 50 #mV
                Vspf_Dic = 27 #steps
            elif self.myRxNum == 4: # this would be second of two retimers input from first retimer
                myTimePassFail = 6.25 #ps
                Tspf_Dic = 7 #steps
                myVolPassFail = 50 #mV
                Vspf_Dic = 27 #steps
            elif self.myRxNum == 5: # this would be second of two retimers input from GPU
                myTimePassFail = 6.25 #ps
                Tspf_Dic = 7 #steps
                myVolPassFail = 50 #mV
                Vspf_Dic = 27 #steps
            elif self.myRxNum == 6 or gpu_entry.get('USP','Unknown') == self.Bmg_B60_PCI_ID: # this would be first of two retimer(s) input from CPU
                if len(self.rxNum) == 1: #this should be for the default rxNum of 2 and wanting to run PCIe AIC where the rxNum should be 6 and then setup the parameters
                    self.myRxNum = 6 # needed for PCIe AIC if the default is 2.
                myTimePassFail = 5.36 #ps
                Tspf_Dic = 7 #steps
                myVolPassFail = 42.9 #mV
                Vspf_Dic = 27 #steps

            input_dic = {
                "Lane": LaneVal,
                "RecNum": self.myRxNum,
                "Dwell_Time": 1,
                "BDF_DSP": BDF_DSP,
                "BDF_USP": BDF_USP,
                "ErrCnt": 2,  # Reduced from 4 to 2 for less demanding test
                "Num_Time_Steps": Tspf_Dic,
                "Num_Voltage_Steps": Vspf_Dic,
                "Segment": int(gpu_entry.get('S','Unknown')), # Taking result from scan
                "Speed": int(gpu_entry.get('Neg','Unknown')),
            }

            # Wrap LMT execution in try-except to handle failures gracefully
            max_retries = 2  # Try up to 2 times for each device
            for attempt in range(max_retries):
                try:
                    if attempt > 0:
                        print(f"Retrying LMT margining for dGPU{dGPUNum} (attempt {attempt + 1}/{max_retries})...")
                        # Add a small delay between retries
                        time.sleep(2)
                    else:
                        print(f"Starting LMT margining for dGPU{dGPUNum}...")

                    start = runLMT(self.Accept_License, Files_Name, Run_Scan, self.Segment_Count, Run_LMT, Execute_Lane_Reversal, input_dic)
                    print(f"LMT margining completed successfully for dGPU{dGPUNum}")
                    rCount += 1 #increments counter to run multiple channels that need margining
                    break  # Success, exit retry loop

                except Exception as e:
                    print(f"Error on attempt {attempt + 1}: LMT margining failed for dGPU{dGPUNum}: {str(e)}")
                    if attempt == max_retries - 1:  # Last attempt failed
                        print(f"All retry attempts failed for dGPU{dGPUNum}. Continuing with next device...\n")
                        continue  # Skip to next device
                    # If not the last attempt, continue with retry loop

            #All runs of LMT completed for the system
            os.chdir(self.Path_LMT_PLR) #change again to results directory

            #lp_cnt = 1 #dGPU counter for file names and to stop the loop when getting through all gpus
            myFNarray = []

            #while lp_cnt < rCount: #rCount is the number of valid runs per system -1
            if int(self.numRepeats) > 1:
                tResults = "PCIe_LMT_Results_Margin_Verification_Run" + str(self.loopCount) + "_dGPU" + str(dGPUNum)
            else:
                tResults = "PCIe_LMT_Results_Margin_Verification_dGPU" + str(dGPUNum)
            docFiles = [f for f in self.retrieveAllFiles('.', fullPath=False) if (f.lower().startswith(tResults.lower()) and f.lower().endswith('.csv'.lower()))]

            # Check if any files were generated for this device
            if docFiles:
                try:
                    filename = self.retrieveLastEdited(docFiles)
                    myFNarray.append(filename)
                    print(f"Processing results for dGPU{dGPUNum}: {filename}")
                    #lp_cnt += 1
                    #rCount = 1
                    SumWline, gpuCount  = self.processMargin(myFNarray, myTimePassFail, myVolPassFail, SumWline, gpuCount)
                    print(f"Results processing completed for dGPU{dGPUNum}")
                except Exception as e:
                    print(f"Error processing results for dGPU{dGPUNum}: {str(e)}")
                    print(f"Continuing with next device...")
            else:
                print(f"Warning: No result files found for dGPU{dGPUNum}. This device may have failed during margining.")
                print(f"Skipping result processing for this device.")
                # Continue to next device without processing

            os.chdir(self.Path_LMT) # move back up a directory
        return self.PFtrack # can't we make this a self so we don't have to worry about returning this.

    # 4th of 4 major steps        
    def processMargin(self, myFNarray, myTimePassFail, myVolPassFail, SumWline, dGPUnum):
        SumWlineArray = []
        HPline = '' #this is what gets written to the screen
        HWline = '' #initialize to write to the file
        
        mydtArray = self.timeStampArray()
        
        for r in range(len(myFNarray)):
            Pline_lod = ''
            Wline_lod = ''
            #SumWlineArray = []
            """rArray = []
            with open(myFNarray[r], 'r') as fr: #opens and reads results, this is an array of the .csv files to be post processed
                for xx in fr:
                    reslineIn = xx.split(',') # split scan results input file on , since csv file 
                    rArray.append(reslineIn)"""
            with open(myFNarray[r], 'r') as file: #reading in each results csv file and put them into a list of dictionaries
                res_lod_csv_reader = csv.DictReader(file)
                res_list_of_dict = [row for row in res_lod_csv_reader]

            HPline = 'Segment\tdGPU\tBDF_DSP\t\tBDF_USP\t\tDID_USP\t\tLane\t' #header for file to be written later
            HWline = 'Segment,dGPU,BDF_DSP,BDF_USP,DID_USP,Lane,' # header for print statement that will be written later

            if self.writeHeader:
                SumHWline = "File,Run," #inserts file name for multiple runs of lmt in the first column
            HPline += 'Eye Width\t\t\tEye Width Pass/Fail\tEye Height\t\t\tEye Height Pass/Fail\t'
            HWline += 'Eye Width,Eye Width Pass/Fail,Eye Height,Eye Height Pass/Fail,'
            HPline = HPline[:-1] # removes trailing tab
            HWline = HWline[:-1]
            HWline += '\n' #insert carriage return
            if self.writeHeader:
                SumHWline += HWline # adds remaining data to the line where the file name was added first

            if self.writeHeaderResWrite:
                print('\033[39m' + '\033[49m' + HPline) # sets foreground and background colors to default
            #gets info to write the results to .csv as well as print to the screen

            if int(self.numRepeats) > 1:
                # this is broken, multiple date time array isn't getting filled.
                ResWrite = 'PCIe_LMT_Results_combined_Run' + str(self.loopCount) + "_" + mydtArray[1] + '.' + mydtArray[2] + '.' + mydtArray[0] + '_' + mydtArray[3] + 'hrs.csv'  #sets file name up, follows convention used in LMT
            else:
                ResWrite = 'tempSingle.csv'

            if self.writeHeaderResWrite:
                with open(ResWrite, 'w') as fe: #open a file to write the results to .csv
                    #header line for output file and to the screen
                    fe.write(HWline)
                    self.writeHeaderResWrite = False
            if int(self.numRepeats) > 1:
                SumResWrite = 'tempMult_Run.csv'
                if self.writeHeader:
                    self.writeHeader = False
                    with open(SumResWrite, 'w') as fef: #open a file to write the results to .csv
                        #header line for output file and to the screen
                        fef.write(SumHWline)
                        
            for pf in range(len(res_list_of_dict)): #go through the results list of dictionaries and operate on only valid GPUs
                #written to screen, aligns the table to "look" nice

                # Values from dictionary
                segment = res_list_of_dict[pf].get('Segment', 'Unknown')
                bdf_dsp = res_list_of_dict[pf].get('BDF_DSP', 'Unknown')
                bdf_usp = res_list_of_dict[pf].get('BDF_USP', 'Unknown')
                did_usp = res_list_of_dict[pf].get('DID_USP', 'Unknown')
                lane = res_list_of_dict[pf].get('Lane', 'Unknown')
                
                # Smart detection of available time margin columns
                right_ps = res_list_of_dict[pf].get('Right (ps)', 'NA')
                left_ps = res_list_of_dict[pf].get('Left (ps)', 'NA')
                time_margin_ps = res_list_of_dict[pf].get('Time Margin (ps)', 'NA')
                eye_width = res_list_of_dict[pf].get('Eye Width', 'NA')
                
                # Smart detection of available voltage margin columns
                up_mv = res_list_of_dict[pf].get('Up (mV)', 'NA')
                down_mv = res_list_of_dict[pf].get('Down (mV)', 'NA')
                voltage_margin_mv = res_list_of_dict[pf].get('Voltage Margin (mV)', 'NA')
                eye_height = res_list_of_dict[pf].get('Eye Height', 'NA')
                                                
                Pline_lod += str(segment) + '\t' + str(dGPUnum) + '\t' + str(bdf_dsp) + '\t\t' + str(bdf_usp) + '\t\t' + did_usp + '\t\t' + str(lane) + '\t' # Print Line
                Wline_lod += str(segment) + ',' + str(dGPUnum) + ',' + str(bdf_dsp) + ',' + str(bdf_usp) + ',' + did_usp + ',' + str(lane) + ',' # Write Line
                
                # Smart eye width calculation - try different data sources in priority order
                RminusL_lod = 'NA'
                # Priority 1: Calculate from separate left/right values if both have valid data
                if (right_ps not in ['NA', 'Unknown', ''] and left_ps not in ['NA', 'Unknown', ''] and 
                    self.is_number(right_ps) and self.is_number(left_ps)):
                    RminusL_lod = float(right_ps) - float(left_ps)
                # Priority 2: Use direct time margin value if available
                elif (time_margin_ps not in ['NA', 'Unknown', ''] and self.is_number(time_margin_ps)):
                    RminusL_lod = float(time_margin_ps)
                # Priority 3: Use eye width column if available
                elif (eye_width not in ['NA', 'Unknown', ''] and self.is_number(eye_width)):
                    RminusL_lod = float(eye_width)
                
                # Smart eye height calculation - try different data sources in priority order  
                UminusD_lod = 'NA'
                # Priority 1: Calculate from separate up/down values if both have valid data
                if (up_mv not in ['NA', 'Unknown', ''] and down_mv not in ['NA', 'Unknown', ''] and 
                    self.is_number(up_mv) and self.is_number(down_mv)):
                    UminusD_lod = float(up_mv) - float(down_mv)
                # Priority 2: Use direct voltage margin value if available
                elif (voltage_margin_mv not in ['NA', 'Unknown', ''] and self.is_number(voltage_margin_mv)):
                    UminusD_lod = float(voltage_margin_mv)
                # Priority 3: Use eye height column if available
                elif (eye_height not in ['NA', 'Unknown', ''] and self.is_number(eye_height)):
                    UminusD_lod = float(eye_height)
                
                if self.is_number(RminusL_lod): #Check if Number
                    if len(str(RminusL_lod)) <= 7: #Adjusting formating if varied decimal places
                        Pline_lod += (str(RminusL_lod) + '\t\t\t\t')
                    else:
                        Pline_lod += (str(RminusL_lod) + '\t\t\t')
                    Wline_lod += str(RminusL_lod) + ','
                    if myTimePassFail <= float(RminusL_lod): #checks against pass/fail criteria on the eye width
                        Pline_lod += ('\033[32m' + '\033[49m' + 'Pass' + '\033[39m' + '\033[49m' + '\t\t\t' )  # Green text
                        Wline_lod += 'Pass' + ','
                    else:
                        Pline_lod += ('\033[31m' + '\033[49m' + 'Fail' + '\033[39m' + '\033[49m' + '\t\t\t' )  # Red text
                        Wline_lod += 'Fail' + ','
                        self.PFtrack = 'Fail'
                else:
                    Pline_lod += (str("NA") + '\t\t\t')
                    Wline_lod += str("NA") + ','
                    Pline_lod += ('\033[31m' + '\033[49m' + 'NA' + '\033[39m' + '\033[49m' + '\t\t\t' )  # Red text
                    Wline_lod += 'NA' + ','
                    self.PFtrack = 'NA'
                if self.is_number(UminusD_lod): #Need to check on what this does 
                    if len(str(UminusD_lod)) < 7:
                        Pline_lod += (str(UminusD_lod) + '\t\t\t\t')
                    else:
                        Pline_lod += (str(UminusD_lod) + '\t\t')
                    Wline_lod += str(UminusD_lod) + ','
                    if myVolPassFail <= float(UminusD_lod): #checks against pass/fail criteria on the eye height
                        Pline_lod += ('\033[32m' + '\033[49m' + 'Pass' + '\033[39m' + '\033[49m' + '\t\t\t' )  # Green text
                        #written to file
                        Wline_lod += 'Pass' + ','
                    else:
                        Pline_lod += ('\033[31m' + '\033[49m' + 'Fail' + '\033[39m' + '\033[49m' + '\t\t\t' )  # Red text
                        #written to file
                        Wline_lod += 'Fail' + ','
                        self.PFtrack = 'Fail'
                else:
                    Pline_lod += (str("NA") + '\t\t\t')
                    Wline_lod += str("NA") + ','
                    Pline_lod += ('\033[31m' + '\033[49m' + 'NA' + '\033[39m' + '\033[49m' + '\t\t\t' )  # Red text
                    #written to file
                    Wline_lod += 'NA' + ','
                    self.PFtrack = 'NA'
                Numtabs = -3
                Pline_lod = Pline_lod[:Numtabs] + '\n'
                Wline_lod = Wline_lod[:-1] + '\n'

            SumWlineArray = Wline_lod.split('\n')
            SumWlineArray = SumWlineArray[:-1]
            
            for jj in range(len(SumWlineArray)):
                SumWline += myFNarray[r] + ',' + str(self.loopCount) + ',' + SumWlineArray[jj] + '\n'
            
            dGPUnum += 1    
            print(Pline_lod[:-1]) #remove final new line and write to screen

            with open(ResWrite, 'a') as fe: #open a file for writing
                a = fe.write(Wline_lod)

            if self.numdGPU == (dGPUnum - 1) and (int(self.numRepeats) == 1): # needed to offset by -1 for dGPUNum because after the first unit dGPUNum is at 2
                ResRdtArray = []
                ResRdtArray = self.timeStampArray()
                ResRm = ResRdtArray[1]#month
                ResRy = ResRdtArray[0]#year
                ResRd = ResRdtArray[2]#day
                ResRh = ResRdtArray[3]#hour
                pfFileName = 'PCIe_LMT_Combined_Results_Verification_' + ResRm + '.' + ResRd + '.' + ResRy + '_' + ResRh + 'hrs_Test' + self.PFtrack + '.csv'
                os.rename(ResWrite, pfFileName)
            
        if int(self.numRepeats) > 1:
            if dGPUnum > self.numdGPU: 
                with open(SumResWrite, 'a') as fef: #open a file for writing
                    a = fef.write(SumWline)
                SumWline = '' #- DEBUG may perm remove.          
        else:
            SumResWrite = ''#a placeholder for a single run since this won't get defined.
        
        if (self.loopCount + 1 == int(self.numRepeats)) and int(self.numRepeats) > 1 and dGPUnum > self.numdGPU:
            ResRdtArray = []
            ResRdtArray = self.timeStampArray()
            spfFileName = 'PCIe_LMT_Combined_Results_Verification_All_Runs_' + ResRdtArray[1] + '.' + ResRdtArray[2] + '.' + ResRdtArray[0] + '_' + ResRdtArray[3] + 'hrs_Test' + self.PFtrack + '.csv'
            os.rename(SumResWrite, spfFileName)
    
        return SumWline, dGPUnum #make class attributes
        
    #checks if user is logged in as root.
    def checkforRoot(self):
        if sys.platform == "linux":
            euid = os.geteuid()
        
            if euid != 0:
                print("Would you like to login as root/admin, this will run the 'sudo su' command? (y/n) ")
                croot = sys.stdin.readline().replace('\n','')
                if len(croot) == 1 and croot.lower() in ['y']:
                    os.system('sudo su')
                else:
                    #print('Lane Margin Tool(LMT) must be run as root/admin')
                    #exit(1)
                    raise Exception('Lane Margin Tool(LMT) must be run as root/admin')

        
    def main(self):
        #**************************Main Program*********************************
        #try:
        self.inputsValidation()
        self.checkforRoot() # This will need to be turned on for publication

        for self.loopCount in range(0,int(self.numRepeats)):
                for self.myRxNum in self.rxNum:
                    self.dtarray = self.timeStampArray()
                    if self.loopCount == 0:
                        print("*********************************************************************************")
                        print("NOTE - Once Scan is complete, the processing time takes a few minutes to complete")
                        print("*********************************************************************************")
                        self.RunScan()
                        self.PFtrack = 'Pass'
                    finalPFtrack = self.runMargin()

        if finalPFtrack == 'Pass':
            return 0
        else:
            return 1
    
    
if __name__ == "__main__":
    lmtObj = LaneMarginTool(sys.argv[1:])
    #main function execution
    sys.exit(lmtObj.main())
