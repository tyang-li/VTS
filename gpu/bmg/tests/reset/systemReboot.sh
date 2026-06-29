#!/bin/bash
export LC_ALL=en_US.UTF-8

###############################################
scriptVersion="Reset Cycle Script Version 1.60"
###############################################

#Changes in 0.95:
#Added a PCIe device BAR check.
#Added CLPeak workload test for PVC.

#Changes in 0.96:
#Rewrote workload tests to run in parallel to cut down execution time.
#Changes SBR test to run in serial to prevent device dropouts seen on some systems.

#Changes in 0.97:
#Added a catch for lost devices
#Changed reboot service to restart on a service failure.
#Added filesystem sync to the cold and custom reset flows.
#Added XPUM service disable for SBR and FLR.

#Changes in .98:
#Added script version to reset log.
#Modified config file parsing to anchor queries to first word.

#Changes in .99:
#Added a dynamic timeout for SBR to allow more time for PVC to reset.
#Made device unbind for FLR and SBR run in parallel to reduce test time.
#Changed FLR to run in parallel.
#Script now supports OAM.
#Added adjustable timeout variables for SBR and FLR tests.
#Added a watchdog timer for parallel processes.
#Changed script flow to run main in a loop and return to main instead of recursively calling main.
#Added support to recognize systems with multiple PCI domains.
#Added a visible sleep counter for FLR and SBR timeouts.
#Modified media transcode test to convert h264 file to h265.

#Changes in 1.0:
#Added logged message to collect debug info after a test failure.

#Changes in 1.1:
#Added system BIOS, baseboard SN and version, and product name to log files to assist with debug and identification.
#Fixed issue with watcdog function only watching a single PID.

#Changes in 1.2:
#Added 170C support.
#Added a driver bind timeout to keep enumeration order consistent.
#Added a runlevel check to ensure system is in RL3 for FLR and SBR.
#Added check for sample_multi_transcode

#Changes in 1.3
#Changed test workloads from clpeak and media transcode to ze_peak sp_compute.
#Added Gaudi support
#Added FLR and SBR recovery methoed if test fails.
#Added feature to resume test even if devices are in a bad state during start or restart.
#Remove FLR countdown start timer.
#Countdowns now update in-place.
#Added testonly option to check the system config without running any resets.
#Added PCIe link speed change test.
#Added PCIe link disable and enable test.
#Added "cleaned" to cleaned up test run log files.
#Added support for custom option to call Python scripts.
 
#Changes in 1.4
#Added new PCIe link retrain test.
#Converted command substitution from backticks to modern $()

#Changes in 1.5
#Added support for additional device IDs.
#Added catch for specifying cycles with testOnly option.
#Unbinding and re-binding habanalabs module devs instead of stopping driver.
#Added a semi-serial SBR function.
#Added device driver cycle test
#suppressed cycle count remaining message before linkretrain test.
#Added a device driver bind wait loop to give time for devices to enumerate.

#Changes in 1.55
#Added additional dynamic retries to allow for device enumeration and resets.
#Added Bus rescan options for node removal.
#Added driverless mode for SBR and link disable tests.
#Added multi-segment or domain support to linkretrain test.
#Added hotplug check and disable for SBR test.

#Changes in 1.56
#Changed reboot service name to rebootTest.service

#Changes in 1.60
#Removed PVC tests and references.
#Reduced global variables.
#Changed wachdog function to return a 0 or 1 instead of setting a variable.
#Cleaned up exit codes. All failures now return a 1 on exit.
#Removed case sensetivity from some command line options.
#Invalid command line now gives an exit code.
#Updated log path to match new log folder name.
#Code refactor and cleanup.
#Modified device driver bind behavior with extra check: bind > check/break > wait > check/break > retry.
#Added device unbind for additional endpoints behind a bridge.
#Added soft power cycle option for S5 cycle testing with graceful shutdown.
#Added timestamps to log file entries
#Made driver binding and unbinding methods uniform.

readonly _scriptName="${0##*/}"
numCycles=$1
_testType=$(tr '[:upper:]' '[:lower:]' <<< $2)

if [[ -n $3 ]]; then
		_extScriptName=$3
fi
if [[ $numCycles = "testonly" ]] || [[ $_testType = "testonly" ]]; then
		numCycles=0
		_testType="testonly"
fi

_watchdogTimeout=300 #Increase to allow more time for tasks to complete.
_launchPath=$(pwd)"/"
_realPath=$(realpath "$0")
_realPath="${_realPath%/*}/"
_configFileName="configTemp"
_assets=content #Folder name for tests and files.
_configFileNamePath=$_realPath$_configFileName
_logFileName=${_realPath}"${_testType}_Reset"
_wrapperLogPath=${_launchPath}"logs/"
_altLogName=${_wrapperLogPath}"${_testType}Reset"
_serviceName=rebootTest.service #systemd service name for reboot script
_systemdPath=/etc/systemd/system/
_scriptRunningPath=/usr/local/sbin/
_minLinkGen=1 #Minimum link gen to downgrade to in speed change test.
#Below are debug settings. Do not change without instruction.
_recovery=0
_DONOTFAIL=0 #If set to 1, disables link recovery fail call.
readonly _SBRMode=2 #SBR node removal: 0 = No removal; 1 = Serial; 2 = Parallel.
readonly _fullBusRescan=0 #Rescan type: 0 = Targeted node; 1 = Full bus.
_FLRUnbind=1 #Driver handling: 0 = start/stop; 1 = unbind/bind.
_SBRDriverOff=0 #Test driver after SBR: 0 = yes; 1 = no.
_FLRtimeout=3 #increase this value if FLR fails.
_SBRtimeout=3 #increase this value if SBR fails.
_SBRrescanTimeout=1
_bindTimeout=.5
_retrainDelay=100 #Delay for link retrain test.
_powerOnDelay=60 #Adjust this time based on how long the OS takes to shut down plus ten seconds.

function findDevices() {
	#Add device endpoint IDs here.
	local ATS="56c0 56c1 56c2"
	local Gaudi="1020 1060 1061 1063"
	local BMG="E211 E221 E223"
	local targetDevices="$BMG $ATS $Gaudi"

	for i in $targetDevices; do
		if [[ $(lspci -d:$i) ]]; then
			_deviceSearchString=$i
			printf "Device ID $_deviceSearchString found.\n"
			break
		fi
	done
	#To add support for a new device, add an elif below with the following variables:
		#elif [[ $_deviceSearchString = "The endpoint device ID to test" ]]; then #Template config.
		#	_USPDID="Device bridge or endpoint connecting to the host."
		#	_USPLevel="Integer indicating number of levels from the upstream host bridge to the endpoint"
		#	_linkSpeed="Device bridge link to host expected link speed, such as 16GT/s.
		#	_linkWidth="Device host bridge link expected PCIe link width, such as x4, x8, or x16"
		#	_barSize0="Bar 0 size seen in lspci, such as 16M"
		#	_barSize2="Bar 2 size seen in lspci, such as 8G"
		#	_barSize4="Bar 4 size seen in lspci, such as 256G. Leave blank if none."
		#	_devDriver="Device driver module array for modules to check or manipulate, such as ( i915 drm ). Put main driver first."
		#	_otherDriver="Driver modules for other functions on a device."
		#	_otherUnbindDevice="Other endpoint device function ID on a PCIe device."
		#	_openCLDev="y if device supports openCL. This enables compatible tests."
		#fi
#TODO: Need to fully implement driver array support for stop and start.
	if [[ $numCycles != "clean" ]]; then
		if [[ $_deviceSearchString = "E211" ]] ||
			[[ $_deviceSearchString = "E221" ]]; then #BMG B60/B60 IBC
			_USPDID="E2FF"
			_USPLevel=4
			_linkSpeed="32GT/s"
			_linkWidth="x8"
			_barSize0="16M"
			_barSize2="32G"
			_devDriver=( xe )
			_otherDriver=( snd_hda_intel )
			_otherUnbindDevice="e2f7"
			_openCLDev="y"
		elif [[ $_deviceSearchString = "E223" ]]; then #BMG B70 IBC
			_USPDID="E2FF"
			_USPLevel=4
			_linkSpeed="32GT/s"
			_linkWidth="x16"
			_barSize0="16M"
			_barSize2="32G"
			_devDriver=( xe )
			_otherDriver=( snd_hda_intel )
			_otherUnbindDevice="e2f7"
			_openCLDev="y"
		elif [[ $_deviceSearchString = "56c1" ]]; then #ATS-140
			_USPDID="4fa1"
			_USPLevel=4
			_linkSpeed="16GT/s"
			_linkWidth="x8"
			_barSize0="16M"
			_barSize2="8G"
			_devDriver=( i915 )
			_openCLDev="y"
		elif [[ $_deviceSearchString = "56c0" ]] ||
			[[ $_deviceSearchString = "56c2" ]]; then #ATS-170
			_USPDID="4fa0"
			_USPLevel=4
			_linkSpeed="16GT/s"
			_linkWidth="x16"
			_barSize0="16M"
			_barSize2="16G"
			_devDriver=( i915 )
			_openCLDev="y"
		elif [[ $_deviceSearchString = "1020" ]]; then #Gaudi 2
			_USPDID="1020"
			_USPLevel=2
			_linkSpeed="16GT/s"
			_linkWidth="x16"
			_barSize0="256M"
			_barSize2="16K"
			_barSize4="128G"
			_openCLDev="n"
			_devDriver=( habanalabs habanalabs_cn habanalabs_ib habanalabs_en habanalabs_compat )
		elif [[ $_deviceSearchString = "1060" ]]; then #Gaudi 3
			_USPDID="1060"
			_USPLevel=2
			_linkSpeed="32GT/s"
			_linkWidth="x16"
			_barSize0="256M"
			_barSize2="32K"
			_barSize4="128G"
			_openCLDev="n"
			_devDriver=( habanalabs habanalabs_cn habanalabs_ib habanalabs_en habanalabs_compat )
		elif [[ $_deviceSearchString = "1061" ]]; then #Gaudi 3 PCIe
			_USPDID="1061"
			_USPLevel=2
			_linkSpeed="32GT/s"
			_linkWidth="x16"
			_barSize0="256M"
			_barSize2="32K"
			_barSize4="128G"
			_openCLDev="n"
			_devDriver=( habanalabs habanalabs_cn habanalabs_ib habanalabs_en habanalabs_compat )
		elif [[ $_deviceSearchString = "1063" ]]; then #Gaudi 3 PCIe
			_USPDID="1063"
			_USPLevel=2
			_linkSpeed="32GT/s"
			_linkWidth="x16"
			_barSize0="256M"
			_barSize2="32K"
			_barSize4="128G"
			_openCLDev="n"
			_devDriver=( habanalabs habanalabs_cn habanalabs_ib habanalabs_en habanalabs_compat )
		else
			printf "****************************************************\n"
			printf "Error: Supported Target Device ID not found.\n"
			printf "Check with your Intel representative for assistance.\n"
			printf "****************************************************\n"
			exit 1
		fi
		checkRunlevel
	fi
}


function healthCheck() {
	logv "Starting health checks."
	_PCIeCheck "$_USPort"
	[[ $_USPLevel > 2 ]] && _PCIeCheck "$_EPBDFList" 2

	if [[ $_SBRDriverOff -eq 1 ]] && [[ $_testType = "sbr" ]] ||
	[[ $_testType = "linkdisable" ]] && [[ $_SBRDriverOff -eq 1 ]]; then
		logv "#################################"
		logv "Driverless Mode selected."
		logv "Skipping software and BAR checks."
		logv "Driver will be disabled."
		logv "#################################"
	else
		barCheck
		[[ $_openCLDev = "y" ]] && clinfoCheck
		[[ $_openCLDev = "y" ]] && ZEPeakTest
		#Tests for Gaudi
		[[ $_devDriver = "habanalabs" ]] && hlsmiCheck
		[[ $_devDriver = "habanalabs" ]] && checkGaudiStatus
		[[ $_devDriver = "habanalabs" ]] && gaudiPortHealthCheck
	fi
}


function checkRunlevel() {
	if [[ $_FLRUnbind -eq 1 ]] && [[ $_testType = "flr" || $_testType = "sbr" ||
		$_testType = "linkdisable" || $_testType = "linkchange" || $_testType = "retrain" ]] ; then
		if systemctl list-units --type target --state active | grep Graphical > /dev/null; then
			printf "#########################################################\n"
			printf "To run this test, disable the graphical.target run target.\n"
			printf "To do this, use \"systemctl isolate multi-user.target\" and\n"
			printf "re-launch this test.\n"
			printf "To make this target permanent, use the command:\n"
			printf "		systemctl set-default multi-user.target\n"
			printf "Press enter to exit\n"
			printf "#########################################################\n"
			read
			exit 1
		else
			printf "Graphical run target not detected. Continuing.\n"
		fi
	fi
}


function cleanUpOld() {
#This can be invoked by using the "clean" flag to wipe out any old configs.
	parseConfigFile
	#TODO: Determine if this can be removed or re-write the recovery function.
	#[[ $_testType = "linkchange" ]] && recoverLinkSpeed
	[[ -n $_serviceName ]] && [[ ! "systemctl status $_serviceName" ]] && systemctl disable $_serviceName
	[[ -n $_systemdPath ]] && [[ -n $_serviceName ]] && rm -f $_systemdPath$_serviceName
	[[ -n $_scriptRunningPath ]] && [[ -n $_scriptName ]] && rm -f $_scriptRunningPath$_scriptName
	[[ -n $_scriptRunningPath ]] && [[ -n $_configFileName ]] && rm -f ${_scriptRunningPath}$_configFileName
	[[ -n $_configFileNamePath ]] && rm -f $_configFileNamePath
	if [[ -e $_logFileName ]]; then
		local newLogName=${_logFileName}-$(date "+%Y-%m-%d_%H%M%S")"_cleaned".log
		cp $_logFileName $newLogName
		rm -f $_logFileName
		printf "Log file saved as $newLogName\n"
	fi
	printf "Old configuration cleaned up.\n"
	exit 0
}


function exitHelp() {
	printf "
This script requires one to three parameters, depending on the selected option.
For tests, the parameters are ./$_scriptName <cycles> <test> <optional script>
Cycle quantity must be an integer less than 99999 and greater than 0.
Other options do not take a cycle parameter: ./$_scriptName <option>

Tests: warm, cold, soft, flr, sbr, linkdisable, linkchange, retrain, drivercycle, custom

	warm - Starts system-level warm reset cycles
	
	cold - Starts system-level hard S5 power reset cycles using host BMC
	
	soft - Starts system-level soft S5 power reset cycles using host OS
	
	flr - Starts device function-level reset cycles
	
	sbr - Starts device secondary bus reset cycles
	
	linkdisable - Starts device link disable and enable cycles
	
	linkchange - Cycles devices through PCIe speed changes from gen2 to max.
	
	retrain - Retrains target PCIe device links.
	
	drivercycle - Stops and Starts the device driver.

	Ex: ./$_scriptName 100 warm - runs 100 warm reset cycles.

	custom - Waits for an external reset source, such as a timed AC (G3)
	cycle, or manual reboot, etc. If using custom, a third parameter may be used
	to specify a custom shell or python script to launch. Custom scripts can be written
	to control a remote PDU or other reset hardware or system functions.
	Put custom scripts in the $_realPath directory.
	
	Ex: ./$_scriptName 100 custom pducontrolscript.sh
		
Other options: clean, testonly
	
	clean: Tells the script to clean up any existing test configuration from 
	a previous incomplete run and generate the final logs. It also attempts
	to restore the system to a good working state. An incomplete run is one
	where the user terminates a test early.
	
	testonly: Runs the reset functional test flow one time without starting
	any reset test.

	Ex: ./$_scriptName clean\n
"
	exit 1
}


function prereqCheck() {
	if [[ $_testType = "cold" ]]; then
		if type ipmitool>/dev/null 2>&1; then
			echo "IPMITool found."
		else
			printf "**************************\n"
			printf "Error: IPMITool not found.\n"
			printf "**************************\n"
			printf "Cold reset test requires IPMITool. Please install and retry.\n"
			exit 1
		fi
	fi
					
	if type sha384sum>/dev/null 2>&1; then
		printf "sha384sum found.\n"
	else
		printf "************************\n"
		printf "Error: sha384sum not found.\n"
		printf "************************\n"
		printf "Script requires sha384sum. Please install and re-run.\n"
		exit 1
	fi
		
	if [[ $_testType = "retrain" ]]; then
		if $(ldconfig -v 2>/dev/null | grep libpciaccess.so > /dev/null); then
			printf "libpciaccess.so found.\n"
		else
			printf "******************************************************\n"
			printf "Error:  libpciaccess.so not found.\n"
			printf "******************************************************\n"
			printf "Please install libpciaccess-dev or libpciaccess-devel.\n"
			exit 1
		fi
	fi
}


function generateConfig() {
	#Check if user wants to clean up the old config, else confirm if config exists or create a new config file.
	if [[ $numCycles = "clean" ]]; then
		cleanUpOld
		exit 0
	elif [[ -f $_configFileNamePath ]]; then
		_logFileName="$(grep ^logName "$_configFileNamePath" | cut -d ' ' -f 2)"
#TODO: Make this a list with a loop to improve maintainability.
	elif [[ $_testType = "testonly" ]] || ([[ $numCycles -gt 0 && $numCycles -lt 99999 ]] &&
		[[ $_testType = "warm" || $_testType = "cold" || $_testType = "flr" || $_testType = "sbr" || 
		$_testType = "linkdisable" || $_testType = "linkchange" || $_testType = "custom"  ||
		$_testType = "retrain" || $_testType = "drivercycle" ]] || [[ $_testType = "soft" ]]); then
		findDevices

		prereqCheck
			
		if touch $_configFileNamePath; then
			echo "resetsLeft_ $numCycles" > $_configFileNamePath
			echo "testType_ $_testType" >> $_configFileNamePath
			echo "logName_ $_logFileName" >> $_configFileNamePath
			echo "wrapperLogPath_ $_wrapperLogPath" >> $_configFileNamePath
			echo "altLogName_ $_altLogName" >> $_configFileNamePath
			echo "launchPath_ $_launchPath" >> $_configFileNamePath
			resetLogInit #This generates the log file before required before logv function can be used.
			echo "pathOrig_ $_realPath" >> $_configFileNamePath
			echo "testTarget_ $_deviceSearchString" >> $_configFileNamePath
		
	#Capture endpoint BDFs and link status.		
			_EPBDFList=$(generateBDFList $_deviceSearchString)
			echo "endPoints_ $_EPBDFList" >> $_configFileNamePath
			saveLinkStatus "$_EPBDFList"
			_numDevs=$(echo $_EPBDFList | wc -w)
			echo "numDevs_ $_numDevs" >> $_configFileNamePath
			logv "Devices found = $_numDevs"
			if [[ -n $_otherUnbindDevice ]]; then
				_otherBDFs=$(generateBDFList $_otherUnbindDevice)
			fi
			echo "otherBDFs_ $_otherBDFs" >> $_configFileNamePath
	#Capture USP BDFs and link status.		
			USPBDF=$(generateBDFList "$_USPDID")
			echo "USP_ $USPBDF" >> $_configFileNamePath
			saveLinkStatus "$USPBDF"
			_parentList=$(findParents "$_EPBDFList")
			echo "parentList_ $_parentList" >> $_configFileNamePath

			echo "barSize0_ $_barSize0" >> $_configFileNamePath
			echo "barSize2_ $_barSize2" >> $_configFileNamePath
			echo "barSize4_ $_barSize4" >> $_configFileNamePath
			echo "PORLinkSpeed_ $_linkSpeed" >> $_configFileNamePath
			echo "PORLinkWidth_ $_linkWidth" >> $_configFileNamePath
			echo "custPduScript_ $_extScriptName" >> $_configFileNamePath
			echo "driverModName_ $_devDriver" >> $_configFileNamePath
			echo "otherDriver_ $_otherDriver" >> $_configFileNamePath
			echo "USPDID_ $_USPDID" >> $_configFileNamePath
			echo "FLRUnBind_ $_FLRUnbind" >> $_configFileNamePath
			echo "openCLDev_ $_openCLDev" >> $_configFileNamePath
			echo "$_configFileNamePath created with $numCycles cycles."
			
		if [[ $(lsmod | grep "$_devDriver\ ") ]]; then
			printf "$_devDriver driver module found and loaded.\n"
		else
			logv "$_devDriver driver module is not loaded. Attempting to start it."
			startDriverMod
		fi
		if [[ ! $(lsmod | grep "$_devDriver\ ") ]]; then
			logv "******************************************************"
			logv "Fatal Error: Device driver not loaded."
			logv "Install or start the $_devDriver driver to continue."
			logv "******************************************************"
			exit 1
		fi
#TODO: Make this a list to iterate through.
		if [[ $_testType = "warm" || $_testType = "cold" || $_testType = "custom" ]] || [[ $_testType = "soft" ]]; then
			generateSystemdUnitFile
			printf "Script autostart file created.\n"
			echo "sbinPath_ $_scriptRunningPath" >> $_configFileNamePath
		fi

		captureLspciToLog

		else 
			printf "****************************************************************************\n"
			printf "Error: Could not create config file. Confirm current directory is writeable.\n"
			printf "****************************************************************************\n"
		fi
	else
		exitHelp
	fi
	updateResetCounter
	parseConfigFile
}


function parseConfigFile() {
	echo "Calling config parser."
	if [ -e "$_configFileNamePath" ]; then 
		_resetCount=$(grep -m1 ^resetsLeft_ "$_configFileNamePath" | cut -d ' ' -f 2)
		_EPBDFList=$(grep -m1 ^endPoints_ "$_configFileNamePath" | cut -d ' ' -f 2-500)
		_deviceSearchString=$(grep -m1 ^testTarget_ "$_configFileNamePath" | cut -d ' ' -f 2-127)
		_testType=$(grep -m1 ^testType_ "$_configFileNamePath" | cut -d ' ' -f 2)
		_logFileName=$(grep -m1 ^logName_ "$_configFileNamePath" | cut -d ' ' -f 2)
		_USPort=$(grep -m1 ^USP_ "$_configFileNamePath" | cut -d ' ' -f 2-500)
		_extScriptName=$(grep -m1 ^custPduScript_ "$_configFileNamePath" | cut -d ' ' -f 2-40)
		_origPath=$(grep -m1 ^pathOrig_ "$_configFileNamePath" | cut -d ' ' -f 2-200)
		_devDriver=$(grep -m1 ^driverModName_ "$_configFileNamePath" | cut -d ' ' -f 2)
		_otherDriver=$(grep -m1 ^otherDriver_ "$_configFileNamePath" | cut -d ' ' -f 2)
		_otherBDFs=$(grep -m1 ^otherBDFs_ "$_configFileNamePath" | cut -d ' ' -f 2-500)
		_barSize0=$(grep -m1 ^barSize0_ "$_configFileNamePath" | cut -d ' ' -f 2)
		_barSize2=$(grep -m1 ^barSize2_ "$_configFileNamePath" | cut -d ' ' -f 2)
		_barSize4=$(grep -m1 ^barSize4_ "$_configFileNamePath" | cut -d ' ' -f 2)
		_PORLinkSpeed=$(grep -m1 ^PORLinkSpeed_ "$_configFileNamePath" | cut -d ' ' -f 2)
		_PORLinkWidth=$(grep -m1 ^PORLinkWidth_ "$_configFileNamePath" | cut -d ' ' -f 2)
		_altLogName=$(grep -m1 ^altLogName_ "$_configFileNamePath" | cut -d ' ' -f 2)
		_launchPath=$(grep -m1 ^launchPath_ "$_configFileNamePath" | cut -d ' ' -f 2)
		_wrapperLogPath=$(grep -m1 ^wrapperLogPath_ "$_configFileNamePath" | cut -d ' ' -f 2)
		_parentList=$(grep -m1 ^parentList_ "$_configFileNamePath" | cut -d ' ' -f 2-500)
		_numDevs=$(grep -m1 ^numDevs_ "$_configFileNamePath" | cut -d ' ' -f 2)
		_USPDID=$(grep -m1 ^USPDID_ "$_configFileNamePath" | cut -d ' ' -f 2)
		_FLRUnbind=$(grep -m1 ^FLRUnBind_ "$_configFileNamePath" | cut -d ' ' -f 2)
		_openCLDev=$(grep -m1 ^openCLDev_ "$_configFileNamePath" | cut -d ' ' -f 2)
	else 
		printf "Config File not found, skipping parse.\n"
	fi
}


function hlsmiCheck() {
	if ! type hl-smi>/dev/null 2>&1; then
		logv "*************************************************************"
		logv "Warning: hl-smi not found. This tool is required to continue."
		logv "Install hl-smi before restarting this test."
		logv "*************************************************************"
		wrapUp
		exit 1
	fi
}


function gaudiPortHealthCheck() {
	logv "*****************************"
	logv "Checking Device SERDES Status"
	logv "*****************************"
	for bdf in $_EPBDFList; do
		local retryCount=15
		logv "checking device $bdf"
		local portCount=$(hl-smi -i $bdf -n ports | grep internal | wc -l)
		
		while [[ -n "$portCount" ]] && [[ $(hl-smi -i $bdf -n link | grep UP -ic) -lt $portCount ]] &&
			[[ $retryCount -gt 0 ]]; do
			logv "$portCount SERDES ports found"
			logv "$(hl-smi -i $bdf -n link | grep UP -ic) SERDES online"
			logv "$bdf SERDES are not ready. Sleeping a bit before retrying."
			let "retryCount--"
			countdown 3
		done
		if [[ $portCount -eq $(hl-smi -i $bdf -n link | grep UP -ic) ]] && [[ -n $portCount ]]; then
			logv "$portCount ports found"
			logv "$(hl-smi -i $bdf -n link | grep UP -ic) are online"
			logv "All ports are linked for device $bdf."
			logv "Check PASSED"
		elif [[ -n $portCount ]] && [[ $retryCount -eq 0 ]]; then
			logv "Error: Check Failed."
			logv "Some SERDES ports are offline. See logs for details"
			logv $(hl-smi -i $bdf -n link)
			testFail
		fi
	done
}


function checkGaudiStatus() {
	logv "***********************"
	logv "Checking device status."
	logv "***********************"
	local status=""
	local healthPath=""
	local healthFile
	local retryCount
	local detectRetry
	local retries=15
	
	startDriverMod #Incase driver was not running for some reason.
	for (( device=0; device<_numDevs; device++ )); do
		retryCount=$retries		
		while [[ ! -e /sys/class/accel/accel${device}/device/status ]] &&
		[[ $retryCount -gt 0 ]]; do
			logv "Device status is not available yet. Waiting a few seconds."
			countdown 3
			let "retryCount--"
		done
		retryCount=$retries
		if [[ -e /sys/class/accel/accel${device}/device/status ]]; then
			status=$(cat /sys/class/accel/accel${device}/device/status 2>/dev/null)
			while [[ $status != *"Operational"* ]] && [[ $retryCount -gt 0 ]]; do
				logv "Device $device is not ready. Status is: $status"
				logv "Sleeping a bit before retrying."
				let "retryCount--"
				countdown 3
				status=$(cat /sys/class/accel/accel${device}/device/status 2>/dev/null)
			done
			
			if [[  $status = *"Operational"* ]]; then
				logv "Device $device Operational"
			else
				logv "Error: Device $device status is $status, expected *Operational* status."
				testFail
			fi
		else
			logv "Error: Device $device status is unavailable. Device may be offline."
			testFail
		fi
	done
}


#TODO: Need to test this function.
function stopDriverMod() {
	local drivers="$1"

	for modRemoveTarget in ${drivers}; do
		if [[ $(lsmod | grep "$modRemoveTarget\ ") ]]; then 
			retryCount=10
			logv "Stopping $_devDriver driver module"
			rmmod $modRemoveTarget &
			watchdog "$pid" || testFail
			sleep .5
			
			while [[ $(lsmod | grep "$modRemoveTarget\ ") ]] && [[ $retryCount -gt 0 ]]; do
				logv "$modRemoveTarget failed to stop. Sleeping for a bit before retrying."
				countdown 5
				rmmod $modRemoveTarget &
				watchdog "$pid" || testFail
				let "retryCount--"
			done	
			
			if [[ ! $(lsmod | grep "$modRemoveTarget\ ") ]]; then
				logv "$modRemoveTarget module stopped successfully"
			else
				logv "Error: Driver module $modRemoveTarget failed to stop. Quitting."
				exit 1
			fi
		fi
	done
}


#TODO: Need to test this function.
function startDriverMod() {
	local drivers="$1"
	
	for modStartTarget in ${drivers}; do
		if [[ ! $(lsmod | grep "$modStartTarget\ ") ]]; then
			logv "Starting $_devDriver driver module"
			modprobe $modStartTarget &
			watchdog "$pid" || testFail
			sleep 1
			if [[ $(lsmod | grep "$modStartTarget\ ") ]]; then
				logv "$modStartTarget module started successfully"
			else
				logv "Error: Driver module failed to start. Quitting."
				exit 1
			fi
		fi
	done
}


function linkRetrain() {
	local testTargets=""
	local toolPath="${_origPath}/${_assets}/LTSSMtool"
	local outFile="linkRetrainlog*.txt"
	local width=""
	local target=""
	local segment=""
	local outfile=""
	if [[ -e $toolPath ]]; then
		[[ ! -x $toolPath ]] && chmod +x $toolPath
	else
		logv "Error: LTSSMTool is missing. Cannot resume."
		wrapUp
		exit 1
	fi
	
	unbindDrivers
	
	for bdf in $_USPort; do
		PCIGEN=$(genLookup $_linkSpeed)
		width=$(cut -c2-3 <<< "$_linkWidth")
		target=$(cut -d ':' -f2 <<< "$bdf")
		segment=$(cut -d ':' -f1 <<< "$bdf" | cut -c4)
		testTargets="$testTargets [${segment}:0x${target},${width},${PCIGEN}]"
	done
	
	logv "Starting link retrain test."
	rm -f linkRetrainlog*.txt
	if $toolPath linkretrain $numCycles -wait $_retrainDelay $testTargets; then
		outFile="$(ls | sort -r | grep linkRetrainlog | grep -m 1 .txt)"
		if [[ -e $outFile ]]; then
			local results="$(cat $outFile)"
			rm $outFile -f
			local result="$(grep "PASSED" <<< "$results" | wc -l)"
			if [[ "$(grep "PASSED" <<< "$results" | wc -l)" -ne $_numDevs ]] ||
				[[ "$(grep "FAILED" <<< "$results")" ]]; then
				log "$results"
				logv "Link retraining failures detected. See log for details."
				testFail
			else
				log "$results"
				logv "Link retraining test completed successfully."
			fi
		else
			logv "Error: unable to find $outFile. Cannot parse result"
			wrapUp
		fi
	else
		logv "Error: Failed to execute $toolpath"
		wrapUp
	fi
	
	bindDrivers
	
	if [[ -e $_configFileNamePath ]]; then
		sed -i "s/resetsLeft_\ .*/resetsLeft_\ 0/" $_configFileNamePath
	else
		"ERROR: Failed to update the config file. Exiting with Caution."
		exit 1
	fi
}


function disableHotPlug() {
	target=$1
	hotPlugStatus=$(setpci -s $target cap10+18.b) &> /dev/null
	if [[ $((0x$hotPlugStatus >> 5 & 0x1)) -eq 1 ]]; then
		logv "$target hotplug is enabled. Disabling."
		setpci -s $target cap10+18.b=$(printf "%x" "$(( "0x$hotPlugStatus" & ~(0x20) ))")
	fi
}	


function _getLinkTrainingStatus() {
	local device=$1
	local base=$(lspci -vs ${device} | awk '/Capabilities: \[[0-9a-fA-F]+\] Express/ {gsub(/\[|\]/,"",$2); print $2; exit}')
	local registerValues=$(setpci -s $device 0x${base}+0x12.w 2> /dev/null)
	if [[ -z $registerValues ]]; then 
		echo "Link Unavailable"
		return 1
	fi
	local trainingStatus=$(((0x$registerValues >> 11) & 1))      #Link Training (1=in progress)
	local linkStatus=$(((0x$registerValues >> 13) & 1))     #Link Active (1=up)
	
	if [[ $trainingStatus -eq 1 ]] && [[ $linkStatus -eq 0 ]]; then
		echo "Training link"
	elif [[ $trainingStatus -eq 0 ]] && [[ $linkStatus -eq 1 ]]; then
		echo "Link Trained"
	elif [[ $trainingStatus -eq 0 ]] && [[ $linkStatus -eq 0 ]]; then
		echo "Link Disabled"
	else
		echo "Unknown error"
	fi
}


#TODO: Need to re-create the logic on this.
function linkDisableEnable() {
	logv "*****************************************"
	logv "Starting link disable and re-enable test."
	logv "*****************************************"
	local waitTime=2
	local failed=0
	local retry=5
	local retryCounter=0
		
	unbindDrivers
	
	for device in $_USPort; do
		logv "Removing device: $device"
		echo 1 > /sys/bus/pci/devices/${device}/remove
	done
		
	for parentBDF in $_parentList; do
		logv "Disabling root port link $parentBDF"
		setpci -f -s ${parentBDF} cap10+10.b=10:10
	done

	sleep .5
	
	logv "Checking if port disable succeeded."
	for parent in $_parentList; do
		while [[ $(_getLinkTrainingStatus $parent) = "Link Trained" ]] &&
		[[ $retryCounter -lt $retry ]]; do
			logv "Port $parent still appears to be enabled. Waiting a few seconds."
			countdown $waitTime
			let "retryCounter++"
		done
		
		if  [[ $(_getLinkTrainingStatus $parent) = "Link Disabled" ]]; then	
			logv "Device $parent root port disabled successfully."
		else
			logv "Error: Could not disable port $parent."
			failed=1
		fi
	done
			
	logv "Re-enabling PCIe Root Ports."
	for parentBDF in $_parentList; do
		setpci -f -s ${parentBDF} cap10+10.b=00:10 &> /dev/null
	done
		
	logv "Waiting a few seconds to allow bus to enable before scanning devices."
	countdown 2
	
	pid=""
	for parent in $_parentList; do
		logv "Scanning bus $parent for devices"
		echo 1 > /sys/bus/pci/devices/${parent}/rescan &
		pid="$! $pid"
		sleep .2
	done
	watchdog "$pid" || testFail
		
	logv "Checking if devices are detected and enabled."
	retryCounter=0
	for parent in $_parentList; do
		while [[ $retryCounter -lt $retry ]] && [[ $(_getLinkTrainingStatus $parent) != "Link Trained" ]]; do
			logv "Waiting for link on $parent to enable."
			countdown $waitTime
			let "retryCounter++"
		done
		
		if [[ $(_getLinkTrainingStatus $parent) = "Link Trained" ]]; then
			logv "Device $parent root port enabled and link trained."
			retryCounter=0
		else
			logv "Error: Device $USparentP status: $(_getLinkTrainingStatus ${parent})."
			failed=2;
		fi
	done

	logv "sleeping for a few seconds before continuing."
	countdown $waitTime
	
	bindDrivers

	if [[ $failed -eq 1 ]]; then
		logv "Error: At least one device failed to disable during the test."
		logv "Link Disable Enable test failed"
		printf "See logs for details.\n"
		testFail
	elif [[ $failed -eq 2 ]]; then
		logv "Error: One or more devices failed to re-enable during the test."
		logv "Link Disable Enable test failed"
		printf "See logs for details.\n"
		testFail
	else
		logv "Link Disable Enable cycle completed successfully."
	fi

	return
}


function speedLookup() {
	case $1 in
		1)
			echo "2.5GT/s"
		;;
		2)
			echo "5GT/s"
		;;
		3)
			echo "8GT/s"
		;;
		4)
			echo "16GT/s"
		;;
		5)
			echo "32GT/s"
		;;
		6)
			echo "64GT/s"
		;;
		*)
			logv "Fatal error: Could not determine link speed."
			return 1
		;;
	esac
	return 0
}


function genLookup(){
	case $1 in
		"2.5GT/s")
			echo "1"
		;;
		"5GT/s")
			echo "2"
		;;
		"8GT/s")
			echo "3"
		;;
		"16GT/s")
			echo "4"
		;;
		"32GT/s")
			echo "5"
		;;
		"64GT/s")
			echo "6"
		;;
		*)
			logv "Fatal error: Could not determine link speed $1"
			return 1
		;;
	esac
	return 0
}

# #TODO: Change this to std out values instead of global variable assignments.
# function getLinkSpeed() {
	# #Input the D:B:D.F
	# TARGETDEVICE=$1
	# CURRENTLINKSPEED=$(lspci -vvs $TARGETDEVICE | grep LnkSta: | cut -d " " -f 2 | awk '{$1=$1;print}' | tr -d ",")
	# CURRENTLINKGEN=$(genLookup $CURRENTLINKSPEED)
	# #CURRENTGENSETTING=$(setpci -f -s $TARGETDEVICE CAP_EXP+30.b | cut -c 2)
	# return 
# }


function _getLinkandWidth() {
		local BDF="$1"
		local CURRENTLINKGEN=$(_getPCIeGen ${BDF})
		local CURRENTLINKSPEED=$(speedLookup $CURRENTLINKGEN)
		local linkWidth=$(_getPCIeWidth ${BDF})
		local USPlinkStatus="Speed ${CURRENTLINKSPEED}, Width $linkWidth"
		echo $USPlinkStatus
}


function _getPCIeGen() {
	if [[ -z $1 ]]; then
		printf "Error: Device DBDF required.\n"
		return -1
	fi
	local device=$1
	local base=$(lspci -vs ${device} | awk '/Capabilities: \[[0-9a-fA-F]+\] Express/ {gsub(/\[|\]/,"",$2); print $2; exit}')
	local gen
	gen=$(setpci -s $device 0x${base}+0x12.w)
	gen=$(( 0x$gen & 0xF ))
	echo "$gen"
	return 0
}


function _getLinkSpeed() {
	local BDF="$1"
	local linkGen=$(_getPCIeGen ${BDF})
	local linkSpeed=$(speedLookup ${linkGen})
	echo linkSpeed
}


function _getPCIeGenCap() {
	if [[ -z $1 ]]; then
		printf "Error: Device DBDF required.\n"
		return -1
	fi
	local device=$1
	local base=$(lspci -vs ${device} | awk '/Capabilities: \[[0-9a-fA-F]+\] Express/ {gsub(/\[|\]/,"",$2); print $2; exit}')
	local gen
	gen=$(setpci -s $device ${base}+0C.w)
	gen=$(( 0x$gen & 0xF ))
	echo "$gen"
	return 0
}


function _getPCIeWidth() {
	local device=$1
	if [[ -z $1 ]]; then
		printf "Error: Device DBDF required.\n"
		return -1
	fi
	local base=$(lspci -vs ${device} | awk '/Capabilities: \[[0-9a-fA-F]+\] Express/ {gsub(/\[|\]/,"",$2); print $2; exit}')
	local width
	width=$(setpci -s $device 0x${base}+0x12.w)
	width=$((( 0x$width & 0x3F0 ) >> 4 ))
	echo "x${width}"
	return 0
}


function _getPCIeWidthCap() {
	local device=$1
	if [[ -z $1 ]]; then
		printf "Error: Device DBDF required.\n"
		return -1
	fi
	local base=$(lspci -vs ${device} | awk '/Capabilities: \[[0-9a-fA-F]+\] Express/ {gsub(/\[|\]/,"",$2); print $2; exit}')
	local width
	width=$(setpci -s $device 0x${base}+0x0C.w)
	width=$((( 0x$width & 0x3F0 ) >> 4 ))
	echo "x${width}"
	return 0
}


function _setLinkSpeed() {
	#Input the D:B:D.f and the target gen
	local BDF=$1
	local gen=$2
	local base=$(lspci -vs ${device} | awk '/Capabilities: \[[0-9a-fA-F]+\] Express/ {gsub(/\[|\]/,"",$2); print $2; exit}')
	logv "Setting link speed to Gen${gen}"
	setpci -f -s ${BDF} ${base}+30.B=0${gen}:0f
	sleep .1

	#Re-equalize the links for gen3 and above.
	if [[ $(_getPCIeGen ${BDF}) -gt 2 ]]; then
		logv "Re-equalizing $BDF"
		setpci -f -s ${BDF} ecap19+04.B=1:1
		sleep .2
	fi
	
	logv "Retraining link"
	setpci -f -s ${BDF} ${base}+10.W=0020:0020
	sleep .2
	
	return 
}


function linkSpeedChange() {
	local retrys=10
	local MAXGEN=$(genLookup $_linkSpeed)
	local fail=0
	local maxRetry=$retrys
	local CURRENTLINKGEN
	local CURRENTLINKSPEED
	#Target speed can be between 1 and 5. This is the PCIe Gen.
	#Determine the card's max speed.
	#if below the max speed, Iterate from the current speed up to max.
	#If at max speed, iterate down to gen 1.
	#Check the link speed each iteration.
	#Iterate back up to the max speed.
	#Check the link each iteration.
	#Run the card health check.
	#EQ setpci -f -s "$bdf" ecap19+04.b=1:1
	
	#recoverLinkSpeed
	
	LINKSPEED=$(speedLookup $_minLinkGen)
	
	logv "*******************************************************************"
	
	unbindDrivers
	
	logv "Setting links to gen $_minLinkGen $LINKSPEED."
	for BDF in $_parentList; do
		CURRENTLINKGEN=$(_getPCIeGen ${BDF})
		CURRENTLINKSPEED=$(speedLookup $CURRENTLINKGEN)
		maxRetry=$retrys
		#getLinkSpeed $BDF
		logv "$BDF initial speed is Gen $CURRENTLINKGEN $CURRENTLINKSPEED, target is Gen $_minLinkGen $LINKSPEED"
		while [[ $CURRENTLINKGEN -gt $_minLinkGen ]] && [[ $maxRetry -gt 0 ]]; do
			_setLinkSpeed $BDF $((CURRENTLINKGEN-1))
			#getLinkSpeed $BDF
			CURRENTLINKGEN=$(_getPCIeGen ${BDF})
			CURRENTLINKSPEED=$(speedLookup $CURRENTLINKGEN)
			sleep 1
			logv "New Speed is Gen $CURRENTLINKGEN $CURRENTLINKSPEED"
			#printf "New Speed is Gen $CURRENTLINKGEN $CURRENTLINKSPEED  \r"
			let "maxRetry--"
			[[ $maxRetry -eq 0 ]] && [[ $CURRENTLINKGEN -ne 1 ]] && fail=1
		done
		printf "\n"
	done
	if [[ $fail -eq 1 ]]; then
		logv "Error: Some PCIe links failed to establish Gen $_minLinkGen. See logs for details."
		testFail
	fi
	sleep 1
	
	logv "*******************************************************************"
	logv "Setting links back to Gen $MAXGEN $_linkSpeed"
	for BDF in $_parentList; do
		maxRetry=$retrys
		CURRENTLINKGEN=$(_getPCIeGen ${BDF})
		CURRENTLINKSPEED=$(_getLinkSpeed ${BDF})
		#getLinkSpeed $BDF
		logv "$BDF initial speed is Gen $CURRENTLINKGEN $CURRENTLINKSPEED, target is $MAXGEN $_linkSpeed"
		while [[ $CURRENTLINKGEN -lt $MAXGEN ]] && [[ $maxRetry -gt 0 ]]; do
			sleep 1
			_setLinkSpeed $BDF $((CURRENTLINKGEN+1))
			CURRENTLINKGEN=$(_getPCIeGen ${BDF})
			CURRENTLINKSPEED=$(_getLinkSpeed ${BDF})
			#getLinkSpeed $BDF
			logv "New speed is Gen $CURRENTLINKGEN $CURRENTLINKSPEED"
			#printf "New speed is Gen $CURRENTLINKGEN $CURRENTLINKSPEED  \r"
			let "maxRetry--"
			[[ $maxRetry -eq 0 ]] && [[ $CURRENTLINKGEN -ne $MAXGEN ]] && fail=1
		done
		printf "\n"
	done

	if [[ $fail -eq 1 ]]; then
		logv "Error: Some PCIe links failed to establish $MAXGEN. See logs for details."
		bindDrivers
		testFail
	else
		logv "All links cycled from Gen $MAXGEN to Gen $_minLinkGen and back to $MAXGEN successfully."
	fi
	
	bindDrivers
	
	return
}


#TODO: Sometimes this MAXGEN variable is blank. Find out why.
function recoverLinkSpeed() {
	local MAXGEN=$(genLookup $_linkSpeed)
	local fail=0
	local maxRetry
	local REEQ=0
	
	logv "Confirming links are set to their target $_linkSpeed Gen $MAXGEN. Adjusting as needed."
	printf "\n"
	for BDF in $_parentList; do
		maxRetry=$((MAXGEN+1))
		CURRENTLINKGEN=$(_getPCIeGen ${BDF})
		CURRENTLINKSPEED=$(_getLinkSpeed ${BDF})
		logv "$BDF link speed is Gen $CURRENTLINKGEN $CURRENTLINKSPEED, target is $MAXGEN $_linkSpeed"
		while [[ $CURRENTLINKGEN -lt $MAXGEN ]] && [[ $maxRetry -gt 0 ]]; do
			sleep 1
			_setLinkSpeed $BDF $((CURRENTLINKGEN+1))
			CURRENTLINKGEN=$(_getPCIeGen ${BDF})
			CURRENTLINKSPEED=$(_getLinkSpeed ${BDF})
			log "New Speed for $BDF is Gen $CURRENTLINKGEN $CURRENTLINKSPEED"
			#printf "New Speed for $BDF is Gen $CURRENTLINKGEN $CURRENTLINKSPEED  \r"
			printf "New Speed for $BDF is Gen $CURRENTLINKGEN $CURRENTLINKSPEED  \n"
			let "maxRetry--"
			[[ $maxRetry -eq 0 ]] && [[ $CURRENTLINKGEN -ne $MAXGEN ]] && fail=1
		done
		printf "\n"
	done
	if [[ $fail -eq 1 ]]; then
		logv "Error: Some PCIe links failed to establish Gen $MAXGEN $_linkSpeed."
		[[ $_DONOTFAIL -eq 1 ]] && printf "See logs for details.\n"
		[[ $_DONOTFAIL -eq 0 ]] && testFail
	fi
}


#TODO:Add support for driverless mode
function unbindDrivers() {
	#This checks the unbind mode and calls functions to stop drivers or unbind devices. 
	[[ $_FLRUnbind -eq 0 ]] && logv "Stopping device driver." && stopDriverMod "$_devDriver $_otherDriver" ||
	[[ $_FLRUnbind -eq 1 ]] && logv "Unbinding device driver." && unbindDevices "$_devDriver" "$_EPBDFList"
	[[ -n $_otherBDFs ]] && unbindDevices "$_otherDriver" "$_otherBDFs"

}


function bindDrivers() {
	#This checks the unbind mode and driver mode and calls functions to start drivers or bind devices. 
	[[ $_FLRUnbind -eq 0 ]] && logv "Starting device driver." && startDriverMod "$_devDriver $_otherDriver" ||
	[[ $_FLRUnbind -eq 1 ]] && logv "Binding device driver." && bindDevices "$_devDriver" "$_EPBDFList"
	[[ -n $_otherBDFs ]] && bindDevices "$_otherDriver" "$_otherBDFs"
}


function unbindDevices() {
	local driver="$1"
	local devices="$2"
	local failed=0
	parseConfigFile
	stopxpumd #This prevents pmt polling that could cause a crash.
	local pid=""
	
	for i in $devices; do
		if [ -d /sys/bus/pci/drivers/${driver}/$i ]; then
			logv "Unbinding $i"
			echo $i > /sys/bus/pci/drivers/${driver}/unbind &
			pid="$! $pid "
		fi
	done
	
	printf "Waiting for device unbind to complete...\n"
	watchdog "$pid" || testFail
		
	for i in $devices; do
		if [ -d /sys/bus/pci/drivers/${driver}/$i ]; then
			logv "Device $i failed to unbind."
			failed=1
		fi
	done
	if [[ $failed -eq 1 ]]; then
		logv "Continuing test with bound devices may cause kernel panics."
		logv "Terminating test run. Contact your Intel representative for directions."
		testFail
	fi
	sleep 1
}


function bindDevices() {
	local driver="$1"
	local devices="$2"
	local maxRetry=3
	local retryCount=0
	local sleeptime=3
	local pid=""
	local retry=0
	local failed=0
	
	for DEV in $devices; do
		if [[ ! -d /sys/bus/pci/drivers/${driver}/$DEV ]]; then
			printf "binding $DEV\n"
			echo $DEV > /sys/bus/pci/drivers/${driver}/bind &
			while [[ ! -d /sys/bus/pci/drivers/${driver}/$DEV ]] && [[ $retry -lt $maxRetry ]]; do
				logv "Device $DEV not ready. Sleeping before retrying."
				let "retry++"
				sleep $sleeptime
				[[ -d /sys/bus/pci/drivers/${driver}/$DEV ]] && break
				echo $DEV > /sys/bus/pci/drivers/${driver}/bind &
			done
			pid="$pid = $!"
			sleep $_bindTimeout #This maintains device binding order for the driver.
			retry=0
		fi
	done
	logv "Waiting for devices to bind..."
	watchdog "$pid" || testFail
	
	for DEV in $devices; do
		while [[ ! -d /sys/bus/pci/drivers/${driver}/$DEV ]] &&	[[ $maxRetry -gt $retryCount ]]; do
			logv "Device $DEV did not bind. Waiting $sleeptime seconds before re-checking."
			countdown $sleeptime
			let "retryCount++"
			[[ -d /sys/bus/pci/drivers/${driver}/$DEV ]] && break
		done
		if [[ ! -d /sys/bus/pci/drivers/${driver}/$DEV ]]; then 
			logv "Device $DEV failed to bind."
			failed=1
			retryCount=0
		else
			logv "Device rebind successfull."
			retryCount=0
		fi
	done
	if [[ $failed -eq 1 ]]; then
		logv "Some devices failed to bind. Reboot the system to restore functionality."
		testFail
	fi
}


function updateResetCounter() {
	parseConfigFile #Need to call this to ensure all script variables are assigned.
	
	if [[ $_resetCount -gt 0 ]]; then
		logv "**********************"
		logv "$_resetCount cycle(s) remaining."
		logv "**********************"
		_resetCount=$((_resetCount - 1))
		sleep .5

		if [[ -f ${_scriptRunningPath}${_configFileName} ]]; then
			sed -i "s/resetsLeft_\ .*/resetsLeft_\ $_resetCount/" ${_scriptRunningPath}$_configFileName
			sed -i "s/resetsLeft_\ .*/resetsLeft_\ $_resetCount/" $_configFileNamePath
		elif [[ -f $_configFileNamePath ]]; then
			sed -i "s/resetsLeft_\ .*/resetsLeft_\ $_resetCount/" $_configFileNamePath
		else
			logv "Error: Config file missing. Quitting."
			wrapUp
			exit 1
		fi
	else
		healthCheck
		printf "\n"
		logv "No resets remaining. Cleaning up files and exiting."
		logv "Reset cycle set completed successfully."
		logv "No fatal errors or failures were detected during the test."
		logv "**************"
		logv "*Test Passed.*"
		logv "**************"
		testResult="_TestPass"
		wrapUp
		exit 0
	fi
}


function rootCheck() {
	if [ "$EUID" -ne 0 ]; then
		printf "****************************************************\n"
		printf "Error: This script must be run with sudo or as root.\n"
		printf "****************************************************\n"
		exit 1
	else
		printf "User appears to be root.\n"
	fi
}


function resetLogInit() {
	if [[ -w $_logFileName ]]; then
		logv "--------------------------------------------------------------"
		printf "logfile $_logFileName exists and is writeable.\n"
		log "Reset count remaining = $(grep resetsLeft_ "$_configFileNamePath" | cut -d ' ' -f 2)"
		logv "--------------------------------------------------------------"
	elif [[ ! $(touch $_logFileName) ]]; then
		echo "logfile created on $(date)" > $_logFileName
		logv "$scriptVersion"
		log "FLR Timeout: $_FLRtimeout"
		log "SBR Timeout: $_SBRtimeout"
		log "Check $(sha384sum -t ${_realPath}${_scriptName} | cut -d " " -f1)"
		logv "$_testType reset test selected"
		logv "Baseboard $(dmidecode --type baseboard | grep Product\ Name | awk '{$1=$1;print}')"
		logv "Baseboard $(dmidecode --type baseboard | grep Version | awk '{$1=$1;print}')"
		logv "Baseboard $(dmidecode --type baseboard | grep Serial\ Number | awk '{$1=$1;print}')"
		logv "System BIOS $(dmidecode --type bios | grep Version | awk '{$1=$1;print}')"
		logv "--------------------------------------------------------------"
		logv "Reset count remaining = $(grep resetsLeft_ "$_configFileNamePath" | cut -d ' ' -f 2)"
		logv "--------------------------------------------------------------"
		echo "Log file $_logFileName created successfully."
	else 
		printf "*************************************************************************\n"
		wall "Error: Could not create log file. Confirm current directory is writeable."
		printf "*************************************************************************\n"
		wrapUp
		exit 1

	fi
}	


function logv() {
	logInput=$1
	if [[ -w $_logFileName ]] && [[ $(grep created "$_logFileName") ]]; then
		echo "$(date) $logInput" | tee -a $_logFileName
	else
		wall "WARNING: Log file access error. Check log file $_logFileName permissions."
		wall "To continue the reset test, directly re-launch the reset script."
		exit 1
	fi
}


function log() {
	logInput=$1
	if [[ -w $_logFileName ]] && [[ $(grep created "$_logFileName") ]]; then
		echo "$(date) $logInput" >> $_logFileName
	else
		wall "WARNING: Log file write error. Check log file $_logFileName permissions."
		wall "To continue the reset test, directly re-launch the reset script."
		exit 1
	fi
}


function captureLspciToLog() { 
	parseConfigFile
	device=0
	if [[ -n $_EPBDFList ]]; then 
		for i in $_EPBDFList; do
			log "Device $device lspci:"
			log "--------------------------------------------------------------"
			lspci -Dvvs $i >> $_logFileName
			log "--------------------------------------------------------------"
			device=$((device+1))
		done
	else
		logv "***********************************************************"
		logv "Error: No target BDFs found. Try a different search string."
		logv "***********************************************************"
		wrapUp
		exit 1
	fi
}


#TODO: Add an option to pass a failure message into the funbction to add to the log.
function testFail() {
	logv "************"
	logv "Test Failed."	
	logv "************"
	[[ $_devDriver = "i915" ]] || [[ $_devDriver = "xe" ]] && logv "
To collect debug information, please run the infoCollect.sh script
located in the Debug folder and submit the debug logs along with the failing
test log to Intel for analysis.
"

	testResult="_TestFail"
	wrapUp
	exit 1
}


function _PCIeCheck() {
	local PCIeDevices=$1
	local endpoint=$2
	local deviceName=""
	local linkStatus
	local linkCap
	local speedStatus
	local linkWidth
	local widthCap
	local speedCap
	local priorlinkStatus
	
	if [[ -n $endpoint ]] && [[ $endpoint -eq 2 ]]; then
		deviceName="Endpoint"
	else
		deviceName="USP"
	fi
		
	logv "***********************************************************************"
	logv "Checking $deviceName device host link speed and width."
	for i in $PCIeDevices; do
		linkStatus=$(_getLinkandWidth ${i})
		
		linkCap=$(lspci -vvs "$i" | grep LnkCap: | cut -d ":" -f 2 | awk '{$1=$1;print}')
		if [[ -z "$linkStatus" ]] || [[ -z "$linkCap" ]]; then
			logv "********************************Warning********************************"
			logv "$deviceName Link status checking for BDF $i unavailable."
			logv "***********************************************************************"
		fi
		logv "Device $i $deviceName link status: $linkStatus"
		speedStatus=$(speedLookup $(_getPCIeGen ${i}) )
		speedCap=$(speedLookup $(_getPCIeGenCap ${i}) )
		linkWidth=$(_getPCIeWidth ${i})
		widthCap=$(_getPCIeWidthCap ${i})

		if [[ "$speedStatus" != "$speedCap" ]] || [[ "$linkWidth" != "$widthCap" ]] ||
			[[ "$speedStatus" != "$_PORLinkSpeed" ]] && [[ -z $endpoint ]] ||
			[[ "$linkWidth" != "$_PORLinkWidth" ]] && [[ -z $endpoint ]] ; then
			logv "********************************Warning********************************"
			logv "Error: $i $deviceName Link speed or width does not match capability."
			logv "Device $i $deviceName status is downgraded."
			logv "$deviceName link speed capability: $speedCap. Speed detected: $speedStatus"
			logv "$deviceName link width capability: $widthCap. Width detected: $linkWidth"
			logv "Required speed: $_PORLinkSpeed and link width: $_PORLinkWidth"
			logv "***********************************************************************"
			testFail
		else
			logv "$deviceName link $i check shows speed and capability match."
		fi
		
	priorlinkStatus=$(grep $i "$_configFileNamePath" | grep LnkSta: | cut -d ":" -f 4 | awk '{$1=$1;print}')
	printf "Device $i Initial status shows: $priorlinkStatus\n"
	printf "Device $i Current status shows: $linkStatus\n"
	
	if [[ "$priorlinkStatus" = *"$linkStatus"* ]]; then
		printf "Initial link speed and width matches current link status.\n"
	else
		logv "Error: $deviceName link speed or width has changed since the test started."
		logv "Original link status: $priorlinkStatus"
		logv "Current $deviceName link width: $linkWidth"
		logv "$deviceName width capability: $widthCap"
		logv "$deviceName speed status: $speedStatus"
		logv "$deviceName speed capability: $speedCap"
		testFail
	fi
	logv "***********************************************************************"
	done
}


function barCheck() {
	local bar0
	local bar2
	local bar4
	
	logv "Check target device BARs."
	for device in $_EPBDFList; do
		bar0=$(lspci -s $device -vv | grep -m1 Region\ 0: | awk -F 'prefetchable) ' '{print $2}')
		bar2=$(lspci -s $device -vv | grep -m1 Region\ 2: | awk -F 'prefetchable) ' '{print $2}')
		bar4=$(lspci -s $device -vv | grep -m1 Region\ 4: | awk -F 'prefetchable) ' '{print $2}')
		
		if [[ $bar0 = *"$_barSize0"* ]] && [[ $bar2 = *"$_barSize2"* ]] && [[ $bar4 = *"$_barSize4"* ]]; then
			[[ -n $_barSize4 ]] && message="and BAR4 ${_barSize4}"
			logv "Device $device PCIe BAR0 $_barSize0 and BAR2 $_barSize2 ${message} check PASSED."
		else
			logv "Error: Device $device BAR allocation does not match expected."
			logv "BAR0 $bar0"
			logv "BAR2 $bar2"
			[[ -n $_barSize4 ]] && logv "BAR4 $bar4"
			testFail
		fi
	done
	logv "*******************************************************************************"
}


function clinfoCheck() {
	if ! type clinfo>/dev/null 2>&1; then
		printf "********************************************************************\n"
		printf "Warning: clinfo not found. The OpenCL check can slightly decrease\n"
		printf "device wait time. If avaialble on this OS, installation recommended.\n"
		printf "********************************************************************\n"
		countdown 4
	else
		logv "Checking device status with clinfo."
		if [[ $(clinfo -l) = "" ]]; then
			printf "Waiting for devices...\n"
			timeout=4
			while [[ $(clinfo -l) = "" && $timeout > 0 ]]; do 
				let "timeout--"
				printf "$timeout  \r"
				if [[ $timeout = 0 ]]; then
					logv "Warning: clinfo did not detect devices. Confirm device driver is loaded."
					logv "Confirm command clinfo -l works on the command line. Skipping check this time"
				fi
				sleep 1
			done
		elif [[ $(clinfo -l | grep Device -c) -ge $_numDevs ]]; then 
			if [[ $(clinfo -l | grep Device -c) -ne $_numDevs ]]; then
				logv "Notice: clinfo detects more devices than targeted for test."
			fi
			logv "$_numDevs Devices found, driver appears to be loaded. Continuing."
		elif [[ $(clinfo -l) = "" ]]; then
			logv "Warning: clinfo could not run or did not detect any devices. Attempting to continue."
		else
			logv "CLInfo Anomaly: found $(clinfo -l | grep Device -c) out of $_numDevs devices."
			logv "Continuing anyway, but the test may fail."
		fi
	fi
}


function warmReset() {
	wall "Reboot Cycler is rebooting the system. Boot to runlevel 1 to halt test early."
	wall "$_resetCount resets remains."
	printf "Resetting the system in: \n"
	countdown 3
	sync
	reboot
	sleep 10 #Gives time to reboot so we don't generate an exit code.
	exit 1
}


function softPowerCycle() {
	#TODO: Check if rtcwake is present.
	wall "Reboot Cycler is rebooting the system. Boot to runlevel 1 to halt test early."
	wall "$_resetCount resets remains."
	printf "Resetting the system in: \n"
	countdown 3
	sync
	rtcwake -l -m no -s $_powerOnDelay
	poweroff -f
	sleep $((_powerOnDelay-10)) #Gives time to reboot so we don't generate an exit code.
	exit 1
}


#Call this with a number for seconds to count down for delays.
function countdown() {
	for (( i=$1; i >= 0; i-- )); do
		printf "Waiting $i  \r"
		sleep 1
	done
	printf "\n"
}


function powerCycle() {
	 if command -v ipmitool>/dev/null; then
		wall "Reboot Cycler is rebooting the system. $_resetCount resets remains."
		wall "Boot to runlevel 1 to halt test early."
		printf "Power cycling the system in \n"
		countdown 3

		sleepTimer=0
		while [[ ! $(lsmod | grep ipmi_devintf) > /dev/null ]]; do
			logv "Waiting for IPMI module to start."
			sleep 1
			let "sleepTimer++"
			if [[ $sleepTimer = 20 ]]; then
				logv "Error: IPMI device driver did not start. Unable to continue."
				wrapUp
				exit 1
			fi
		done
		sync
		sleep 2
		ipmitool chassis power cycle
		#This sleep allows time for the ipmi command to complete. System dependent. Does not impact script completion time.
		sleep 10
	else
		printf "**********************************************\n"
		printf "Error: IPMITool required for power cycle test.\n"
		printf "**********************************************\n"
		wrapUp
		exit 1
	fi

	logv "Fatal Error: IPMI did not respond. System did not reboot."
	exit 1
}


function customReset() {
	if [[ -n "${_extScriptName// }" ]]; then
		wall "Calling custom script $_extScriptName in a few seconds..."
		countdown 3
		logv "Executing external script $_extScriptName"
		sync
		sleep 2
			if [[ $(echo $_extScriptName | rev | cut -d "." -f1 | rev) = "sh" ]]; then
				bash ${_origPath}${_extScriptName}
			elif [[ $(echo $_extScriptName | rev | cut -d "." -f1 | rev) = "py" ]]; then
				if type python>/dev/null 2>&1; then
					python=python
				elif type python3>/dev/null 2>&1; then
					python=python3
				else
					logv "Error: Python not found."
					cleanup
					exit 1
				fi
				$python ${_origPath}${_extScriptName}
			else
				logv "Error: Unknown script extension."
				logv "This test supports .py and .sh scripts."
				cleanup
				exit 1
			fi
	else
		logv "Waiting for external reset trigger"
		sync
		sleep 2
		wall "Waiting for manual power cycle or reset."
	fi
	exit 0
}


function findParents() {
	#This will find the parent devices for the devices PCIe switch. Might need to modify for other devices.
	local _parentList=""
	local BDFList="$1"
	local parent
	local rootPort
	for endPointbdf in $BDFList; do
		parent=$(ls -lr /sys/bus/pci/devices/ | grep $endPointbdf | cut -d "/" -f5-100)
        rootPort=$(echo $parent | rev | cut -d "/" -f$_USPLevel | rev)
		_parentList=$_parentList" "$rootPort
	done
	if [[ $(echo $_parentList | wc -w) -eq $_numDevs ]]; then
		log "Expected parent device count found."
		echo $_parentList
		return 0
	else	
		logv "Error: Expected $_numDevs parent devices, found $(echo $_parentList | wc -w)"
		testFail
	fi
}


function SBReset_No_Remove() {
	local targetBus
	local USPportarray
	local index=0
	
	stopxpumd
	if [[ $_parentList = "" ]]; then
		logv "Error: Could not determine device parents."
		testFail
	fi	
	
	unbindDrivers

	for bus in $_parentList; do
		disableHotPlug $bus
		targetBus=${USPportarray[$index]}
		let "index++"
			
		#logv "Issuing SBR on device $bus"
		#bridgeControl=$(setpci -s $bus BRIDGE_CONTROL)
		#if [[ $bridgeControl = "" ]]; then
	#		logv "Error: Unable to get bridge control register on $bus for SBR operation."
#			wrapUp
#			exit 1
#		fi
		
		#Negotiated speed: setpci -s $bus CAP_EXP+12.W
		setpci -s $bus BRIDGE_CONTROL.B=40:40
		#setpci -s $bus BRIDGE_CONTROL=$(printf %04x $((0x$bridgeControl | 0x40)))
		if [[ $? != 0 ]]; then
			logv "Error: Unable to trigger SBR on $bus."
			wrapUp
			exit 1
		fi
		
		sleep $_SBRtimeout
		setpci -s $bus BRIDGE_CONTROL.B=00:40
		#setpci -s $bus BRIDGE_CONTROL=$bridgeControl
		if [[ $? != 0 ]]; then
			logv "Error: Unable to restore bridge control register."
			wrapUp
			exit 1
		fi
	done

	printf "Sleeping for $_SBRtimeout seconds to allow device recovery: \n"
	countdown $_SBRtimeout	
	
	bindDrivers
	
	#if [[ $_SBRDriverOff -eq 0 ]]; then
	#	if [[ $_FLRUnbind -eq 0 ]]; then
	#		startDriverMod
	#	elif [[ $_FLRUnbind -eq 1 ]]; then
	#		bindDevices "$_devDriver" "$_EPBDFList"
	#	else
	#		logv "Running driverless mode. Skipping driver operations."
	#	fi
	#fi

	return
}

function SBResetSemiSerial() {
	local targetBus
	local USPportarray
	local index=0
	local pid=""
	local missingDev=""
	
	stopxpumd
	if [[ $_parentList = "" ]]; then
		logv "Error: Could not determine device parents."
		testFail
	fi	
	
	unbindDrivers

	pid=""
	USPportarray=($_USPort)

	for bus in $_parentList; do
		disableHotPlug $bus
		targetBus=${USPportarray[$index]}
		logv "------------------------------------------"
		logv "Removing upstream device node $targetBus."
		echo 1 > /sys/bus/pci/devices/$targetBus/remove &
		pid="$! $pid"
		let "index++"
		logv "Waiting for node removal to complete..."
		watchdog "$pid" || testFail
		sleep 1
	
		if [[ -L "/sys/bus/pci/devices/$targetBus" ]]; then
			logv "Error: Upstream node $targetBus removal unsuccessfull."
			testFail
		fi
	
		logv "Issuing SBR on device $bus"
		bridgeControl=$(setpci -s $bus BRIDGE_CONTROL)
		if [[ $bridgeControl = "" ]]; then
			logv "Error: Unable to get bridge control register on $bus for SBR operation."
			wrapUp
			exit 1
		fi
		
		setpci -s $bus BRIDGE_CONTROL=$(printf %04x $((0x$bridgeControl | 0x40)))
		if [[ $? != 0 ]]; then
			logv "Error: Unable to trigger SBR on $bus."
			wrapUp
			exit 1
		fi
		
		sleep $_SBRtimeout
		setpci -s $bus BRIDGE_CONTROL=$bridgeControl
		if [[ $? != 0 ]]; then
			logv "Error: Unable to restore bridge control register."
			wrapUp
			exit 1
		fi
		
		printf "Sleeping for $_SBRtimeout seconds before bus rescan: \n"
		#TODO: Hardcode this.
		countdown $_SBRtimeout
		
		if [[ $_fullBusRescan = 0 ]]; then
			logv "Rescanning for devices at node $bus"
			echo 1 > /sys/bus/pci/devices/$bus/rescan &
			pid="$! $pid"
		else
			logv "Rescanning globally for devices"
			echo 1 > /sys/bus/pci/rescan &
			pid="$! $pid"
		fi

		logv "Waiting for scan to complete..."
		watchdog "$pid" || testFail
		sleep $_SBRtimeout #Increase if failing.

		logv "Waiting for endpoint detection after rescan."
		timeout=$_watchdogTimeout	
		local notFound=1
	
		while [[ $notFound -eq 1 && $timeout>0 ]]; do
			sleep 1
			notFound=0
			for i in $_USPort; do
				if [[ ! -L "/sys/bus/pci/devices/$i" ]]; then
					notFound=1
					break
				fi
			done
			printf "$timeout "
			timeout=$((timeout-1))
		done
	done
	
	printf "\n"
	sleep 1

	for i in $_USPort; do
		if [[ -L "/sys/bus/pci/devices/$i" ]]; then
			logv "Device $i found"	
		else
			missingDev="$missingDev $i"
		fi
	done
	
	if [[ $notFound -eq 1 ]]; then
		logv "Error: Some devices were not found after rescan."
		logv "Missing Devices: $missingDev"
		testFail
	fi
	
	bindDrivers
	
	#if [[ $_SBRDriverOff -eq 0 ]]; then
	#	if [[ $_FLRUnbind -eq 0 ]]; then
	#		startDriverMod
	#	elif [[ $_FLRUnbind -eq 1 ]]; then
	#		bindDevices "$_devDriver" "$_EPBDFList"
	#	else
	#		logv "Running driverless mode. Skipping driver operations."
	#	fi
	#fi
	return
}


function SBReset() {
	local targetBus
	local USPportarray
	local index=0
	local pid=""
	local missingDev=""
	
	stopxpumd
	if [[ $_parentList = "" ]]; then
		logv "Error: Could not determine device parents."
		testFail
	fi	

	unbindDrivers
	
	pid=""
	USPportarray=($_USPort)
	#DSPportarray=($_EPBDFList) #This line is not used anymore.

	for bus in $_parentList; do
		disableHotPlug $bus
		targetBus=${USPportarray[$index]}
		logv "Removing upstream device node $targetBus."
		echo 1 > /sys/bus/pci/devices/$targetBus/remove &
		pid="$! $pid"
		let "index++"
	done
	logv "Waiting for node removal to complete..."
	watchdog "$pid" || testFail
	sleep 1
	

	index=0
	for bus in $_parentList; do
		targetBus=${USPportarray[$index]}
		if [[ -L "/sys/bus/pci/devices/$targetBus" ]]; then
			logv "Error: Upstream node $targetBus removal unsuccessfull."
			testFail
		fi

		logv "Issuing SBR on device $bus"
		bridgeControl=$(setpci -s $bus BRIDGE_CONTROL)
		if [[ $bridgeControl = "" ]]; then
			logv "Error: Unable to get bridge control register on $bus for SBR operation."
			wrapUp
			exit 1
		fi
		
		setpci -s $bus BRIDGE_CONTROL=$(printf %04x $((0x$bridgeControl | 0x40)))
		if [[ $? != 0 ]]; then
			logv "Error: Unable to trigger SBR on $bus."
			wrapUp
			exit 1
		fi
		
		sleep 1
		setpci -s $bus BRIDGE_CONTROL=$bridgeControl
		if [[ $? != 0 ]]; then
			logv "Error: Unable to restore bridge control register."
			wrapUp
			exit 1
		fi
		let "index++"
	done

	#SBR timeout. Increase if failing.
	resetTimeout=$_SBRtimeout
	printf "Sleeping for $resetTimeout seconds before bus rescan: \n"
	countdown $resetTimeout
	
	if [[ $_fullBusRescan = 0 ]]; then
		for bus in $_parentList; do
			logv "Rescanning for devices at node $bus"
			echo 1 > /sys/bus/pci/devices/$bus/rescan &
			sleep $_SBRrescanTimeout
			pid="$! $pid"
		done
	else
		logv "Rescanning globally for devices"
		echo 1 > /sys/bus/pci/rescan &
		pid="$! $pid"
	fi

	logv "Waiting for scan to complete..."
	watchdog "$pid" || testFail
	sleep $_SBRtimeout #Increase if failing.
		
	logv "Waiting for endpoint detection after rescan."
	timeout=$_watchdogTimeout	
	local notFound=1
	while [[ $notFound -eq 1 && $timeout > 0 ]]; do
		sleep 1
		notFound=0
		for i in $_USPort; do
			if [[ ! -L "/sys/bus/pci/devices/$i" ]]; then
				notFound=1
				
				break
			fi
		done
		printf "$timeout "
		timeout=$((timeout-1))
	done
	
	printf "\n"
	sleep 1
	
	for i in $_USPort; do
		if [[ -L "/sys/bus/pci/devices/$i" ]]; then
			logv "Device $i found"	
		else
			missingDev="$missingDev $i"
		fi
	done
	
	if [[ $notFound -eq 1 ]]; then
		logv "Error: Some devices were not found after rescan."
		logv "Missing Devices: $missingDev"
		testFail
	fi
		
	bindDrivers

	return
}


function FLReset() {
	stopxpumd #This prevents test failures.
	local resetList="$_EPBDFList"
	local count=0
	local pid""
	
	unbindDrivers
	
	pid=""
	
	for flrTarget in $resetList; do
		logv "Issuing FLR on device $flrTarget"
		{
			echo 1 > /sys/bus/pci/devices/$flrTarget/reset
			if [ $? != 0 ]; then
				logv "Error: Device $flrTarget failed to reset."
				bindDrivers
				testFail
			fi
		}&
		pid="$! $pid"
		sleep .5
	done
	
	logv "Waiting for FLR to complete..."
	watchdog "$pid" || testFail
	
	logv "FLR cycle completed."
	
	#$_FLRtimeout Wait for the device reset to complete. Increase if failing.
	for (( i=$_FLRtimeout; $i >= 0; i-- )); do
		printf "Sleeping for $_FLRtimeout seconds before binding devices: $i  \r"
		sleep 1
	done
	
	printf "\n"
	
	bindDrivers

	targets=$(ls /sys/bus/pci/drivers/$_devDriver/ | grep 000)
	for targetDev in $targets; do
		if [[ $_EPBDFList =~ $targetDev ]]; then
			resetList="$targetDev $resetList"
			let "count++"
		fi
	done

	if [[ $_numDevs -ne $count ]]; then 
		logv "************************************************************************"
		logv "Fatal Error: FLR Device count mismatch. Some FLR devices may be unbound."
		logv "Check logs for additional info. A device may have failed to complete FLR."
		logv "Expected $numDevs, found $count."
		logv "************************************************************************"
		testFail
	else
		logv "FLR cycle successful on all devices."
	fi
	return
}


function cycleDriver() {
	stopDriverMod "$_devDriver $_otherDriver"
	
	startDriverMod "$_devDriver $_otherDriver"
}


function wrapUp() {
	printf "wrapping up.\n"
#TODO: Make this a list to iterate through for maintenance.		
	if [[ $_testType = "warm" || $_testType = "cold" || $_testType = "custom" ]] || [[ $_testType = "soft" ]]; then 
		cleanUpSystemdUnitFile
	fi
	
	#TODO: Should I take this out?
	# [[ $_testType = "linkchange" ]] && _DONOTFAIL=1 && recoverLinkSpeed

	rm -f $_configFileNamePath
	rm -f ${_origPath}/$_configFileName
			
	if [[ $_launchPath != $_origPath ]] && [[ -d $_wrapperLogPath ]]; then
		newAltLogName=$_altLogName-$(date -u "+%Y-%m-%d_%H%M%S")$testResult.log
		cp $_logFileName $newAltLogName
		
		if [[ -e $newAltLogName ]]; then
			wall "Log file saved to $newAltLogName"
			rm -f $_logFileName
		else
			wall "Error writing new log. Log saved as $_logFileName"
		fi
	else
		newLogName=${_logFileName}-$(date -u "+%Y-%m-%d_%H%M%S")$testResult.log
		cp $_logFileName $newLogName
		
		if [[ -e $newLogName ]]; then
			wall "Log file saved to $newLogName"
			rm -f $_logFileName
		else
			wall "Error writing new log. Log saved as $_logFileName"
		fi
	fi
}


function generateBDFList() {
	local numDevs
	local targetDevice=$1
	if [[ $(echo $targetDevice | wc -w) > 1 ]]; then
		BDFs=""
		for i in $targetDevice; do
			if [[ $(lspci -Dd:$i) = "" ]]; then
				logv "*********************************************************************"
				logv "Error:  Device $i not found. Verify command line. quitting..."
				logv "*********************************************************************"
				wrapUp
				exit 1
			fi
		done
	else
		BDFs=$(lspci -Dd:$targetDevice | awk -- 'BEGIN {list=""} {if(list!="") list=list " "; list=list $1} END {print list}')
	fi
	
	if [ -z "$BDFs" ]; then
		logv "********************************************************************"
		logv "Error: No target devices detected. Verify Command line. Exiting."
		logv "********************************************************************"
		wrapUp
		exit 1
	else
		echo "$BDFs"
	fi
	return 0
}


function saveLinkStatus() {
	local targets="$1"
	for i in $targets; do	
		deviceStat=$(lspci -s "$i" -vvv | grep LnkSta:)
		echo "$i $deviceStat" >> $_configFileNamePath
	done
}


function triggerReset() {
	local testType="$(grep ^testType_ "$_configFileNamePath" | cut -d ' ' -f 2)"
	logv "++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
	[[ ! $testType = "retrain" ]] && logv "$_resetCount cycle(s) remaining."
	logv "Initiating $testType test at $(date)"
	log "Check $(sha384sum -t ${_realPath}${_scriptName} | cut -d " " -f1)"
	logv "------------------------------------------------------------"
	
	case $testType in
		warm)
			warmReset
		;;
		soft)
			softPowerCycle
		;;
		cold)
			powerCycle
		;;
		flr)
			FLReset
		;;
		sbr)
			case $_SBRMode in
				0)
					SBReset_No_Remove
				;;
				1)
					SBResetSemiSerial
				;;
				2)
					SBReset
				;;
				*)
					logv "Fatal Error: SBRMode is $_SBRMode"
					exit 1
				;;
			esac
		;;
		linkdisable)
			linkDisableEnable
		;;
		linkchange)
			linkSpeedChange
		;;
		retrain)
			linkRetrain
		;;
		custom)
			customReset
		;;
		drivercycle)
			cycleDriver
		;;
		testonly)
			logv "Test Only selected. Cleaning up config."
			wrapUp
		;;
		*)
			logv "Somehow a bad test type slipped through. Check command line. Quitting."
			wrapUp
			exit 1
		;;
	esac
}


function generateSystemdUnitFile() {
	parseConfigFile
 	logv "Generating startup config unit file ${_systemdPath}$_serviceName."
	[[ ! -f ${_systemdPath}$_serviceName ]] && touch ${_systemdPath}$_serviceName
	
	if [[ -w ${_systemdPath}$_serviceName ]]; then
	
		echo "[Unit]
Description=Reboot Cycle Script
After=network.target
[Service]
Type=idle
ExecStart=${_scriptRunningPath}$_scriptName
TimeoutStartSec=0
Restart=on-failure
RestartSec=15
[Install]
WantedBy=multi-user.target" > ${_systemdPath}$_serviceName

	else
		logv "*************************************************************"
		logv "Error: ${_systemdPath}$_serviceName is not writeable. Quitting..."
		logv "*************************************************************"
		wrapUp
		exit 1
	fi

	systemctl daemon-reload
	systemctl enable $_serviceName
	
	logv "Copying reset script to Service compatible location $_scriptRunningPath."
	if [[ ! -f ${_scriptRunningPath}$_scriptName ]] && [[ ! -f ${_scriptRunningPath}$_configFileName ]] ; then
		cp $_realPath$_scriptName $_scriptRunningPath 
		cp $_configFileNamePath $_scriptRunningPath
		_resetCount=$((_resetCount - 1))
		sed -i "s/resetsLeft\ .*/resetsLeft\ $_resetCount/" ${_scriptRunningPath}$_configFileName
		sleep 1
	else
		logv "***********************************************************"
		logv "Error: ${_scriptRunningPath}$_scriptName or ${_scriptRunningPath}$_configFileName already exists."
		logv "run: ./$_scriptName clean"
		logv "to start a new test set. Quitting..."
		logv "***********************************************************"
		wrapUp
		exit 1
	fi
}


function cleanUpSystemdUnitFile() {
	logv "Cleaning up boot cycle unit file."
	[[ -n $_systemdPath ]] && [[ -n $_serviceName ]] &&
		rm -f ${_systemdPath}$_serviceName && logv "Cleaning up reset service unit file."

	[[ -n $_scriptRunningPath ]] && [[ -n $_scriptName ]] &&
		rm -f ${_scriptRunningPath}$_scriptName && logv "Cleaning up service script copy."
	
	[[ -n $_scriptRunningPath ]] && [[ -n $_configFileName ]] &&
		rm -f ${_scriptRunningPath}$_configFileName && logv "Cleaning up script config file."

	logv "Cleanup complete."
	systemctl daemon-reload
}


#TODO: Add:
#logv "cat /sys/kernel/debug/dri/${card}/gt0/uc/huc_info"
#cat /sys/kernel/debug/dri/${card}/gt0/uc/huc_info >> $_logFileName


#ZE_Peak workload test
function ZEPeakTest() {
	local pid=""
	if [[ ! -f ${_origPath}/$_assets/ze_peak ]]; then
		logv "Fatal Error: ${_origPath}$_assets/ze_peak not found. Re-install test package and retry."
		wrapUp
		exit 1
	else
		command="./ze_peak -t sp_compute -i 1 -w 0"
		chmod +x ${_origPath}$_assets/ze_peak
		logv "******************************************"
		
		for (( device=0; device<$_numDevs; device++ )); do
			logv "Launching ze_peak on device $device."
			(export ZE_AFFINITY_MASK=$device && cd $_origPath$_assets/ && $command > "$_origPath/ze_peaktmp.$device") & 
			pid="$! $pid"
		done
		
		logv "******************************************"
		echo "Waiting for workload to complete..."
		watchdog "$pid" || failed=1

		for (( dev=0; dev<$_numDevs; dev++ )); do
			CLPResult=$(cat "${_origPath}ze_peaktmp.$dev")
			logv "$CLPResult"
			if grep -i -e "Data\ Center" -e "$_deviceSearchString" <<< "$CLPResult" &> /dev/null && #TODO: Need to make this check more robust.
				grep "float16" <<< $CLPResult &> /dev/null; then
				logv "*****Device $dev Workload test passed.*****"				
				failed=0
				_recovery=0
			else
				logv "*****************************************"
				logv "Error: ZE_Peak test Failed on device $dev."
				logv "*****************************************"
				failed=1
				log "$CLPResult"
				let "_recovery++"
				break
			fi
		done
			unset ZE_AFFINITY_MASK
			rm -f $_origPath/ze_peaktmp*
		#If a workload test fails to run once time after either an SBR or FLR, this can attempt to
		#recover by first issuing an flr. If this does not recover the device, we try  SBR (hot reset). If devices still fail, we assume something bad happened between the host and card and end the test.
		if [[ $failed -eq 0 ]]; then
			logv "************************************"
			logv "Workload test passed on all devices."
			logv "************************************"
		elif [[ $_testType = "flr" ]] && [[ $_recovery -lt 2 ]] ; then
			logv "**************************************************************************"
			logv "Device failed to function during $_testType test. Attempting recovery with FLR."
			logv "**************************************************************************"
			FLReset
		elif [[ $_testType = "sbr" ]] || [[ $_testType = "flr" ]] && [[ $_recovery -lt 3 ]] ; then
			logv "***********************************************************************************"
			logv "Device failed to function after $_testType recovery. Attempting to recover again with SBR."
			logv "***********************************************************************************"
			SBReset
		elif [[ $failed -eq 1 ]]; then
			logv "See logs for details."
			rm -f $_origPath/ze_peaktmp*
			testFail
		else
			logv "Test ended in a strange situation. Quitting."
			exit 1
		fi
	fi
}


function stopxpumd() {

	if [[ $(pgrep xpumd) ]]; then
		if [[ $(systemctl stop xpum) ]]; then 
			logv "Error: XPUMD could not be stopped. Continuing will cause a system hang or driver issues."
			logv "Please contact your intel representative for direction. Exiting..."
			wrapUp
			exit 1
		else
			logv "XPUMD process successfully stopped."
		fi
	fi
}


#This function returns a 1 if watchdog timeout is met, 0 for success.
function watchdog() {
	local pids=$1
	local timeout=$_watchdogTimeout
	local piddead=0
	printf "Starting timeout countdown...\n"
	while [[ $piddead -eq 0 && $timeout -gt 0 ]]; do
		piddead=1
		for i in $pids; do
			if ps -p $i &>/dev/null; then
				piddead=0
				break
			fi
		done
		printf "Remaining time limit: $timeout  \r"
		let "timeout--"
		sleep 1
	done
	printf "\n"
    if [[ $timeout -eq 0 ]]; then
		logv "*********************************************************"
		logv "Current task exceeded time limit. Killing remaining PIDs."
		logv "*********************************************************"
		kill -9 $pids &>/dev/null
		wait
		return 1
	else
		return 0
	fi
	sleep .5
}


function main() {
	rootCheck
	while true; do
		generateConfig
		healthCheck
		triggerReset
	done
}

main
