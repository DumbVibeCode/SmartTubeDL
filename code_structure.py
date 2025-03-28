import ast
import sys
import os

OUTPUT_FILE = "structure_output.md"

def format_args(args_node):
    args = []
    for arg in args_node.args:
        args.append(arg.arg)
    if args_node.vararg:
        args.append(f"*{args_node.vararg.arg}")
    if args_node.kwarg:
        args.append(f"**{args_node.kwarg.arg}")
    return ", ".join(args)

def analyze_node(node, indent_level=0):
    lines = []
    indent_md = "  " * indent_level  # два пробела на уровень
    if isinstance(node, ast.ClassDef):
        lines.append(f"{indent_md}- **КЛАСС** `{node.name}` (строка {node.lineno})")
    elif isinstance(node, ast.FunctionDef):
        args = format_args(node.args)
        lines.append(f"{indent_md}- `def {node.name}({args})` (строка {node.lineno})")

    for child in ast.iter_child_nodes(node):
        lines.extend(analyze_node(child, indent_level + 1))

    return lines

def analyze_code_structure(filepath):
    if not os.path.isfile(filepath):
        print(f"[ERROR] Файл не найден: {filepath}")
        return

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)

        structure = []

        for node in tree.body:
            structure.extend(analyze_node(node))

        with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
            out.write(f"# 📁 Структура кода: `{os.path.basename(filepath)}`\n\n")
            for line in structure:
                out.write(line + "\n")

        print(f"✅ Markdown-структура сохранена в {OUTPUT_FILE}")

    except Exception as e:
        print(f"[ERROR] Ошибка при анализе структуры: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("⚠️ Укажи путь к .py файлу:\nПример: python code_structure.py ytd.py")
    else:
        analyze_code_structure(sys.argv[1])
