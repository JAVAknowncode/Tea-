import os
import sys
import re

class TeaEngine:
    def __init__(self):
        self.variables = {}
        self.imported_libraries = set()

    def run_file(self, file_path):
        if not os.path.isabs(file_path):
            base_dir = os.path.dirname(os.path.abspath(__file__))
            file_path = os.path.join(base_dir, file_path)

        try:
            with open(file_path, 'r') as f:
                content = f.read()
        except FileNotFoundError:
            print(f"Error: Could not find Tea file at '{file_path}'.")
            return

        lines = [line.strip() for line in content.split('\n') if line.strip() and not line.strip().startswith("//")]
        
        print(f"Successfully compiled and loaded: {os.path.basename(file_path)}")
        
        for line in lines:
            if line == "import.tea.util;":
                print("[Validator] Bootstrap header 'import.tea.util;' verified.")
            
            elif line.startswith("<import") and line.endswith(">;"):
                match = re.search(r'<import\s+(\w+>;)', line)
                if match:
                    lib_name = match.group(1).split('>;')[0]
                    if not lib_name.endswith('l'):
                        print(f"Syntax Error: Module '{lib_name}' rejected. Library names must end with the letter 'l'.")
                        sys.exit(1)
                    else:
                        self.imported_libraries.add(lib_name)
                        print(f"[Compiler] Imported module successfully: <import {lib_name}>;")

            elif line.startswith("iol.print"):
                match = re.search(r'iol\.print\((.*?)\);', line)
                if match:
                    content_to_print = match.group(1).strip()
                    if content_to_print.startswith('"') or content_to_print.startswith("'"):
                        print(content_to_print.strip('"\''))
                    elif content_to_print in self.variables:
                        print(self.variables[content_to_print])
                    else:
                        print(content_to_print)

            elif line.startswith("variable"):
                match = re.search(r'variable\s+(\w+)\s*=\s*(.*?);', line)
                if match:
                    var_name = match.group(1)
                    var_value = match.group(2).strip()
                    if var_value.isdigit():
                        self.variables[var_name] = int(var_value)
                    else:
                        self.variables[var_name] = var_value.strip('"\'')
                    print(f"[Compiler] Declared variable: {var_name} = {self.variables[var_name]}")

if __name__ == "__main__":
    engine = TeaEngine()
    target_file = sys.argv[1] if len(sys.argv) > 1 else "app.tea"
    engine.run_file(target_file)
