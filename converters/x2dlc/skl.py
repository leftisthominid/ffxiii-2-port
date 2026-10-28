# SEDBSKL skeleton (identical bytes on PC and 360). Only the joint order is needed here:
# 0x30 u32 n_sections, u32 string table offset (rel. 0x60), u32 n_strings, 'lks\0';
# 0x40 sections {u32 name string, u32 offset, u32 size, u32 ?}; 'HierarchyData' = 0xB0-byte joint
# records whose first u32 is the joint's name string index.
import struct

def joints(d):
    nsec, stroff, nstr = struct.unpack_from('<III', d, 0x30)
    names = [s.decode('latin1') for s in d[0x60 + stroff:].split(b'\0')[:nstr]]
    secs = [struct.unpack_from('<IIII', d, 0x40 + 16 * i) for i in range(nsec)]
    hier = [s for s in secs if names[s[0]] == 'HierarchyData'][0]
    return [names[struct.unpack_from('<I', d, 0x60 + hier[1] + 0xB0 * i)[0]] for i in range(hier[2] // 0xB0)]

def parents(d):
    """parent joint index (-1 for the root) per joint: i32 at joint record + 0x38."""
    nsec, stroff, nstr = struct.unpack_from('<III', d, 0x30)
    names = [s.decode('latin1') for s in d[0x60 + stroff:].split(b'\0')[:nstr]]
    secs = [struct.unpack_from('<IIII', d, 0x40 + 16 * i) for i in range(nsec)]
    hier = [s for s in secs if names[s[0]] == 'HierarchyData'][0]
    return [struct.unpack_from('<i', d, 0x60 + hier[1] + 0xB0 * i + 0x38)[0] for i in range(hier[2] // 0xB0)]
