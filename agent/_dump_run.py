from pathlib import Path
src = Path(r"C:\Users\edwar\prism-ml\agent\overseer.py").read_text(encoding="utf-8")
start = src.find("def run(args:")
end = src.find("\ndef main(", start)
Path(r"C:\Users\edwar\prism-ml\agent\_run_dump.txt").write_text(src[start:end], encoding="utf-8")
print("bytes", end-start)
# also argparse section
a = src.find("def main(")
print(src[a:a+900])
