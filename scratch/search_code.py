import os
import sys

# Set output encoding to UTF-8
sys.stdout.reconfigure(encoding='utf-8')

keywords = ["price", "category", "sort", "order", "filter"]
for root, dirs, files in os.walk(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    for file in files:
        if file.endswith(".py"):
            path = os.path.join(root, file)
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                for i, line in enumerate(lines):
                    for kw in keywords:
                        if kw in line.lower():
                            print(f"{file}:{i+1} -> {line.strip()[:100]}")
                            break
