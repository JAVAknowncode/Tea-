import os
import sys
import re
import math

# ==========================================
# Tea Standard Libraries
# ==========================================
class IOLibrary:
    @staticmethod
    def print_msg(msg):
        print(f"[IOL Output] {msg}")

    @staticmethod
    def read_input(prompt=""):
        return input(str(prompt))

class MathLibrary:
    @staticmethod
    def sqrt(x):
        return math.sqrt(float(x))

    @staticmethod
    def power(base, exp):
        return math.pow(float(base), float(exp))

    @staticmethod
    def absolute(x):
        return abs(float(x))

class AILibrary:
    @staticmethod
    def process_logic(val):
        return f"[AILL Engine] Processed intelligence metric: {val}"

    @staticmethod
    def evaluate_condition(flag):
        return bool(flag)

# ==========================================
# Tea Compiler + VM (with Native Array Lists)
# ==========================================
class TeaCompiler:
    def __init__(self):
        self.errors = []
        self.imported_libs = set()
        self.functions = {}
        self.libraries = {
            "iol": IOLibrary,
            "mathl": MathLibrary,
            "aill": AILibrary
        }

    def compile_source(self, file_path):
        if not os.path.isabs(file_path):
            base_dir = os.path.dirname(os.path.abspath(__file__))
            file_path = os.path.join(base_dir, file_path)

        try:
            with open(file_path, 'r') as f:
                lines = [line.expandtabs(4) for line in f.readlines()]
        except FileNotFoundError:
            print(f"[Tea Compile Error] Source file not found: '{file_path}'")
            return False

        print(f"Building target: {os.path.basename(file_path)}...")
        bytecode, _ = self.parse_block(lines, 0, top_level=True)

        if self.errors:
            print(f"\n[Compilation Failed] Found {len(self.errors)} error(s):")
            for err in self.errors:
                print(f"  -> {err}")
            return False

        print("Compilation successful. Array-enabled bytecode generated.")
        return bytecode

    def parse_block(self, lines, start_index, top_level=False):
        bytecode = []
        i = start_index
        n = len(lines)

        while i < n:
            line_num = i + 1
            line = lines[i].strip()

            if not line or line.startswith("//"):
                i += 1
                continue

            # 1. Bootstrap Header
            if top_level and line_num == 1:
                if line != "import.tea.util;":
                    self.errors.append(f"Line {line_num}: Invalid bootstrap header! Expected 'import.tea.util;', found '{line}'")
                else:
                    bytecode.append(("BOOTSTRAP",))
                i += 1
                continue

            # 2. Imports
            import_match = re.match(r'^<\s*import\s+([a-zA-Z_]\w*)\s*>;$', line)
            if import_match:
                lib_name = import_match.group(1)
                if not lib_name.endswith('l'):
                    self.errors.append(f"Line {line_num}: Library '{lib_name}' must end with 'l'")
                else:
                    self.imported_libs.add(lib_name)
                    bytecode.append(("IMPORT", lib_name))
                i += 1
                continue

            # 3. Function definition
            func_match = re.match(r'^function\s+([a-zA-Z_]\w*)\s*\((.*?)\)\s*\{\s*$', line)
            if func_match:
                func_name = func_match.group(1)
                params = [p.strip() for p in func_match.group(2).split(',') if p.strip()]
                i, body = self.extract_and_parse_block(lines, i + 1)
                self.functions[func_name] = {"params": params, "body": body}
                bytecode.append(("FUNC_DEF", func_name))
                continue

            # 4. Print Statement
            print_match = re.match(r'^iol\.print\s*\((.*?)\)\s*;\s*$', line)
            if print_match:
                bytecode.append(("PRINT", print_match.group(1).strip()))
                i += 1
                continue

            # 5. Variable Declaration (Supports lists like variable myList = [1, 2, 3];)
            var_match = re.match(r'^variable\s+([a-zA-Z_]\w*)\s*=\s*(.*?)\s*;\s*$', line)
            if var_match:
                bytecode.append(("STORE_VAR", var_match.group(1), var_match.group(2).strip()))
                i += 1
                continue

            # 6. Return Statement
            return_match = re.match(r'^return\s+(.*?)\s*;\s*$', line)
            if return_match:
                bytecode.append(("RETURN", return_match.group(1).strip()))
                i += 1
                continue

            # 7. IF Statement
            if_match = re.match(r'^if\s*\((.*?)\)\s*\{\s*$', line)
            if if_match:
                condition = if_match.group(1).strip()
                i, if_body = self.extract_and_parse_block(lines, i + 1)
                else_body = []
                if i < n and lines[i].strip().startswith("else"):
                    i, else_body = self.extract_and_parse_block(lines, i + 1)
                bytecode.append(("IF", condition, if_body, else_body))
                continue

            # 8. LOOPS Statement
            loops_match = re.match(r'^loops\s*\((.*?)\)\s*\{\s*$', line)
            if loops_match:
                condition = loops_match.group(1).strip()
                i, body = self.extract_and_parse_block(lines, i + 1)
                bytecode.append(("LOOPS", condition, body))
                continue

            # 9. Function call or Array index assignment statement
            call_match = re.match(r'^([a-zA-Z_]\w*)\s*\((.*?)\)\s*;\s*$', line)
            if call_match:
                bytecode.append(("CALL", call_match.group(1), call_match.group(2).strip()))
                i += 1
                continue

            if line == "}":
                i += 1
                break

            self.errors.append(f"Line {line_num}: Unknown instruction: '{line}'")
            i += 1

        return bytecode, i

    def extract_and_parse_block(self, lines, start_index):
        body, next_i = self.parse_block(lines, start_index, top_level=False)
        return next_i, body

    def evaluate_expression(self, expr, memory_stack):
        expr = expr.strip()
        if not expr:
            return None

        # 1. Array Literal Evaluation (e.g., [10, 20, "tea"])
        if expr.startswith('[') and expr.endswith(']'):
            inner = expr[1:-1].strip()
            if not inner:
                return []
            elements = []
            # Split items safely respecting commas
            for item in inner.split(','):
                elements.append(self.evaluate_expression(item.strip(), memory_stack))
            return elements

        # 2. Array Index Access Lookup (e.g., myList[0])
        index_match = re.match(r'^([a-zA-Z_]\w*)\s*\[\s*(.*?)\s*\]$', expr)
        if index_match:
            arr_name, idx_expr = index_match.groups()
            flat = {}
            for scope in memory_stack:
                flat.update(scope)
            if arr_name in flat:
                arr = flat[arr_name]
                idx = self.evaluate_expression(idx_expr, memory_stack)
                if isinstance(arr, list) and isinstance(idx, (int, float)):
                    try:
                        return arr[int(idx)]
                    except IndexError:
                        return f"[Error] Index {idx} out of range for array '{arr_name}'"
            return f"[Error] Invalid array reference '{arr_name}'"

        # 3. String Literal
        if (expr.startswith('"') and expr.endswith('"')) or (expr.startswith("'") and expr.endswith("'")):
            return expr[1:-1]

        # 4. Pure Number
        if re.match(r'^-?\d+(\.\d+)?$', expr):
            return float(expr) if '.' in expr else int(expr)

        # 5. Library Function Call
        lib_call = re.match(r'^([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)\((.*)\)$', expr)
        if lib_call:
            lib_name, func_name, args_str = lib_call.groups()
            if lib_name not in self.imported_libs:
                return f"[Error] Library '{lib_name}' not imported"
            lib_class = self.libraries.get(lib_name)
            if not lib_class or not hasattr(lib_class, func_name):
                return f"[Error] Unknown function {lib_name}.{func_name}"
            args = [self.evaluate_expression(a.strip(), memory_stack) for a in args_str.split(',') if a.strip()]
            try:
                return getattr(lib_class, func_name)(*args)
            except Exception as e:
                return f"[Error] {e}"

        # 6. User Function Call
        user_call = re.match(r'^([a-zA-Z_]\w*)\((.*)\)$', expr)
        if user_call:
            fname, args_str = user_call.groups()
            if fname in self.functions:
                return self.call_user_function(fname, args_str, memory_stack)

        # 7. Variable Lookup
        flat = {}
        for scope in memory_stack:
            flat.update(scope)

        if expr in flat:
            return flat[expr]

        # 8. Arithmetic & Comparisons
        try:
            allowed = set("0123456789+-*/()=<>! .")
            if all(c in allowed or c.isalnum() or c == '_' for c in expr):
                safe = expr.replace("==", "___EQ___").replace("=", "==").replace("___EQ___", "==")
                return eval(safe, {"__builtins__": {}}, flat)
        except:
            pass

        return expr

    def call_user_function(self, name, args_str, memory_stack):
        func = self.functions[name]
        args = [self.evaluate_expression(a.strip(), memory_stack) for a in args_str.split(',') if a.strip()]

        if len(args) != len(func["params"]):
            return f"[Error] Function {name} expects {len(func['params'])} arguments"

        local_scope = dict(zip(func["params"], args))
        memory_stack.append(local_scope)

        result = None
        for instr in func["body"]:
            if instr[0] == "RETURN":
                result = self.evaluate_expression(instr[1], memory_stack)
                break
            self.execute_instruction(instr, memory_stack)

        memory_stack.pop()
        return result

    def execute_instruction(self, instruction, memory_stack):
        op = instruction[0]

        if op == "PRINT":
            print(self.evaluate_expression(instruction[1], memory_stack))

        elif op == "STORE_VAR":
            var_name, var_expr = instruction[1], instruction[2]
            val = self.evaluate_expression(var_expr, memory_stack)
            memory_stack[-1][var_name] = val

        elif op == "IF":
            cond = self.evaluate_expression(instruction[1], memory_stack)
            memory_stack.append({})
            if cond:
                self.execute_bytecode(instruction[2], memory_stack)
            elif instruction[3]:
                self.execute_bytecode(instruction[3], memory_stack)
            memory_stack.pop()

        elif op == "LOOPS":
            while self.evaluate_expression(instruction[1], memory_stack):
                memory_stack.append({})
                self.execute_bytecode(instruction[2], memory_stack)
                memory_stack.pop()

        elif op == "CALL":
            self.evaluate_expression(f"{instruction[1]}({instruction[2]})", memory_stack)

        elif op == "RETURN":
            pass

    def execute_bytecode(self, bytecode, memory_stack):
        for instr in bytecode:
            if instr[0] in ("BOOTSTRAP", "IMPORT", "FUNC_DEF"):
                continue
            self.execute_instruction(instr, memory_stack)

    def run_vm(self, bytecode):
        memory_stack = [{}]
        print("\n--- TEA Array Virtual Machine Execution ---")
        self.execute_bytecode(bytecode, memory_stack)
        print("--- Execution Complete ---")

if __name__ == "__main__":
    compiler = TeaCompiler()
    target = sys.argv[1] if len(sys.argv) > 1 else "app.tea"
    bc = compiler.compile_source(target)
    if bc:
        compiler.run_vm(bc)
