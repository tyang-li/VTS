#!/bin/bash
export LC_ALL=en_US.UTF-8
scriptVer=0.5

#Changelist
#V0.5 - Initial release.


realPath=$(realpath ${BASH_SOURCE[0]})
realPath="${realPath%/*}/"
launchPath=$(pwd)"/"
wrapperLogPath=${launchPath}"Logs/Test_Logs/"
timestamp=$(date "+%Y-%m-%d_%H%M%S")
logFolder=debugLogs-$timestamp
logFileName=${realPath}$logFolder/DiagInfo-${timestamp}.log
logFileNameExtra=${realPath}$logFolder/DiagInfoExtra-${timestamp}.log
dmesgLogFileName=${realPath}$logFolder/dmesg-${timestamp}.log
journalLogFileName=${realPath}$logFolder/journalctl-${timestamp}.log
lastBootJournalLogFileName=${realPath}$logFolder/lastBootJournalctl-${timestamp}.log
assets=${realPath}../gpu/bmg/tests/reset/content/

watchdogTimeout=99
TOC=""
testResults=""
tempFile=${logFolder}temp_${timestamp}.tmp
clInfoString="Intel(R)"

#TODO: Add upade-pciids command.

function _findDevices() {
	local deviceEndPointIDs="E211 E221 E223 E20B"

	for i in $deviceEndPointIDs; do
		if [[ $(lspci -d:"$i") ]]; then
			devString="$i"
			logv "Device ID $devString found."
			break
		fi
	done
	
	if [[ $devString = "E211" ]] || 
		[[ $devString = "E221" ]]; then #ARC B60 / B60 IBC
		readonly _targetDev="$devString"
		readonly _deviceType="B60"
		readonly _USPLevel=4
		readonly _modName="xe"
		readonly _linkSpeed="32GT/s"
		readonly _linkWidth="x8"
		readonly _barSize0="16M"
		readonly _barSize2="32G"
		readonly _barSize4=""
		readonly _devMemSize="24"
		readonly _usp="E2FF"
		readonly _vendorID="8086"
		readonly _throttleCoreTemp="105"
		readonly _maxCoreTemp="130" #TODO: Need to verify
	elif [[ $devString = "E223" ]]; then #BMG B70 IBC
		readonly _targetDev="$devString"
		readonly _deviceType="B70"
		readonly _USPLevel=4
		readonly _modName="xe"
		readonly _linkSpeed="32GT/s"
		readonly _linkWidth="x16"
		readonly _barSize0="16M"
		readonly _barSize2="32G"
		readonly _barSize4=""
		readonly _devMemSize="32"
		readonly _usp="E2FF"
		readonly _vendorID="8086"
		readonly _throttleCoreTemp="105"
		readonly _maxCoreTemp="130" #TODO: Need to verify
	elif [[ $devString = "E20B" ]] ||
		[[ $devString = "E20B" ]]; then #ARC B580 -= Not completed.
		readonly _targetDev="$devString"
		readonly _deviceType="B580"
		readonly _modName="xe"
		readonly _usp=""
		readonly _USPLevel=4
		readonly _linkSpeed="16GT/s"
		readonly _linkWidth="x16"
		readonly _barSize0="16M"
		readonly _barSize2="16G"
		readonly _vendorID="8086"
	else
		logv "No supported devices found in system. Errors will be seen in script output."
	fi

	readonly _driverDir="/sys/bus/pci/drivers/$_modName"
	readonly _devCount=$(lspci -d:$_targetDev | wc -l)
	_result "${_deviceType}_Devices_Found_${_devCount}"
	readonly _EPBDFList=$(lspci -Dd $_vendorID:$_targetDev | awk -- 'BEGIN {list=""} {if(list!="") list=list " "; list=list $1} END {print list}')
}


function _PythonCmd() {
	if type python>/dev/null 2>&1; then
		PYCMD="python "
	elif type python3>/dev/null 2>&1; then
		PYCMD="python3 "
	else logv "Python not found"
	fi
}


function _captureBaseboardInfo() {
	_toc "START_BASEBOARD_INFO"
	log "****************************START_BASEBOARD_INFO****************************"
	log "******************HOST_INFO********************************"
	log "Baseboard $(dmidecode --type baseboard | grep Product\ Name | awk '{$1=$1;print}')"
	log "Baseboard $(dmidecode --type baseboard | grep Version | awk '{$1=$1;print}')"
	log "Baseboard $(dmidecode --type baseboard | grep Serial\ Number | awk '{$1=$1;print}')"
	log "******************HOST_BIOS********************************"
	log "System BIOS $(dmidecode --type bios | grep Version | awk '{$1=$1;print}')"
	log "******************HOST_MEMORY********************************"
	log "System memory: $(grep MemTotal /proc/meminfo | tr -d '[:blank:]' | cut -d ":" -f2)"
	log "******************FRU_DATA***********************************"
	log "$(ipmitool fru)"
	log "******************PROC_MEMINFO***********************************"
	log "$(cat /proc/meminfo)"
	log "****************************END_BASEBOARD_INFO****************************"
}


function _rootCheck() {
	if [ "$EUID" -ne 0 ]; then
		printf "****************************************************\n"
		printf "Error: This script must be run with sudo or as root.\n"
		printf "****************************************************\n"
		exit 0
	fi
}


function logv() {
	[[ ! -e $realPath$logFolder ]] && mkdir $realPath$logFolder
	logInput=$1
	echo "$logInput" | tee -a $logFileName
}


function log() {
	[[ ! -e $realPath$logFolder ]] && mkdir $realPath$logFolder
	logInput=$1
	echo "$logInput" >> $logFileName
}


function getXPUCmd() {
	if type xpumcli>/dev/null 2>&1; then
		echo "xpumcli"
	elif type xpu-smi>/dev/null 2>&1; then
		echo "xpu-smi"
	else
		echo 1
	fi
}


function logExtra() {
	logInput=$1
	echo "$logInput" >> $logFileNameExtra
}


function _result() {
	testResults="$testResults $1"
}

	
function _toc() {
	TOC="$TOC $1"
}


function _tocExtra() {
	TOCExtra="$TOCExtra $1"
}


function _scriptVer() {
	logv "*** $0 version ${scriptVer}. ***"
}

#TODO: Update this.
function _openingMessage() {
	printf "************************************************\n"
	printf "This script will attempt to collect the following info:\n"
	printf -- "-lspci output.\n"
	printf -- "-Device driver bindings.\n"
	printf -- "-Loaded device driver modules.\n"
	printf -- "-Host dmesg.\n"
	printf -- "-Device IFWI versions.\n"
	[[ $_deviceType = "pvc" ]] && printf -- "-Device AMC versions.\n"
	printf -- "-clinfo output.\n"
	[[ $_deviceType = "pvc" ]] && printf -- "-CLPeak performance data\n"
	printf -- "-BMC logs.\n"
	printf -- "-BMC telemetry data.\n"
	printf -- "-$_modName Modinfo\n"
	printf -- "-Full LSPCI tree and verbose output\n"
	printf -- "-Sysfs entries related to Intel AIC accelerators.\n"
	printf -- "-Host configuration details.\n"
	printf "************************************************\n"
}


function _checkIommuPT() {
	_toc "START_IOMMU_PT_CHECK"
	log "**********************************START_IOMMU_PT_CHECK**********************************"
	if [[ $(grep "iommu=pt" /proc/cmdline) ]]; then
		_result "IOMMU_Passthrough_ENABLED"
		logv "IOMMU Passthrough Enabled"
	else
		_result "IOMMU_Passthrough_DISABLED"
		logv "IOMMU Passthrough Disabled"
	fi
	log "**********************************END_IOMMU_PT_CHECK**********************************"
}


function _getCmdline() {
	_toc "START_PROC_CMDLINE_OS_VER"
	log "**********************************START_PROC_CMDLINE_OS_VER**********************************"
	log "$(cat /proc/cmdline)"
	log "$(cat /etc/*release)"
	log "$(uname -a)"
	log "**********************************END_PROC_CMDLINE_OS_VER**********************************"
}


function _captureENV() {
	_toc "START_ENV"
	log "**********************************START_ENV**********************************"
	log "$(env)"
	log "**********************************END_ENV**********************************"
}


function _PCIeTreeLinkCheck() {
	_toc "START_PCI_TREE_LINK_CHECK"
	log "******************************START_PCI_TREE_LINK_CHECK*****************************"
	for device in $_EPBDFList; do
		fullTree=$(ls -lr /sys/bus/pci/devices | grep $device)
		IFS="/"
		busTree=$(for i in $fullTree; do echo $i | grep ^00; done)
		unset IFS
	    linkStatus=$(for i in $busTree; do
			printf "Device $i  "
			printf "$(lspci -ns $i | cut -d" " -f3)  "
			lspci -vvs $i | grep LnkSta: | awk '{$1=$1; print}'
		done)
		logv "------------------Device $device PCIe tree link status:------------------"
		logv "$linkStatus"
		
		if grep -i downgraded <<< "$linkStatus" >/dev/null; then
			downDev="$(grep downgraded <<< $linkStatus | awk '{print $2}')"
			downDev=$(tr -s '\n' '_' <<< $downDev)
			_result "Device_${device}_PCI_tree_link_downgrade_detected_on_${downDev}#WARNING#"
			logv "****Link ${device} downgraded. This may cause device performance problems****."
		else
			_result "Device_${device}_PCI_tree_link_check_PASSED"
		fi
	done
	log "******************************END_PCI_TREE_LINK_CHECK*****************************"
}


function _numaCheck() {
	log "**********************************START_NUMA_CONFIG_CHECK**********************************"
	_toc "START_NUMA_CONFIG_CHECK"
	if type numactl>/dev/null 2>&1; then
		numaStatus=$(numactl --hardware)
		socketCount=$(grep "physical id" /proc/cpuinfo | sort --unique | wc -l)
		numaCount=$(grep -m1 available: <<< "$numaStatus" |awk -F" " '{print $2}')
		logv "$numaStatus"
				
		if [[ $socketCount -ne $socketCount ]] || [[ $numaCount -ne $numaCount ]]; then
			logv "Warning: Unable to determine if sub-NUMA is enabled"
			_result "Sub-NUMA_status_could_not_be_determined_#WARNING#"
		elif [[ $socketCount -lt $numaCount ]]; then
			logv "Notice: Sub-NUMA appears to be enabled. This may have a performance impact."
			_result "Sub-NUMA_appears_to_be_enabled"
		elif [[ $socketCount -eq $numaCount ]]; then
			logv "sub-NUMA appears to be disabled."
			_result "Sub-NUMA_appears_to_be_disabled"
		fi
	else
		logv "Error: numactl not installed. Unable to check numa config."
		_result "numactl_not_installed_sub-NUMA_status_could_not_be_determined_#WARNING#"
	fi
	log "**********************************END_NUMA_CONFIG_CHECK**********************************"
}


function _hugePagesCheck() {
	log "**********************************START_HUGE_PAGES_CHECK**********************************"
	_toc "START_HUGE_PAGES_CHECK"
	pageSize=$(sysctl vm.nr_hugepages | awk '{print $3}')
	if [[ $pageSize -lt 50000 ]] && [[ $pageSize -eq $pageSize ]]; then
		_result "Huge_Pages_Size_is_${pageSize}_Recommend_50000_Check_#FAILED#"
		logv "Huge Page size below 50000. Detected $pageSize"
		logv "Set huge pages with sysctl vm.nr_hugepages=50000"
	elif [[ $pageSize -ge 50000 ]] && [[ $pageSize -eq $pageSize ]]; then
		_result "Huge_Pages_Size_is_${pageSize}_Check_PASSED"
		logv "Huge Page size at or above 50000. Detected $pageSize"
	else
		_result "Could_Not_Determine_if_Huge_Pages_Size_is_50000_#WARNING#"
		logv "WARNING: Could not determine huge page size."
	fi
	log "**********************************END_HUGE_PAGES_CHECK**********************************"
}


function countdown() {
	for (( i=$1; i >= 0; i-- )); do
		printf "Waiting $i  \r"
		sleep 1
	done
	printf "\n"
}


function _xpuTempCheck() {
	_toc "START_DEVICE_TEMPS"
	local maxTempDelta=10
	local temperature=""
	local temps=""
	local allTemps=""
	local sortedTemps=""
	local delta=""
	local memTemp=""
	local xpucmd=$(getXPUCmd)

	log "**********************************START_DEVICE_TEMPS**********************************"
	if [[ $xpucmd -eq 1 ]]; then
		logv "Error: Missing XPU. Skipping device temperature check."
		_result "XPU_MISSING_DEVICE_TEMP_TEST_SKIPPED_#WARNING#"
		return 1
	fi
	
	logv "Checking device temperatures"

	for device in $_EPBDFList; do
		temperature=$(${xpucmd} dump -d $device -n1 -m 3 | sed '1d' | awk -F" " '{print $3}' | cut -f1 -d".")
		allTemps="$allTemps $temperature"
		if [[ ! $temperature -eq $temperature ]] &> /dev/null; then
			logv "Error: Temperature value not a number. Device may be offline." 
		elif [[ $temperature -lt $_throttleCoreTemp ]] && [[ ! -z $temperature ]]; then
			_result "Device_${device}_Temperature_${temperature}C_Check_PASSED"
			logv "Device_${device}_Temperature_${temperature}C_Check_PASSED"
		elif [[ $temperature -gt $_throttleCoreTemp ]] && [[ $temperature -lt $_maxCoreTemp ]]; then	
			_result "Device_${device}_Temperature_${temperature}C_Check_#WARNING#"
			logv "Device_${device}_Temperature_${temperature}C_Check_#WARNING#"
		else
			_result "Device_${device}_Temperature_${temperature}C_Check_#FAILED#"
			logv "Device_${device}_Temperature_${temperature}C_Check_#FAILED#"
		fi
	done

	if [[ ! -z $allTemps ]]; then
		logv "Checking if device temperature deltas are below ${maxTempDelta}C"
		sortedTemps=$(for i in $allTemps; do echo $i; done | sort)
		sortedTemps=( $sortedTemps )
		local lastIndex=$(( $(echo ${#sortedTemps[@]}) - 1 ))
		if [[ ${sortedTemps[$lastIndex]} -eq ${sortedTemps[$lastIndex]} &&
			${sortedTemps[0]} -eq ${sortedTemps[0]} ]]; then
			delta=$(( ${sortedTemps[$lastIndex]} - ${sortedTemps[0]} ))
			if [[ $delta -eq $delta ]] && [[ $delta -gt $maxTempDelta ]]; then
				_result "Device_Temperature_Delta_is_${delta}C_Check_#FAILED#"
				logv "Device_Temperature_Delta_is_${delta}C_Check_#FAILED#"
			elif [[ $delta -eq $delta ]] && [[ $delta -le $maxTempDelta ]]; then
				_result "Device_Temperature_Delta_is_${delta}C_Check_PASSED"
				logv "Device_Temperature_Delta_is_${delta}C_Check_PASSED"
			else
				_result "Device_Temperature_Range_Does_Not_Appear_to_be_a_Number_#WARNING#"
				logv "Device_Temperature_Range_Does_Not_Appear_to_be_a_Number_#WARNING#"
			fi
		else
			_result "Device_Temperature_Does_Not_Appear_to_be_a_Number_#WARNING#"
			logv "Device_Temperature_Does_Not_Appear_to_be_a_Number_#WARNING#"
		fi
	else
		logv "Skipping temperature Delta Check. Temperature data unavailable."
		_result "Device_Temperature_Delta_Threshold_Check_SKIPPED"
	fi
	
	logv "Capturing Device Memory Temperatures"
	for device in $_EPBDFList; do
		memTemp=$(${xpucmd} dump -d $device -n1 -m 4 | sed '1d' | awk -F" " '{print $3}' | cut -f1 -d".")
		if [[ $memTemp -eq $memTemp ]]; then
			_result "Device_${device}_Memory_Temperature_is_${memTemp}C"
		else
			_result "Device_${device}_Memory_Temperature_Unavailable_#WARNING#"
		fi
	done
	
	log "**********************************END_DEVICE_TEMPS**********************************"
}


function _hostMemQtyCheck() {
	_toc "START_HOST_MEMORY_SIZE_CHECK"
	log "**********************************START_HOST_MEMORY_SIZE_CHECK**********************************"
	local _hostmem=$((_devMemSize*2*_devCount))
	local mem=$(free --giga | grep Mem | tr -s " " | cut -d " " -f2)

	if [[ $mem -lt $_hostmem ]]; then
		_result "Host_Memory_${mem}_GiB_is_Less_Than_Recommended_${_hostmem}_GiB_#WARNING#"
		logv "Warning: Host memory is below recommended ${_hostmem}_GiB"
	else
		_result "${mem}_GiB_Host_Memory_Meets_Minimum_Recommended_${_hostmem}_GiB_Check_PASSED"
		logv "Host ram is $mem GiB. This meets minimum $_hostmem GiB recommendation."
	fi
	log "**********************************END_HOST_MEMORY_SIZE_CHECK**********************************"
}


function _ZEPeakTest() {
	pid=""
	_toc "START_ZEPEAK_TEST"
	log "**********************************START_ZEPEAK_TEST**********************************"
	if [[ ! -f /$assets/ze_peak ]]; then
		logv "Fatal Error: /$assets/ze_peak not found. Re-install test package and retry."
	else
		command="./ze_peak -t sp_compute"
		chmod +x $assets/ze_peak
		for (( device=0; device<$_devCount; device++ )); do
			logv "Launching ze_peak on device $device."
			(export ZE_AFFINITY_MASK=$device && cd $assets/ && $command > "${assets}zePeaktmp.${device}") & 
			pid="$! $pid"
		done
		logv "******************************************"
		printf "Waiting for workload to complete...\n"
		_watchdog "$pid"
		
		failed=0
					
		for (( dev=0; dev<$_devCount; dev++ )); do
			zePResult=$(cat "${assets}zePeaktmp.${dev}")
			log "$zePResult"
			if grep -i $_targetDev <<< "$zePResult" &> /dev/null && grep "float16" <<< "$zePResult" &> /dev/null; then
				logv "Device $dev Workload test passed."
				_result "Device_${dev}_ZEPeak_Test_PASSED"
			else
				logv "*****************************************"
				logv "ze_peak test FAILED on device $dev."
				logv "*****************************************"
				_result "Device_${dev}_ZEPeak_Test_#FAILED#"
				failed=1
			fi
		done
			ZE_AFFINITY_MASK=""
			rm -f $assets/zePeaktmp*
		if [[ "$failed" -eq 1 ]]; then
			logv "ze_peak test failed. See logs for details."
			rm -f $assets/zePeaktmp*
		else
			logv "************************************"
			logv "Workload test passed on all devices."
			logv "************************************"
		fi
	fi
	log "**********************************END_ZEPEAK_TEST**********************************"
}


function _captureLspci() {
	device=0
	_toc "START_LSPCI"
	log "**********************************START_LSPCI**********************************"
	logv "Capturing lspci for target devices"
	if [[ ! -z $_targetDev ]]; then 
		for i in $_targetDev; do
			log "Device $device lspci:"
			log "--------------------------------------------------------------"
			log "$(lspci -vvvnnDd:$i)"
			log "$(lspci -nnxxxDd:$i)"
			log "--------------------------------------------------------------"
			device=$((device+1))
		done
	else
		logv "Capturing lspci for all devices"
		log "$(lspci -Dvvvnn)"
		log "$(lspci -Dxxxnn)"
	fi
	_toc "START_DEVICE_TREE"
	log "**********************************START_DEVICE_TREE**********************************"
	logv "Capturing lspci device tree"
	log "$(lspci -Dvnnt)"
	log "**********************************END_LSPCI**********************************"
}


function _captureModules() {
	_toc "START_"$_modName"_MODULES"
	log "**********************************START_${_modName}_MODULES**********************************"
	logv "Capturing running $_modName module tree"
	log "$(lsmod | grep $_modName)"
	log "**********************************END_${_modName}_PROCESSES**********************************"
}


function _captureDmesg() {
	_toc "START_DMESG"
	log "**********************************START_DMESG**********************************"
	logv "Capturing dmesg logs"
	log "DMESG saved to $dmesgLogFileName"
	dmesg -T > $dmesgLogFileName
	log "**********************************END_DMESG**********************************"
}


function _captureJournalctl() {
	_toc "START_JOURNALCTL"
	log "********************************START_JOURNALCTL**********************************"
	logv "Capturing journalctl logs"
	log "JOURNALCTL saved to $journalLogFileName"
	log "JOURNALCTL last boot saved to $lastBootJournalLogFileName"	
	journalctl --boot > $journalLogFileName
	journalctl --boot=-1 > $lastBootJournalLogFileName
	log "********************************END_JOURNALCTL**********************************"
}


function _captureFlashVersion() {
	xpucmd=$(getXPUCmd)
	_toc "START_FLASH_INFO"
	[[ ! -z $tempFile ]] && rm -f $tempFile
	log "**********************************START_FLASH_INFO**********************************"
	if [[ $xpucmd -eq 1 ]]; then
		logv "Error: Missing XPU. Skipping flash version capture."
		_result "XPU_MISSING_FIRMWARE_VERSION_SKIPPED_#WARNING#"
		return 1
	fi
	
	logv "Capturing device flash versions. This may take a while."
	device=0

	logv "Capturing device ID, BDF, FW Version, FW Status, Stepping, Driver version, GFX FW Ver, Dev SN."
	$xpucmd discovery --dump 1 11 9 22 7 8 10 5 > $tempFile &
	_watchdog $!
	versions=$(tail -n +2 "${tempFile}")
	for i in $versions; do
		fwVersion="$fwVersion $(cut -d "," -f 3 <<< $i)"
	done
	
	for version in $fwVersion; do
		logv "Device $device IFWI version $version"
		_result "Device_${device}_IFWI_${version}"
		device=$(($device + 1))
	done
	logv "$versions"
		
	rm -f $tempFile	
	log "**********************************END FLASH INFO**********************************"
}


function _getAMCVersion() {
	_toc "START_AMC_VERSION"
	version=""
	log "**********************************START_AMC_VERSION**********************************"
	logv "Collecting AMC version info."
	if type busybox>/dev/null 2>&1; then
		printf "Busybox found.\n"

		card=0

		BARList=$(lspci -nnvvd:$devString | grep Region\ 0: | grep size | cut -d ' ' -f5 | awk -- 'BEGIN {list=""} {if(list!="") list=list " "; list=list $1} END {print list}')
		
		for address in $BARList; do
			offsetList="$offsetList $(printf "%X" $((0x$address + $_amcOffset)))"
		done
		
		for offset in $offsetList; do
			for i in {0..3}; do 
				version=$version$(busybox devmem 0x$offset 8 | rev | cut -c 1 | rev)
				offset=$(printf "%X" $((0x$offset + 0x4)))
			done
			logv "Device $card AMC version: $version"
			_result "Device_${card}_AMC_${version}"
			version=""
			card=$((card + 1))
		done
	else
		logv "busybox not found, cannot collect version AMC in-band."
	fi
	log "**********************************END_AMC_VERSION**********************************"
}


function _captureClinfo() {
	_toc "START_CLINFO"
	log "**********************************START_CLINFO**********************************"
	logv "Capturing clinfo device list"
	if type clinfo>/dev/null 2>&1; then
		log "$(clinfo -l)"
		found=$(clinfo -l | grep Device | grep $clInfoString | wc -l)
		if [[ $found != $_devCount ]]; then
			_result "ClInfo_Check_Found_${found}_of_${_devCount}_Devs_#FAILED#"
		else
			_result "ClInfo_Check_Found_${found}_of_${_devCount}_Devs_PASSED"
		fi
	else
		logv "clinfo not found, skipping check."
	fi
	log "**********************************END_CLINFO**********************************"
}


function _prereqCheck() {
	prereqs=""
	logv "Info Collector version $version"
#TODO: Need to update this to the new devices.	
	if [[ $_deviceType = "pvc" ]] || [[ $_deviceType = "ats" ]]; then
	
		printf "Checking system prereqs for data collection.\n"
		if ! type clinfo>/dev/null 2>&1; then
			printf "****************************\m"
			logv "clinfo missing."
			printf "****************************\n"
			prereqs="$prereqs clinfo"
		fi
	#TODO: Test with both XPU-smi and xpucli.
		if ! type xpumcli>/dev/null 2>&1 && ! type xpu-smi>/dev/null 2>&1; then
			printf "****************************\n"
			logv "xpum missing."
			printf "****************************\n"
			prereqs="$prereqs xpum"
		fi
	#TODO: Might not need this. Need to check if we can still use it for BMG.
		if ! type sample_multi_transcode>/dev/null 2>&1 && [[ $_deviceType = "ats" ]]; then
			printf "****************************\n"
			logv "sample_multi_transcode missing."
			printf "****************************\n"
			prereqs="$prereqs intel-media-va-driver-non-free libmfx1 libigfxcmrt7 libmfxgen1 libvpl2 libvpl-tools"
		fi
		if ! type numactl>/dev/null 2>&1; then
			printf "****************************\n"
			logv "numactl missing."
			printf "****************************\n"
			prereqs="$prereqs numactl"
		fi
	fi
	if ! type ipmitool>/dev/null 2>&1; then
		printf "****************************\n"
		logv "ipmitool missing."
		printf "****************************\n"
		prereqs="$prereqs ipmitool"
	fi	
}


function _barCheck() {
	_toc "START_BAR_CHECK"
	log "**********************************START_BAR_CHECK**********************************"
	for device in $_EPBDFList; do
		bar0=$(lspci -vvs $device | grep -m1 Region\ 0: | awk -F 'prefetchable) ' '{print $2}')
		bar2=$(lspci -vvs $device | grep -m1 Region\ 2: | awk -F 'prefetchable) ' '{print $2}')
		bar4=$(lspci -vvs $device | grep -m1 Region\ 4: | awk -F 'prefetchable) ' '{print $2}')
		
		if [[ $bar0 = *"$_barSize0"* ]] && [[ $bar2 = *"$_barSize2"* ]] && [[ $bar4 = *"$_barSize4"* ]]; then
			[[ ! -z $_barSize4 ]] && message="and BAR4 ${_barSize4}"
			logv "Device $device PCIe BAR0 $_barSize0 and BAR2 $_barSize2 ${message} check PASSED."
			_result "Device_${device}_BAR_Check_PASSED"
		else
			logv "Error: Device $device BAR size does not match expected."
			logv "BAR0 detected $bar0, expected $_barSize0"
			logv "BAR2 detected $bar2, expected $_barSize2"
			[[ ! -z $_barSize4 ]] && logv "BAR4 detected $bar4, expected $_barSize4"
			_result "Device_${device}_BAR_Check_#FAILED#"
		fi
	done
	log "**********************************END_BAR_CHECK**********************************"
}


function _captureDeviceBindings() {
	_toc "START_"$_modName"_DRIVER_BINDINGS"
	log "**********************************START_${_modName}_DRIVER_BINDINGS**********************************"
	logv "Capturing $_modName device bindings."
	[[ -d $_driverDir ]] && log "$(ls -l $_driverDir | grep 00\:)"
	local bindings=$(ls $_driverDir | grep 00)
	local found=0
	
	logv "Checking device bindings."
	for deviceBDF in $_EPBDFList; do
		found=0
		for driverBind in $bindings; do
			if [[ $deviceBDF = $driverBind ]]; then
				found=1
				break
			fi
		done
		if [[ $found -eq 1 ]]; then
			logv "Device ${deviceBDF} bound to driver."
			_result "Device_${deviceBDF}_Driver_Binding_Check_PASSED"
		else
			logv "Device ${deviceBDF} not bound to driver."
			_result "Device_${deviceBDF}_Driver_Binding_Check_#FAILED#"
		fi
	done
	log "**********************************END_${_modName}_DRIVER_BINDINGS**********************************"
}


function _capturemodInfo () {
	_toc "START_MODINFO"
	log "**********************************START_MODINFO**********************************"
	logv "Capturing $_modName module info."
	log "$(modinfo $_modName)"
	if [[ $(lsmod | grep $_modName) = "" ]]; then
		_result "$_modName_Check_#FAILED#"
	else
		_result "$_modName_Check_PASSED"
	fi
	log "**********************************END_MODINFO**********************************"
}


function _captureBMCSEL() {
	_toc "START_BMC_SEL"
	log "**********************************START_BMC_SEL**********************************"
	logv "Capturing BMC Logs."
	if type ipmitool>/dev/null 2>&1; then 
		log "$(ipmitool sel list)"
	else
		logv "ipmitool not found, skipping."
	fi
	log "**********************************END_BMC_SEL**********************************"
}


function _captureSensors() {
	_toc "START_BMC_SENSORS"
	log "**********************************START_BMC_SENSORS**********************************"
	logv "Capturing BMC sensor data."
	if type ipmitool>/dev/null 2>&1; then 
		log "$(ipmitool sensor list)"
	else
		logv "ipmitool not found, skipping."
	fi
	log "**********************************END_BMC_SENSORS**********************************"
}


function _collectPackages() {
	_toc "START_INTEL_PACKAGES"
	log "**********************************START_INTEL_PACKAGES**********************************"
	if type rpm >/dev/null 2>&1; then
		log "$(rpm -qa | grep -i -e libdrm -e libigc -e libm -e libva -e linux-firmware-ats -e vainfo -e va-driver -e intel -e opencl)"
	elif type apt-cache > /dev/null 2>&1; then
		log "$(dpkg --list | grep -i -e libdrm -e libigc -e libm -e libva -e linux-firmware-ats -e vainfo -e va-driver -e intel -e opencl)"
	fi
	log "**********************************END_INTEL_PACKAGES**********************************"
}


function _captureDmiDecode() {
	log "**********************************START_DMIDECODE**********************************"
	_toc "START_DMIDECODE"
	
	log "$(dmidecode)"
	
	log "**********************************END_DMIDECODE**********************************"
}


function _checkACSStatus() {
	log "**********************************START_ACS_CHECK**********************************"
		_toc "START_ACS_CHECK"
		logv "Checking PCIe ACS status"
	for device in $_EPBDFList; do
		parent=$(ls -lr /sys/bus/pci/devices/ | grep $device | cut -d "/" -f5-100)
		rootPort=$(echo $parent | rev | cut -d "/" -f${_USPLevel} | rev)
		status=$(lspci -vvs $rootPort | grep ACSCtl  | awk '{$1=$1; print}')
	    acsStatus=$(setpci -s $rootPort ecap_acs+6.w)
		
		logv "ACS status for ${device}:"
		logv "Port $rootPort register status: $acsStatus"
		logv "Port $rootPort $status"
		if [[ $acsStatus = "0000" ]]; then
			logv "ACS is DISABLED"
			_result "Device_${device}_ACS_ON_PORT_${rootPort}_IS_DISABLED"
		else 
			_result "Device_${device}_ACS_ON_PORT_${rootPort}_IS_ENABLED"
			logv "ACS is ENABLED"
		fi
		logv "____________________________________________________________________"
	done
	log "**********************************END_ACS_CHECK**********************************"
}

function _USPcheck() {
	_toc "START_USP_LINK_CHECK"
	log "**********************************START_LINK_CHECK**********************************"
	deviceList=$(lspci -nnDd:$_usp | awk -- 'BEGIN {list=""} {if(list!="") list=list " "; list=list $1} END {print list}')
	
	if [[ -z $deviceList ]]; then
		logv "Error: Target devices not detected."
	else
		for i in $deviceList; do
			USPlinkStatus=$(lspci -s "$i" -vvv | grep LnkSta: | cut -d ":" -f 2 | awk '{$1=$1;print}')
			USPlinkCap=$(lspci -s "$i" -vvv | grep LnkCap: | cut -d ":" -f 2 | awk '{$1=$1;print}')
			
			log "**********Device $i link status: ${USPlinkStatus}**********"
			USPspeedStatus=$(echo $USPlinkStatus | cut -d ' ' -f 2 | tr -d ',')
			USPspeedCap=$(echo $USPlinkCap | cut -d ' ' -f 4 | tr -d ',')
			USPlinkWidth=$(echo $USPlinkStatus |  cut -d ',' -f 2 | cut -d ' ' -f 3)
			USPwidthCap=$(echo $USPlinkCap | cut -d ',' -f 3 | cut -d ' ' -f 3)		

			if [[ "$USPspeedStatus" != "$USPspeedCap" ]] || [[ "$USPlinkWidth" != "$USPwidthCap" ]] ||
				[[ "$USPspeedStatus" != "$_linkSpeed" ]] || [[ "$USPlinkWidth" != "$_linkWidth" ]]; then
				logv "*********************Device $i link issue detected*********************"
				logv "Device $i link status is downgraded."
				logv "Speed detected: $USPspeedStatus and link width $USPlinkWidth"
				logv "***********************************************************************"
				_result "Device_${i}_PCI_Link_Check_#FAILED#"
			else
				logv "USP link $i operating at correct speed and width."
				_result "Device_${i}_PCI_Link_Check_PASSED"			
			fi
		done
	fi
	log "**********************************END_USP_LINK_CHECK**********************************"
}

#TODO: Add an index to the end.
function _CaptureAdditionalInfo() {
	NODES_UNDER_PROC=(cpuinfo interrupts iomem meminfo modules mtrr version bus/pci/devices)
	printf "Collecting device debug info from sysfs.\n"
	for node in ${NODES_UNDER_PROC[@]}; do
		_tocExtra "START_${node}"
		logExtra "*******************************START_${node}*******************************"
		result=$(cat /proc/$node)
        logExtra "$result"
		logExtra "*******************************END_${node}*******************************"
    done
}


#TODO: Add an index to the end.
function _captureGPUInfo() {
	I915_DEBUGFS_NODES=(i915_engine_info i915_gpu_info i915_frequency_info i915_gem_objects
					i915_display_info i915_dmc_info gt0/uc/guc_info gt0/uc/huc_info)
    Intel_card_count=0
    num_cards=$(ls /dev/dri/card* | grep -ic card)
    for((card_i=0; card_i<$num_cards; card_i++)); do
        card_name=$(cat /sys/kernel/debug/dri/${card_i}/name | awk '{print $1}')
        if [ "i915" = $card_name ]; then
            logExtra "${PURPLE}Card $card_i is an Intel GPU.${RST_ATTR}"
            Intel_card_count=$((Intel_card_count + 1))

            for node in ${I915_DEBUGFS_NODES[@]}; do
				if [[ -f /sys/kernel/debug/dri/${card_i}/$node ]]; then
				_tocExtra "START_Card_${card_i}_Node${node}"
					logExtra "****************START_Card_${card_i}_Node${node}****************"
					result=$(cat /sys/kernel/debug/dri/${card_i}/$node)
					logExtra "$result"
				fi
            done

            node="$(cat /sys/kernel/debug/dri/${card_i}/gt0/uc/guc_log_dump)"
			_tocExtra "START_Card_${card_i}_guc_log"
            logExtra "********START_Card_${card_i}_guc_log***********"
			logExtra "$node"
            node=/sys/kernel/debug/dri/${card_i}/i915_error_state
            ret=$(head -n 1 $node | grep -ic hang)
            if [[ ${ret} -ge 1 ]]; then
				
                logExtra "********Card_${card_i}_i915_error_state***********"
				_tocExtra "Card_${card_i}_i915_error_state"
				logExtra "$(cat $node )"
            fi
        else
            logExtra "${YELLOW}Card $card_i is not an Intel GPU!${RST_ATTR}"
        fi
    done

    if [[ $Intel_card_count -eq 0 ]]; then
        logExtra "${RED}Intel GPU NOT DETECTED!! (Total detected: ${num_cards})${RST_ATTR}"
    else
        logExtra "${PURPLE}Detected $Intel_card_count Intel Card(s). (Total detected: ${num_cards})${RST_ATTR}"
    fi
}


function _createIndex() {
	logv "******************************START_INDEX******************************"
	logv "$logFileName"
	logv "File contents can be searched by section:"
	for item in $TOC; do
		logv "$item"
	done
	logv "******************************END_INDEX******************************"
	logExtra "******************************START_INDEX******************************"
	logExtra "File contents can be searched by section:"
	for item in $TOCExtra; do
		logExtra "$item"
	done
	logExtra "******************************END_INDEX******************************"
}


function _passFailSummary() {
	logv "**********************************START_DEVICE_SUMMARY**********************************"
	if [[ $(grep FAILED <<< $testResults 2>/dev/null) ]]; then
		_result "########One_Or_More_Checks_FAILED########"
		printf "##########Warning: One ore more tests FAILED########\n"
	elif [[ $(grep WARNING <<< $testResults 2>/dev/null) ]]; then
		_result "#######One_Or_More_Checks_Issued_WARNINGS#######"
		printf "#######One_Or_More_Checks_Issued_WARNINGS#######\n"
	else
		_result "No_Failures_Detected"
	fi
	logv "Device status summary:"
	for i in $testResults; do
		logv "$i"
	done
			
	sync
	logv "**********************************END_DEVICE_SUMMARY**********************************"
	
	if [[ $prereqs != "" ]]; then
		printf "******************************************************************************\n"
		printf "*Note: to collect additional info, install the following packages or programs:\n"
		for item in $prereqs; do
			echo $item			
		done
		printf "******************************************************************************\n"
	fi
}


function _watchdog() {
	pids=$1
	timeout=$watchdogTimeout
	piddead=0
	printf "\n"
	while [[ $piddead = 0 && $timeout>0 ]]; do
		piddead=1
		for i in $pids; do
		 if ps -p $i &>/dev/null; then
			piddead=0
			break
		fi
		done
		printf "Timeout countdown: $timeout  \r"
		timeout=$((timeout-1))
		sleep 1
	done
	printf "\n"
    [[ $timeout = 0 ]] && logv "Process timeout limit reached, killing PIDs" && kill -9 $pids &>/dev/null
	sleep 1
}


function _wrapUp() {

	if [[ $launchPath != $realpath ]] && [[ -d $wrapperLogPath ]]; then
		cp -r $realPath$logFolder $wrapperLogPath
		
		if [[ -d $wrapperLogPath$logFolder ]]; then
			printf "Log files saved to $wrapperLogPath\n"
			rm -rf  $realPath$logFolder
		else
			printf "Error moving logs. Log saved to $realPath$logFolder\n"
		fi
	else
		printf "Detailed log files are located at:\n"
		printf "$logFileName\n"
		printf "$dmesgLogFileName\n"
		printf "$logFileNameExtra\n"
		printf "$journalLogFileName\n"
		printf "$lastBootJournalLogFileName\n"
		printf "Please provide these logs to your Intel representative for analysis.\n"
	fi
}


function main() {
	_rootCheck
	_findDevices
	_prereqCheck
	_openingMessage
	_scriptVer
	_captureBaseboardInfo
	#_PythonCmd #not used ATM.
	_capturemodInfo
	_captureLspci
	_captureDmiDecode
	_captureENV
	_barCheck
	_USPcheck
	_getCmdline
	_numaCheck
	_hugePagesCheck
	_checkIommuPT
	_hostMemQtyCheck
	_captureDeviceBindings
	_captureModules
	_captureDmesg
	_captureJournalctl
	_PCIeTreeLinkCheck
	#_getAMCVersion
	#_captureGPUInfo
	if [[ $_deviceType = "B60" ]] || [[ $_deviceType = "B70" ]] ; then
		_checkACSStatus
		_xpuTempCheck
		_ZEPeakTest
		_captureFlashVersion
		_captureClinfo
		_collectPackages
	fi
	_captureBMCSEL
	_captureSensors
	_CaptureAdditionalInfo
	_createIndex
	_passFailSummary
	_wrapUp
	exit 0
}

main
