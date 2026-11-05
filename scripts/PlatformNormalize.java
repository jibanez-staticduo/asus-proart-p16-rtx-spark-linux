// Analysis database only. Original file and live hardware remain untouched.
//@category N1X
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.mem.MemoryBlock;

public class PlatformNormalize extends GhidraScript {
    public void run() throws Exception {
        int count = 0;
        for (MemoryBlock block : currentProgram.getMemory().getBlocks()) {
            if (!block.isExecute() || !block.isInitialized()) continue;
            for (long offset = 4; offset + 8 <= block.getSize(); offset += 4) {
                Address a = block.getStart().add(offset);
                if (getInt(a) != 0xd63f0220 || getInt(a.add(4)) != 0xd63f01e0
                    || (getInt(a.subtract(4)) & 0xffc003ff) != 0xf9400231) continue;
                clearListing(a, a.add(3));
                setBytes(a, new byte[] {0x1f, 0x20, 0x03, (byte)0xd5});
                count++;
            }
        }
        println("Analysis-only ARM64 CFG normalizations before analysis: " + count);
    }
}
