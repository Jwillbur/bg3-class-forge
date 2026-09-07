#!/usr/bin/env python3
"""Read a DDS file's DXGI format straight from its header - no texconv needed."""
import struct
import sys

# minimal DXGI_FORMAT values we care about
DXGI = {
    98: "BC7_UNORM",
    99: "BC7_UNORM_SRGB",
    71: "BC1_UNORM",
    72: "BC1_UNORM_SRGB",
    77: "BC3_UNORM",
    78: "BC3_UNORM_SRGB",
    28: "R8G8B8A8_UNORM",
    29: "R8G8B8A8_UNORM_SRGB",
}


def read_dds(path):
    with open(path, "rb") as f:
        data = f.read(148)
    if data[:4] != b"DDS ":
        return f"NOT A DDS (magic={data[:4]!r})"
    # DDS_HEADER: magic(4) size(4) flags(4) height(4) width(4) ...
    height, width = struct.unpack_from("<II", data, 12)
    # ddspf (DDS_PIXELFORMAT) starts at absolute offset 76 (magic=4 +
    # header-up-to-ddspf=72). ddspf: size(4)@76 flags(4)@80 fourCC(4)@84
    fourcc = data[84:88]
    if fourcc == b"DX10":
        dxgi_format, = struct.unpack_from("<I", data, 128)
        fmt = DXGI.get(dxgi_format, f"DXGI#{dxgi_format}")
        return f"{width}x{height}  DX10 ext  {fmt}"
    else:
        return f"{width}x{height}  legacy fourCC={fourcc!r}"


for p in sys.argv[1:]:
    print(f"{p}\n  -> {read_dds(p)}")
