#!/bin/bash
# Copyright (C) 2024-2026 Intel Corporation


function xpumTest () {
	realPath=$(realpath ${BASH_SOURCE[0]})
	realPath="${realPath%/*}/"
	source ${realPath}../../../../common/deviceDetect.sh
	
	xpuTool=$(_detectXPU)
	if [[ $? -ne 0 ]]; then 
		printf "Error: XPU not found. Quitting test.\n"
		return -1
	else
		
		local retCode=""
		local l1Tests=""
		local l2Tests=""
		local l3Tests=""
		local testLevel=""
		local xpuTests=""
		local xpuResult=""
		local testFailed=""
		local testSkipped=""
		local testResult=""
		local testName=""
		local numDevs=""
		
		if [[ $# -eq 1 ]] && [[ "$1" = "health" ]] || [[ "$1" -ge 1 && "$1" -le 3 ]]; then
			testLevel="$1"
		else
			printf "Error: Exactly one integer inout between 1 and 3 or health is required.\n"
			return 1
		fi
		if [[ $(which -s ${xpuTool}) ]]; then
			printf "Error: XPU not found. Install xpu-smi and re-try.\n"
			return 1
		elif [[ $testLevel = "health" ]]; then
			printf "Starting XPUM health test.\n\n"
			xpuResult=$(${xpuTool} health -l)
		else
			printf "Starting XPUM level $testLevel tests.\n\n"
			xpuResult=$(${xpuTool} diag -l "$testLevel")
		fi
		
		printf "Test Result Details:\n${xpuResult}\n\n"
		
		l1Tests="Software Env Variables:Software Library:Software Permission:Software Exclusive:Computation Check"
		l2Tests="${l1Tests}:Integration PCIe:Media Codec"
		l3Tests="${l2Tests}:Performance Computation:Performance Power:Performance Memory Bandwidth:Performance Memory Allocation:Memory Error"
		health="GPU Core Temperature:GPU Memory Temperature:GPU Power:GPU Frequency"
		
		case $testLevel in
			1)
				xpuTests="$l1Tests"
			;;
			2)
				xpuTests="$l2Tests"
			;;
			3)
				xpuTests="$l3Tests"
			;;
			health)
				xpuTests="$health"
				numDevs=$(grep "Device\ ID" <<< "$xpuResult" | wc -l)
			;;
			*)
				printf "Error: Invalid test option. Quitting...\n"
				return 1
			;;
		esac
			
		testFailed=0
		testSkipped=0
		IFS=":"
		
		for testName in $xpuTests; do
			testResult=$(grep "$testName" <<< "$xpuResult")
			if [[ -n "$testResult" ]]; then
				if [[ $testLevel = "health" ]]; then
					if [[ $(grep "OK" <<< "$testResult" | wc -l) -eq "$numDevs" ]]; then
						printf ""$testName" PASSED\n"
					else
						printf ""$testName" FAILED\n"
						testFailed=1
					fi
				else
					if grep -q "Pass" <<< "$testResult"; then
						printf ""$testName" PASSED\n"
					else
						printf ""$testName" FAILED\n"
						testFailed=1
					fi
				fi
			else
				printf ""$testName" was not run. Skipping check.\n"
				testSkipped=1
			fi
		done
		
		unset IFS

		printf "\n"
		if [[ "$testFailed" -eq 1 ]]; then
			printf "At least one test FAILED.\nSee logs for details.\n\n"
			retCode=1
		elif [[ "$testSkipped" -eq 1 ]]; then
			printf "At least one test was SKIPPED.\nSee logs for details.\n\n"
			retCode=1
		else
			printf "All tests PASSED.\n\n"
			retCode=0
		fi
		return $retCode
	fi
}


