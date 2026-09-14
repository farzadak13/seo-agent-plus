import os

# پوشه‌هایی که باید نادیده گرفته شوند
IGNORE_DIRS = {'.venv', 'venv', 'env', '__pycache__', '.pytest_cache', '.git', 'db', 'tests'}
OUTPUT_FILE = 'all_project_code.txt'
TARGET_DIR = '.'

with open(OUTPUT_FILE, 'w', encoding='utf-8') as outfile:
    for root, dirs, files in os.walk(TARGET_DIR):
        # فیلتر کردن پوشه‌های اضافی
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        
        for file in files:
            if file.endswith('.py') and file != 'merge.py':
                filepath = os.path.join(root, file)
                outfile.write(f"\n\n{'='*60}\n")
                outfile.write(f"FILE: {filepath}\n")
                outfile.write(f"{'='*60}\n\n")
                
                try:
                    with open(filepath, 'r', encoding='utf-8') as infile:
                        outfile.write(infile.read())
                except Exception as e:
                    outfile.write(f"# Error reading file: {e}\n")

print(f"Done! All code merged into: {OUTPUT_FILE}")