# Copyright (C) 2024-2026 Intel Corporation
version = "0.6"

# Standard return/status codes used across tests and orchestration.
STATUS_SUCCESS = 0
STATUS_FAILED = 1
ERROR_EXCEPTION = 2

gpuDidDevicesDict = {'1020':{'family':'gaudi','model':'Intel Gaudi 2'},
                     '1060':{'family':'gaudi','model':'Intel Gaudi 3 OAM'},
                     '1061':{'family':'gaudi','model':'Intel Gaudi 3 PCIe'},
                     '1063':{'family':'gaudi','model':'Intel Gaudi 3 PCIe'},
                     'e20b':{'family':'bmg'  ,'model':'Intel Arc B580 Graphics'},
                     'e211':{'family':'bmg'  ,'model':'Intel Arc B60 Graphics'},
                     'e221':{'family':'bmg'  ,'model':'Intel Arc B60 IBC Graphics'},
                     'e223':{'family':'bmg'  ,'model':'Intel Arc B70 IBC Graphics'}
                    }

# Shared constants for realistic sensor value ranges
# These ranges are used across different test types (xpum, dgdiag) and utilities
# for filtering out unrealistic sensor measurements that could be errors
REALISTIC_POWER_MIN_W = 1.0      # Minimum realistic GPU power in watts
REALISTIC_POWER_MAX_W = 2000.0   # Maximum realistic GPU power in watts
REALISTIC_TEMP_MIN_C = 10.0      # Minimum realistic GPU temperature in Celsius
REALISTIC_TEMP_MAX_C = 200.0     # Maximum realistic GPU temperature in Celsius
REALISTIC_FREQ_MIN_MHZ = 50.0    # Minimum realistic GPU frequency in MHz (GPUs typically operate above 50MHz)
REALISTIC_FREQ_MAX_MHZ = 10000.0 # Maximum realistic GPU frequency in MHz

# PCI Class Code Definitions
# Format: 0xCCSSPP where CC=Base Class, SS=Subclass, PP=Programming Interface
PCI_CLASSES = {
    0x00: {"name": "Unclassified", "subclasses": {
        0x00: "Non-VGA-Compatible Unclassified Device",
        0x01: "VGA-Compatible Unclassified Device"
    }},
    0x01: {"name": "Mass Storage Controller", "subclasses": {
        0x00: "SCSI Storage Controller",
        0x01: "IDE Interface",
        0x02: "Floppy Disk Controller",
        0x03: "IPI Bus Controller",
        0x04: "RAID Controller",
        0x05: "ATA Controller",
        0x06: "Serial ATA Controller",
        0x07: "Serial Attached SCSI Controller",
        0x08: "Non-Volatile Memory Controller",
        0x80: "Other Mass Storage Controller"
    }},
    0x02: {"name": "Network Controller", "subclasses": {
        0x00: "Ethernet Controller",
        0x01: "Token Ring Controller",
        0x02: "FDDI Controller",
        0x03: "ATM Controller",
        0x04: "ISDN Controller",
        0x05: "WorldFip Controller",
        0x06: "PICMG 2.14 Multi Computing",
        0x07: "Infiniband Controller",
        0x08: "Fabric Controller",
        0x80: "Other Network Controller"
    }},
    0x03: {"name": "Display Controller", "subclasses": {
        0x00: "VGA Compatible Controller",
        0x01: "XGA Controller",
        0x02: "3D Controller",
        0x80: "Other Display Controller"
    }},
    0x04: {"name": "Multimedia Controller", "subclasses": {
        0x00: "Video Device",
        0x01: "Audio Device",
        0x02: "Computer Telephony Device",
        0x03: "Mixed Mode Device",
        0x80: "Other Multimedia Controller"
    }},
    0x05: {"name": "Memory Controller", "subclasses": {
        0x00: "RAM Controller",
        0x01: "Flash Controller",
        0x80: "Other Memory Controller"
    }},
    0x06: {"name": "Bridge Device", "subclasses": {
        0x00: "Host Bridge",
        0x01: "ISA Bridge",
        0x02: "EISA Bridge",
        0x03: "MicroChannel Bridge",
        0x04: "PCI-to-PCI Bridge",
        0x05: "PCMCIA Bridge",
        0x06: "NuBus Bridge",
        0x07: "CardBus Bridge",
        0x08: "RACEway Bridge",
        0x09: "PCI-to-PCI Bridge (Subtractive Decode)",
        0x0A: "InfiniBand-to-PCI Host Bridge",
        0x80: "Other Bridge Device"
    }},
    0x07: {"name": "Simple Communication Controller", "subclasses": {
        0x00: "Serial Controller",
        0x01: "Parallel Controller",
        0x02: "Multiport Serial Controller",
        0x03: "Generic Modem",
        0x04: "IEEE 488.1/2 (GPIB) Controller",
        0x05: "Smart Card Controller",
        0x80: "Other Communication Controller"
    }},
    0x08: {"name": "Base System Peripheral", "subclasses": {
        0x00: "PIC",
        0x01: "DMA Controller",
        0x02: "Timer",
        0x03: "RTC",
        0x04: "PCI Hot-Plug Controller",
        0x05: "SD Host Controller",
        0x06: "IOMMU",
        0x80: "Other Base System Peripheral"
    }},
    0x09: {"name": "Input Device Controller", "subclasses": {
        0x00: "Keyboard Controller",
        0x01: "Digitizer Pen Controller",
        0x02: "Mouse Controller",
        0x03: "Scanner Controller",
        0x04: "Gameport Controller",
        0x80: "Other Input Controller"
    }},
    0x0A: {"name": "Docking Station", "subclasses": {
        0x00: "Generic Docking Station",
        0x80: "Other Docking Station"
    }},
    0x0B: {"name": "Processor", "subclasses": {
        0x00: "386",
        0x01: "486",
        0x02: "Pentium",
        0x10: "Alpha",
        0x20: "PowerPC",
        0x30: "MIPS",
        0x40: "Co-Processor",
        0x80: "Other Processor"
    }},
    0x0C: {"name": "Serial Bus Controller", "subclasses": {
        0x00: "FireWire (IEEE 1394)",
        0x01: "ACCESS Bus",
        0x02: "SSA",
        0x03: "USB Controller",
        0x04: "Fibre Channel",
        0x05: "SMBus",
        0x06: "InfiniBand",
        0x07: "IPMI Interface",
        0x08: "SERCOS Interface",
        0x09: "CANbus",
        0x80: "Other Serial Bus Controller"
    }}
}
