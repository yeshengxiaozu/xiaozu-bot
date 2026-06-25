# updater/paths.py
from pathlib import Path

# 当前 updater 文件所在目录
PLUGIN_DIR = Path(__file__).resolve().parent.parent

# data 统一目录（唯一真实写入点）
DATA_DIR = PLUGIN_DIR / "data"

def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
