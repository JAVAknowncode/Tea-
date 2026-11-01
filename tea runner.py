import sys
import re
import math

class TeaEngine:
    def __init__(self):
        self.variables = {}
        self.imported_libraries = set()

    def run_file(self, file_path):
        try:
            with open(file_path, 'r') as f:
                content = f.read()
        except FileNotFoundError:
            print(f"Error: Could not find Tea file '{file_path}'.")
            return

        lines = [line.strip() for line in content.split('\n') if line.strip() and not line.strip().startswith("//")]

        if not lines:
            print("Error: The .tea file is empty.")
            return

        # Enforce Bootstrap Rule
        expected_bootstrap = "import.tea.util;"
        if lines[0] != expected_bootstrap:
            print(f"Compilation Error: Every .tea file must start with '{expected_bootstrap}'")
            print(f"Found instead: '{lines[0]}'")
            return

        code_lines = []
        for line in lines[1:]:
            if line.startswith("<import") and line.endswith(">;"):
                lib_name = line.replace("<import", "").replace(">;", "").strip()
                if not lib_name.endswith('l'):
                    print(f"Syntax Error: Library name '{lib_name}' must end with 'l'.")
                    return
                self.imported_libraries.add(lib_name)
            else:
                code_lines.append(line)

        print(f"--- [Tea Compiler] Initialized successfully ---")
        print(f"Loaded Modules: {list(self.imported_libraries)}")
        print(f"Executing application instantly...\n")
        
        self.execute_code(code_lines)

    def execute_code(self, lines):
        for line in lines:
            # 1. I/O Library Handlers (iol)
            if line.startswith("iol.print("):
                match = re.search(r'iol\.print\((.*?)\);', line)
                if match:
                    arg = match.group(1).strip()
                    if arg.startswith('"') and arg.endswith('"'):
                        print(arg[1:-1])
                    elif arg in self.variables:
                        print(self.variables[arg])
                    else:
                        print(arg)

            # 2. Variable Declarations & Math/AI Library Hooks
            elif line.startswith("variable "):
                match = re.search(r'variable\s+(\w+)\s*=\s*(.*?);', line)
                if match:
                    var_name = match.group(1)
                    var_val_expr = match.group(2).strip()
                    
                    # Math Library Handler (mathl)
                    if "mathl.power" in var_val_expr and "mathl" in self.imported_libraries:
                        p_match = re.search(r'mathl\.power\((.*?),\s*(.*?)\)', var_val_expr)
                        if p_match:
                            base = float(p_match.group(1))
                            exp = float(p_match.group(2))
                            self.variables[var_name] = math.pow(base, exp)
                    
                    # AI Model Library Handler Placeholder (aill)
                    elif "aill." in var_val_expr and "aill" in self.imported_libraries:
                        # You can expand your AI model evaluation logic here!
                        self.variables[var_name] = f"[AI Model Result for: {var_val_expr}]"
                    
                    # File Library Handler Placeholder (filel)
                    elif "filel." in var_val_expr and "filel" in self.imported_libraries:
                        # You can expand your file handling logic here!
                        self.variables[var_name] = f"[File Operation Result for: {var_val_expr}]"

                    else:
                        try:
                            if '.' in var_val_expr:
                                self.variables[var_name] = float(var_val_expr)
                            else:
                                self.variables[var_name] = int(var_val_expr)
                        except ValueError:
                            self.variables[var_name] = var_val_expr.strip('"')

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tea_runner.py <your_file.tea>")
    else:
        engine = TeaEngine()
        engine.run_file(sys.argv[1])
