import sys
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError

TEMPLATES_DIR = Path(__file__).parent.parent / "web" / "templates"

def main():
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    errors = 0

    for template_path in TEMPLATES_DIR.rglob("*.html"):
        name = str(template_path.relative_to(TEMPLATES_DIR))
        try:
            env.parse(template_path.read_text(encoding="utf-8"))
        except TemplateSyntaxError as e:
            print(f"FAIL: {name} (line {e.lineno}): {e.message}")
            errors += 1
        except Exception as e:
            print(f"FAIL: {name}: {e}")
            errors += 1

    if errors > 0:
        print(f"\nFound {errors} syntax error(s) in templates.")
        sys.exit(1)

    print("OK: All templates parsed successfully.")

if __name__ == "__main__":
    main()