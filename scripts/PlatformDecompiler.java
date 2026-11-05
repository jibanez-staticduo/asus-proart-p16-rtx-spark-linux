// Static analysis only: the imported OEM driver is never executed.
//@category N1X
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.Address;
import java.io.*;
import java.util.*;

public class PlatformDecompiler extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1 || args.length > 2) throw new IllegalArgumentException("Output file and optional --arm64-cfg required");
        int normalized = 0;
        if (args.length == 2) {
            if (!args[1].equals("--arm64-cfg")) throw new IllegalArgumentException(args[1]);
            // ARM64 CFG preserves argument registers and x15. Removing only the
            // guard call in this analysis database restores indirect-call arguments.
            // Never export this database as a runnable binary or modify the input.
            List<Address> guards = new ArrayList<>();
            InstructionIterator instructions = currentProgram.getListing().getInstructions(true);
            while (instructions.hasNext()) {
                Instruction instruction = instructions.next();
                Address a = instruction.getAddress();
                if (getInt(a) == 0xd63f0220 && getInt(a.add(4)) == 0xd63f01e0
                    && (getInt(a.subtract(4)) & 0xffc003ff) == 0xf9400231) guards.add(a);
            }
            for (Address a : guards) {
                clearListing(a, a.add(3));
                setBytes(a, new byte[] {0x1f, 0x20, 0x03, (byte)0xd5});
                disassemble(a);
            }
            normalized = guards.size();
            println("Normalized " + normalized + " ARM64 CFG guard calls in the analysis database only");
        }
        DecompInterface decompiler = new DecompInterface();
        if (!decompiler.openProgram(currentProgram)) throw new IOException(decompiler.getLastMessage());
        int count = 0;
        try (PrintWriter writer = new PrintWriter(new File(args[0]))) {
            writer.println("/* Static decompilation of " + currentProgram.getName() + "; inferred types are not a hardware specification. */");
            writer.println("/* Analysis-only ARM64 CFG normalizations: " + normalized + " */");
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext()) {
                monitor.checkCancelled();
                Function function = functions.next();
                if (function.isExternal() || function.isThunk()) continue;
                DecompileResults result = decompiler.decompileFunction(function, 10, monitor);
                writer.println("\n/* Function " + function.getEntryPoint() + " " + function.getName() + " */");
                if (result.decompileCompleted()) writer.println(result.getDecompiledFunction().getC());
                else writer.println("/* FAILED: " + result.getErrorMessage() + " */");
                if (++count % 100 == 0) { writer.flush(); println("Exported " + count + " functions"); }
            }
        } finally { decompiler.dispose(); }
        println("Exported " + count + " functions total");
    }
}
