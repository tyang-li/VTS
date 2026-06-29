# Copyright (C) 2024-2026 Intel Corporation

def add_arguments(parser):
    parser.add_argument('-mt', help="Monitor Type", choices = ['xpum','dgdiag','None'], default='xpum', dest='mt')
    
    # CPU stress compatibility argument (restored from original implementation)
    parser.add_argument('-cs', help='CPU Stress tool to run in parallel', choices=['None', 'stress-ng', 'ptat'], default='None', dest='cs')
    
    # PCIe downgrade control argument (restored from original implementation)  
    parser.add_argument('-pcie_downgrade', help='Disable PCIe downgrade before test execution (True/False)', type=lambda x: x.lower() == 'true', default=False, dest='pcie_downgrade')
    
    return parser
