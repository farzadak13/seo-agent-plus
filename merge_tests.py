import os

# پوشه‌هایی که در تست‌ها باید نادیده گرفته شوند
IGNORE_DIRS = {'__pycache__', '.pytest_cache'}
OUTPUT_FILE = 'all_test_code.txt'
TARGET_DIR = './tests'  # مسیر دقیق پوشه تست‌ها

if not os.path.exists(TARGET_DIR):
    print(f"Directory '{TARGET_DIR}' not found! Please make sure the 'tests' folder exists.")
else:
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as outfile:
        for root, dirs, files in os.walk(TARGET_DIR):
            # فیلتر کردن پوشه‌های اضافی (کش‌ها)
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            
            for file in files:
                # فقط فایل‌های پایتون را می‌خوانیم
                if file.endswith('.py'):
                    filepath = os.path.join(root, file)
                    outfile.write(f"\n\n{'='*60}\n")
                    outfile.write(f"FILE: {filepath}\n")
                    outfile.write(f"{'='*60}\n\n")
                    
                    try:
                        with open(filepath, 'r', encoding='utf-8') as infile:
                            outfile.write(infile.read())
                    except Exception as e:
                        outfile.write(f"# Error reading file: {e}\n")

    print(f"Done! All test code merged into: {OUTPUT_FILE}")