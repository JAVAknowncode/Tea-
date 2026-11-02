import os
import sys
import re

class TeaCompiler:
    def __init__(self):
        self.symbol_table = {}
        self.bytecode = []

    def compile_source(self, file_path):
        if not os.path.isabs(file_path):
            base_dir = os.path.dirname(os.path.abspath(__file__))
            file_path = os.path.join(base_dir, file_path)

        try:
            with open(file_path, 'r') as f:
                lines = f.readlines()
        except FileNotFoundError:
            print(f"[Tea Compile Error] Source file not found: '{file_path}'")
            return False

        print(f"Building target: {os.path.basename(file_path)}...")

        for line_num, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            
            if not line or line.startswith("//"):
                continue

            if line_num == 1:
                if line != "import.tea.util;":
                    print(f"[Compile Error on line {line_num}] Invalid bootstrap header!")
                    print(f"  -> Expected: 'import.tea.util;'")
                    print(f"  -> Found:    '{line}'")
                    sys.exit(1)
                else:
                    self.bytecode.append(("BOOTSTRAP", line))
                continue

            if line.startswith("<import") and line.endswith(">;"):
                match = re.search(r'<import\s+(\w+>;)', line)
                if match:
                    lib_token = match.group(1)
                    lib_name = lib_token.split('>;')[0]
                    if not lib_name.endswith('l'):
                        print(f"[Compile Error on line {line_num}] Module '{lib_name}' rejected.")
                        print(f"  -> Rule violation: Custom library names in Tea must end with 'l'.")
                        sys.exit(1)
                    else:
                        self.bytecode.append(("IMPORT", lib_name))
                else:
                    print(f"[Compile Error on line {line_num}] Malformed import statement: '{line}'")
                    sys.exit(1)

            elif line.startswith("iol.print"):
                match = re.search(r'iol\.print\((.*?)\);', line)
                if match:
                    content = match.group(1).strip()
                    self.bytecode.append(("PRINT", content))
                else:
                    print(f"[Compile Error on line {line_num}] Malformed print statement: '{line}'")
                    sys.exit(1)

            elif line.startswith("variable"):
                match = re.search(r'variable\s+(\w+)\s*=\s*(.*?);', line)
                if match:
                    var_name = match.group(1)
                    var_value = match.group(2).strip()
                    self.bytecode.append(("STORE_VAR", var_name, var_value))
                else:
                    print(f"[Compile Error on line {line_num}] Invalid variable declaration syntax: '{line}'")
                    sys.exit(1)
            
            else:
                print(f"[Compile Error on line {line_num}] Unknown instruction: '{line}'")
                sys.exit(1)

        print("Compilation successful. Bytecode generated.")
        return True

    def run_vm(self):
        memory = {}
        print("\n--- TEA Virtual Machine Execution ---")
        for instruction in self.bytecode:
            op = instruction[0]
            
            if op == "BOOTSTRAP":
                pass
            elif op == "IMPORT":
                pass
            elif op == "PRINT":
                content = instruction[1]
                if content.startswith('"') or content.startswith("'"):
                    print(content.strip('"\''))
                elif content in memory:
                    print(memory[content])
                else:
                    print(content)
            elif op == "STORE_VAR":
                var_name, var_value = instruction[1], instruction[2]
                if var_value.isdigit():
                    memory[var_name] = int(var_value)
                else:
                    memory[var_name] = var_value.strip('"\'')
        print("--- Execution Complete ---")

if __name__ == "__main__":
    compiler = TeaCompiler()
    target_file = sys.argv[1] if len(sys.argv) > 1 else "app.tea"
    
    if compiler.compile_source(target_file):
        compiler.run_vm()
