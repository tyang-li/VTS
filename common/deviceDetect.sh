#!/bin/bash


# Initialize global deviceID variable from command line argument

function _findDevices() {
	if [[ ! -z $1 ]]; then
		local deviceID="$1"
	else
		printf "Error: Device ID required.\n"
		return 1
	fi

	local deviceEndPointIDs="8086:${deviceID}"
	local _EPBDFList=""
	local devString=""
	
	for i in $deviceEndPointIDs; do
		if [[ $(lspci -d "$i") ]]; then
			devString="$i"
			break
		fi
	done

	if [[ -n "$devString" ]]; then
		_devCount=$(lspci -d "$devString" | wc -l)
		_EPBDFList=$(lspci -nDd "$devString" | awk -- 'BEGIN {list=""} {if(list!="") list=list " "; list=list $1} END {print list}')
	fi

	#Returns nothing if no devices are detected.
	echo "$_EPBDFList"
	return 0
}


function _procWatch() {
	local pids=$1
	local timeout=90
	local piddead=0

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

	if [[ $timeout = 0 ]]; then
	       echo "Process timeout limit reached, killing PIDs" && kill -9 $pids &>/dev/null
	       return 1
        else
	       return 0
	fi
}


function _findParents() {
	local USPLevel=3
	[[ -n $2 ]] && USPLevel=$((USPLevel + $2))
	if [[ -n $1 ]]; then
                local deviceID="$1"
        else
                printf "Error: Device ID required.\n"
                return 1
        fi
	local BDFList="$(_findDevices $deviceID)"
	local parentList=""
	local parent
	local rootPort

	for endPointbdf in $BDFList; do
		parent=$(ls -lr /sys/bus/pci/devices/ | grep $endPointbdf | cut -d "/" -f5-100)
	        rootPort=$(echo $parent | rev | cut -d "/" -f$USPLevel | rev)
		parentList=$parentList" "$rootPort
	done
	
	echo "$parentList"
	return 0
}


function _checkPeers(){
	local device=""
	local basePort=""
	local truncList=""
	local numDevices=""
	local switchDomains=1
	local compItem=""

	if [[ -n $1 ]]; then
		device=$1
		basePort=$(_findParents $device 1)
		numDevices=$(wc -w <<< $basePort)
	else
		printf "Error: device ID require. \n"
		return -1
	fi

	if [[ $numDevices -gt 1 ]]; then
		for dev in $basePort; do
			truncList=$(sort <<< $truncList)
			truncList="$truncList $(cut -d ":" -f 1-2 <<< $dev)"
		done
		read -a truncList <<< "$truncList"
	
		compItem=${truncList[0]}
		for (( targetDev=1; targetDev < numDevices; targetDev++ )); do
			if [[ $compItem != ${truncList[$targetDev]} ]]; then
				let "switchDomains++"
				compItem="${truncList[$targetDev]}"
			fi
		done
	else
		printf "Only one device detected. No Peer-to-Peer testing available."
		return 1
	fi
	printf "$numDevices Devices found across ${switchDomains} switch domain(s)\n"
	return 0
}


function _detectXPU() {
	if type xpu-smi>/dev/null 2>&1; then
		echo "xpu-smi"
		return 0
	elif type xpumcli>/dev/null 2>&1; then
		echo "xpumcli";
		return 0
	else
		echo "Error: XPU missing"
		return 1
	fi
}


function _checkRunlevel() {
        if systemctl list-units --type target --state active | grep Graphical > /dev/null; then
                return 1  # System is in graphical mode
        else
                return 0  # System is in multi-user target
        fi
}


function _setMultiUserTarget() {
        # Check if we're already in multi-user target
        if ! systemctl list-units --type target --state active | grep Graphical > /dev/null; then
                return 0  # Already in multi-user target
        fi
        
        # Switch to multi-user target
        if systemctl isolate multi-user.target > /dev/null 2>&1; then
                return 0  # Success
        else
                return 1  # Failed to switch
        fi
}


function _PCIeTreeLinkCheck() {
	if [[ ! -z $1 ]]; then
		local deviceID="$1"
	else
		printf "Error: Device ID required.\n"
		return 1
	fi
	local fullTree=""
	local busTree=""
	local linkStatus=""
	local retCode=""
	local downDev=""
	local did=""
	local _EPBDFList="$(_findDevices ${deviceID})"
	
	printf "******************************PCI_TREE_LINK_CHECK*****************************\n"

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
		printf "______________Device $device PCIe tree link status:______________\n"
		printf "$linkStatus\n"
		if grep -i downgraded <<< "$linkStatus" >/dev/null; then
			downDev="$(grep downgraded <<< "$linkStatus" | awk '{print $2}')"
			downDev=$(tr -s '\n' '_' <<< $downDev)
			printf "****Device ${device} PCI tree link downgrade detected on ${downDev} ##FAILED##****\n"
			retCode=1
		else
			printf "****Device ${device} PCI tree link check ##PASSED##****\n"
			retCode=0
		fi
	done
	printf "******************************END_PCI_TREE_LINK_CHECK*****************************\n"
	return $retCode
}


function unbindDevices() {
	local _EPBDFList="$1"
	local _devDriver="$2"

	for i in $_EPBDFList; do
		if [ -d /sys/bus/pci/drivers/${_devDriver}/$i ]; then
			printf "Unbinding $i\n"
			echo $i > /sys/bus/pci/drivers/${_devDriver}/unbind 
		fi
	done

	for i in $_EPBDFList; do
		if [ -d /sys/bus/pci/drivers/${_devDriver}/$i ]; then
			printf "Error: Device $i failed to unbind.\n"
			return 1
		fi
	done
	sleep 1
	return 0
}


function bindDevices() {
	local maxRetry=2
	local retryCount=0
	local sleeptime=3
	local _EPBDFList="$1"
	local _devDriver="$2"

	for DEV in $_EPBDFList; do
		if [[ ! -d /sys/bus/pci/drivers/${_devDriver}/$DEV ]]; then
			printf "binding $DEV\n"
			echo $DEV > /sys/bus/pci/drivers/${_devDriver}/bind
		fi
	done
	
	for DEV in $_EPBDFList; do
		while [[ ! -d /sys/bus/pci/drivers/${_devDriver}/$DEV ]] &&
		[[ $maxRetry -gt $retryCount ]]; do
			printf "Device $DEV did not bind. Waiting $sleeptime seconds before re-checking.\n"
			sleep $sleeptime
			let "retryCount++"
		done

		if [[ ! -d /sys/bus/pci/drivers/${_devDriver}/$DEV ]]; then 
			printf "Device $DEV failed to bind. Some devices may be unavailable and a system reboot is required to restore device functionality.\n"
			return 1
		else
			printf "Device rebind successfull.\n"
		fi
	done
}



function _FLR() {
        if [[ ! -z $1 ]]; then
                local deviceID="$1"
        else
                printf "Error: Device ID required.\n"
                return 1
        fi
	local resetList=$(_findDevices $1)
	local count=0
	local _numDevs=$(wc -w <<< $resetList)
	local _devDriver="xe"
	local _EPBDFList=$(_findDevices)
	unbindDevices "$resetList" "$_devDriver"


	for flrTarget in $resetList; do
		printf "Issuing FLR on device $flrTarget\n"
		{
			echo 1 > /sys/bus/pci/devices/$flrTarget/reset
			if [ $? != 0 ]; then
			printf "Error: Device $flrTarget failed to reset.\n"
			bindDevices
			return 1
			fi
		}
		sleep .5
	done

	printf "\n"
	sleep 1
	bindDevices "$resetList" "$_devDriver"

	targets=$(ls /sys/bus/pci/drivers/$_devDriver/ | grep 000)
	for targetDev in $targets; do
		if [[ $_EPBDFList =~ $targetDev ]]; then
			resetList="$targetDev"" ""$resetList"
			let "count++"
		fi
	done

	if [[ $_numDevs -ne $count ]]; then
		printf "************************************************************************\n"
		printf "Fatal Error: FLR Device count mismatch. Some FLR devices may be unbound.\n"
		printf "Expected $numDevs, found $count.\n"
		printf "************************************************************************\n"
		return 1
	else
		printf "FLR cycle successful on all devices.\n"
	fi
	return
}


function _getPCIeSpeed() {
	if [[ -z $1 ]]; then
		printf "Error: Device DBDF required.\n"
		return -1
	fi
	local device=$1
	local speed
	speed=$(_speedLookup $(setpci -s $device CAP_EXP+12.w))
	speed=$(( 0x$speed & 0xF ))
	echo "$speed"
	return 0
}


function _getPCIeWidth() {
	local device=$1
	if [[ -z $1 ]]; then
		printf "Error: Device DBDF required.\n"
		return -1
	fi
	local width
	width=$(setpci -s $device CAP_EXP+12.w)
	width=$((( 0x$width & 0x3F0 ) >> 4 ))
	echo "x${width}"
	return 0
}


function _speedLookup() {
	if [[ -z $1 ]]; then
		printf "Error: Speed value is null\n"
		return -1
	fi
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
			printf "Fatal error: Could not determine link speed.\n"
			return 1
		;;
	esac
	return 0
}
