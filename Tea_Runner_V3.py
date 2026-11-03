import os
import sys
import re
import math
import tkinter as tk

# ==========================================
# Tea Standard Libraries
# ==========================================
class IOLibrary:
    @staticmethod
    def print_msg(msg):
        print(f"[IOL] {msg}")

    @staticmethod
    def read_input(prompt=""):
        return input(str(prompt))

class MathLibrary:
    @staticmethod
    def sqrt(x):
        try:
            val = float(x)
            if val < 0:
                raise ValueError("Cannot calculate square root of negative number")
            return math.sqrt(val)
        except Exception as e:
            raise TeaRuntimeError(f"mathl.sqrt() failed: {e}")

    @staticmethod
    def power(base, exp):
        try:
            return math.pow(float(base), float(exp))
        except Exception as e:
            raise TeaRuntimeError(f"mathl.power() failed: {e}")

    @staticmethod
    def absolute(x):
        try:
            return abs(float(x))
        except Exception as e:
            raise TeaRuntimeError(f"mathl.absolute() failed: {e}")

class AILibrary:
    @staticmethod
    def process_logic(val):
        return f"[AILL] Processed: {val}"

    @staticmethod
    def evaluate_condition(flag):
        return bool(flag)

class GraphicLibrary:
    _root = None
    _canvas = None
    _width = 400
    _height = 400

    @staticmethod
    def _init_window():
        if GraphicLibrary._root is None:
            try:
                GraphicLibrary._root = tk.Tk()
                GraphicLibrary._root.title("Tea Language Graphic Engine")
                GraphicLibrary._canvas = tk.Canvas(GraphicLibrary._root, width=GraphicLibrary._width, height=GraphicLibrary._height, bg="black")
                GraphicLibrary._canvas.pack()
            except Exception as e:
                raise TeaRuntimeError(f"graphicl failed to initialize Tkinter window: {e}")

    @staticmethod
    def window(width, height):
        GraphicLibrary._width = int(width)
        GraphicLibrary._height = int(height)
        GraphicLibrary._init_window()
        return "[GRAPHICL] Window initialized successfully."

    @staticmethod
    def draw_rect(x, y, w, h, color):
        GraphicLibrary._init_window()
        GraphicLibrary._canvas.create_rectangle(float(x), float(y), float(x) + float(w), float(y) + float(h), fill=str(color), outline="")
        GraphicLibrary._root.update()
        return "[GRAPHICL] Rectangle drawn."

    @staticmethod
    def draw_circle(x, y, r, color):
        GraphicLibrary._init_window()
        r = float(r)
        x, y = float(x), float(y)
        GraphicLibrary._canvas.create_oval(x - r, y - r, x + r, y + r, fill=str(color), outline="")
        GraphicLibrary._root.update()
        return "[GRAPHICL] Circle drawn."

    @staticmethod
    def clear():
        if GraphicLibrary._canvas:
            GraphicLibrary._canvas.delete("all")
        return "[GRAPHICL] Canvas cleared."

class GameLibrary:
    _initialized = False
    _screen = None
    _clock = None
    _running = False

    @staticmethod
    def init():
        try:
            import pygame
            pygame.init()
            GameLibrary._initialized = True
            GameLibrary._clock = pygame.time.Clock()
            return "[GAMEL] Pygame subsystem initialized successfully."
        except ImportError:
            raise TeaRuntimeError("gamel requires 'pygame' to be installed. Run 'pip install pygame'.")
        except Exception as e:
            raise TeaRuntimeError(f"gamel initialization error: {e}")

    @staticmethod
    def set_mode(width, height, title="Tea Game"):
        if not GameLibrary._initialized:
            GameLibrary.init()
        try:
            import pygame
            GameLibrary._screen = pygame.display.set_mode((int(width), int(height)))
            pygame.display.set_caption(str(title))
            GameLibrary._running = True
            return f"[GAMEL] Display mode set to {width}x{height}."
        except Exception as e:
            raise TeaRuntimeError(f"gamel set_mode failed: {e}")

    @staticmethod
    def clear_screen(r=0, g=0, b=0):
        if GameLibrary._screen:
            GameLibrary._screen.fill((int(r), int(g), int(b)))
        return "[GAMEL] Screen cleared."

    @staticmethod
    def update():
        if GameLibrary._screen:
            try:
                import pygame
                pygame.display.flip()
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        GameLibrary._running = False
                        pygame.quit()
            except Exception as e:
                raise TeaRuntimeError(f"gamel update failed: {e}")
        return GameLibrary._running

# ==========================================
# Custom Exceptions
# ==========================================
class TeaSyntaxError(Exception):
    def __init__(self, message, line_num, file_name):
        super().__init__(f"\n[Tea Syntax Error] {file_name}:{line_num}\n  → {message}")

class TeaRuntimeError(Exception):
    def __init__(self, message, line_num=None, file_name=None):
        location = f" {file_name}:{line_num}" if line_num else ""
        super().__init__(f"\n[Tea Runtime Error]{location}\n  → {message}")

# ==========================================
# Tea Compiler + VM
# ==========================================
class TeaCompiler:
    def __init__(self):
        self.errors = []
        self.imported_libs = set()
        self.functions = {}
        self.libraries = {
            "iol": IOLibrary,
            "mathl": MathLibrary,
            "aill": AILibrary,
            "graphicl": GraphicLibrary,
            "gamel": GameLibrary
        }
        self.current_file = "app.tea"

    def compile_source(self, file_path):
        if not os.path.isabs(file_path):
            base_dir = os.path.dirname(os.path.abspath(__file__))
            file_path = os.path.join(base_dir, file_path)

        self.current_file = os.path.basename(file_path)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = [line.expandtabs(4) for line in f.readlines()]
        except FileNotFoundError:
            raise TeaSyntaxError(f"File not found: {file_path}", 0, self.current_file)

        print(f"Building: {self.current_file} ...")
        bytecode, _ = self.parse_block(lines, 0, top_level=True)

        if self.errors:
            print(f"\n[Compilation Failed] {len(self.errors)} error(s):")
            for err in self.errors:
                print(err)
            return None

        print("Compilation successful.")
        return bytecode

    def parse_block(self, lines, start, top_level=False):
        bytecode = []
        i = start
        n = len(lines)

        while i < n:
            line_num = i + 1
            line = lines[i].strip()

            if not line or line.startswith("//"):
                i += 1
                continue

            # Bootstrap
            if top_level and line_num == 1:
                if line != "import.tea.util;":
                    self.errors.append(TeaSyntaxError(
                        f"Invalid bootstrap. Expected 'import.tea.util;', found '{line}'",
                        line_num, self.current_file
                    ))
                else:
                    bytecode.append(("BOOTSTRAP", line_num))
                i += 1
                continue

            # Import
            m = re.match(r'^<\s*import\s+([a-zA-Z_]\w*)\s*>;$', line)
            if m:
                lib = m.group(1)
                if not lib.endswith("l"):
                    self.errors.append(TeaSyntaxError(
                        f"Library '{lib}' must end with 'l'", line_num, self.current_file
                    ))
                else:
                    self.imported_libs.add(lib)
                    bytecode.append(("IMPORT", lib, line_num))
                i += 1
                continue

            # Function
            m = re.match(r'^function\s+([a-zA-Z_]\w*)\s*\((.*?)\)\s*\{\s*$', line)
            if m:
                name = m.group(1)
                params = [p.strip() for p in m.group(2).split(",") if p.strip()]
                body, i = self.extract_block(lines, i + 1)
                self.functions[name] = {"params": params, "body": body}
                bytecode.append(("FUNC_DEF", name, line_num))
                continue

            # Print
            m = re.match(r'^iol\.print\s*\((.*?)\)\s*;\s*$', line)
            if m:
                bytecode.append(("PRINT", m.group(1).strip(), line_num))
                i += 1
                continue

            # Variable
            m = re.match(r'^variable\s+([a-zA-Z_]\w*)\s*=\s*(.*?)\s*;\s*$', line)
            if m:
                bytecode.append(("STORE", m.group(1), m.group(2).strip(), line_num))
                i += 1
                continue

            # Return
            m = re.match(r'^return\s+(.*?)\s*;\s*$', line)
            if m:
                bytecode.append(("RETURN", m.group(1).strip(), line_num))
                i += 1
                continue

            # If
            m = re.match(r'^if\s*\((.*?)\)\s*\{\s*$', line)
            if m:
                cond = m.group(1).strip()
                if_body, i = self.extract_block(lines, i + 1)
                else_body = []
                if i < n and lines[i].strip().startswith("else"):
                    else_body, i = self.extract_block(lines, i + 1)
                bytecode.append(("IF", cond, if_body, else_body, line_num))
                continue

            # Loops
            m = re.match(r'^loops\s*\((.*?)\)\s*\{\s*$', line)
            if m:
                cond = m.group(1).strip()
                body, i = self.extract_block(lines, i + 1)
                bytecode.append(("LOOPS", cond, body, line_num))
                continue

            # Function call statement
            m = re.match(r'^([a-zA-Z_]\w*)\s*\((.*?)\)\s*;\s*$', line)
            if m:
                bytecode.append(("CALL", m.group(1), m.group(2).strip(), line_num))
                i += 1
                continue

            if line == "}":
                i += 1
                break

            self.errors.append(TeaSyntaxError(
                f"Unknown syntax: '{line}'", line_num, self.current_file
            ))
            i += 1

        return bytecode, i

    def extract_block(self, lines, start):
        body, next_i = self.parse_block(lines, start, top_level=False)
        return body, next_i

    def eval_expr(self, expr, memory, line_num=None):
        expr = expr.strip()
        if not expr:
            return None

        # Array literal
        if expr.startswith("[") and expr.endswith("]"):
            inner = expr[1:-1].strip()
            if not inner:
                return []
            return [self.eval_expr(x.strip(), memory, line_num) for x in inner.split(",")]

        # Array access
        m = re.match(r'^([a-zA-Z_]\w*)\s*\[\s*(.*?)\s*\]$', expr)
        if m:
            name, idx_expr = m.groups()
            flat = {}
            for scope in memory:
                flat.update(scope)

            if name not in flat:
                raise TeaRuntimeError(f"Undefined array '{name}'", line_num, self.current_file)

            arr = flat[name]
            if not isinstance(arr, list):
                raise TeaRuntimeError(f"'{name}' is not an array", line_num, self.current_file)

            idx = self.eval_expr(idx_expr, memory, line_num)
            try:
                return arr[int(idx)]
            except:
                raise TeaRuntimeError(f"Invalid index for array '{name}'", line_num, self.current_file)

        # String
        if (expr.startswith('"') and expr.endswith('"')) or (expr.startswith("'") and expr.endswith("'")):
            return expr[1:-1]

        # Number
        if re.match(r'^-?\d+(\.\d+)?$', expr):
            return float(expr) if "." in expr else int(expr)

        # Library call
        m = re.match(r'^([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)\((.*)\)$', expr)
        if m:
            lib, func, args_str = m.groups()
            if lib not in self.imported_libs:
                raise TeaRuntimeError(f"Library '{lib}' not imported", line_num, self.current_file)

            klass = self.libraries.get(lib)
            if not klass or not hasattr(klass, func):
                raise TeaRuntimeError(f"Unknown function {lib}.{func}", line_num, self.current_file)

            args = [self.eval_expr(a.strip(), memory, line_num) for a in args_str.split(",") if a.strip()]
            try:
                return getattr(klass, func)(*args)
            except Exception as e:
                raise TeaRuntimeError(str(e), line_num, self.current_file)

        # User function
        m = re.match(r'^([a-zA-Z_]\w*)\((.*)\)$', expr)
        if m:
            name, args_str = m.groups()
            if name in self.functions:
                return self.call_function(name, args_str, memory, line_num)
            raise TeaRuntimeError(f"Undefined function '{name}'", line_num, self.current_file)

        # Variable
        flat = {}
        for scope in memory:
            flat.update(scope)
        if expr in flat:
            return flat[expr]

        # Math expression
        try:
            return eval(expr, {"__builtins__": {}}, flat)
        except Exception as e:
            raise TeaRuntimeError(f"Cannot evaluate '{expr}': {e}", line_num, self.current_file)

    def call_function(self, name, args_str, memory, line_num):
        func = self.functions[name]
        args = [self.eval_expr(a.strip(), memory, line_num) for a in args_str.split(",") if a.strip()]

        if len(args) != len(func["params"]):
            raise TeaRuntimeError(
                f"Function '{name}' expects {len(func['params'])} args, got {len(args)}",
                line_num, self.current_file
            )

        local = dict(zip(func["params"], args))
        memory.append(local)

        result = None
        for instr in func["body"]:
            if instr[0] == "RETURN":
                result = self.eval_expr(instr[1], memory, instr[2])
                break
            self.execute(instr, memory)

        memory.pop()
        return result

    def execute(self, instr, memory):
        op = instr[0]
        line = instr[-1]

        if op == "PRINT":
            print(self.eval_expr(instr[1], memory, line))

        elif op == "STORE":
            val = self.eval_expr(instr[2], memory, line)
            memory[-1][instr[1]] = val

        elif op == "IF":
            cond = self.eval_expr(instr[1], memory, line)
            memory.append({})
            if cond:
                self.run_block(instr[2], memory)
            elif instr[3]:
                self.run_block(instr[3], memory)
            memory.pop()

        elif op == "LOOPS":
            while self.eval_expr(instr[1], memory, line):
                memory.append({})
                self.run_block(instr[2], memory)
                memory.pop()

        elif op == "CALL":
            self.eval_expr(f"{instr[1]}({instr[2]})", memory, line)

    def run_block(self, block, memory):
        for instr in block:
            if instr[0] not in ("BOOTSTRAP", "IMPORT", "FUNC_DEF"):
                self.execute(instr, memory)

    def run_vm(self, bytecode):
        memory = [{}]
        print("\n--- Tea VM (Core + Graphics & Game Expanded) ---")
        try:
            self.run_block(bytecode, memory)
        except TeaRuntimeError as e:
            print(e)
            sys.exit(1)
        print("--- Done ---")

if __name__ == "__main__":
    compiler = TeaCompiler()
    target = sys.argv[1] if len(sys.argv) > 1 else "app.tea"

    try:
        bc = compiler.compile_source(target)
        if bc:
            compiler.run_vm(bc)
    except TeaSyntaxError as e:
        print(e)
        sys.exit(1)
