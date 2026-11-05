// Import the captured SCP SRAM as data; seed its observed Cortex-M vector table.
//@category N1X
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.mem.MemoryBlock;
import java.math.BigInteger;

public class PlatformCortexM extends GhidraScript {
    public void run() throws Exception {
        MemoryBlock block = currentProgram.getMemory().getBlock(toAddr(0));
        if (block == null || block.getSize() != 0x80000)
            throw new IllegalArgumentException("Expected the 512 KiB SCP SRAM snapshot at address zero");
        block.setExecute(true);
        if (currentProgram.getListing().getNumInstructions() == 0)
            currentProgram.getProgramContext().setValue(currentProgram.getRegister("TMode"),
                block.getStart(), block.getEnd(), BigInteger.ONE);
        int seeded = 0;
        // Only architectural/system vectors, not arbitrary words in data/heap.
        for (int offset = 4; offset < 0x100; offset += 4) {
            long vector = Integer.toUnsignedLong(getInt(toAddr(offset)));
            if ((vector & 1) == 0 || vector < 0x101 || vector >= 0x13000) continue;
            Address entry = toAddr(vector & ~1L);
            disassemble(entry);
            if (getFunctionAt(entry) == null) createFunction(entry, offset == 4 ? "scp_reset" : null);
            currentProgram.getSymbolTable().addExternalEntryPoint(entry);
            seeded++;
        }
        // The observed firmware's win_ipi_register() at 0x4138 stores a
        // handler/name pair at +0x18/+0x1c in each 0x20-byte IPI table row.
        for (int id = 0; id < 0x33; id++) {
            long handler = Integer.toUnsignedLong(getInt(toAddr(0x14bd8 + id * 0x20 + 0x18)));
            if ((handler & 1) == 0 || handler < 0x101 || handler >= 0x13000) continue;
            Address entry = toAddr(handler & ~1L);
            disassemble(entry);
            if (getFunctionAt(entry) == null) createFunction(entry, "scp_ipi_" + Integer.toHexString(id));
            currentProgram.getSymbolTable().addExternalEntryPoint(entry);
        }
        // Startup/task callbacks identified through literal references, not
        // guessed instruction scanning across the live heap and log areas.
        for (long address : new long[] {0x40a4, 0x41a4, 0x432c, 0x907c}) {
            Address entry = toAddr(address);
            disassemble(entry);
            if (getFunctionAt(entry) == null) createFunction(entry, null);
            currentProgram.getSymbolTable().addExternalEntryPoint(entry);
        }
        if (getFunctionAt(toAddr(0xb974)) != null)
            getFunctionAt(toAddr(0xb974)).setNoReturn(true);
        println("Seeded " + seeded + " observed Thumb vector entries; input file unchanged");
    }
}
