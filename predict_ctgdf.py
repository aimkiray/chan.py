"""兼容性入口：核心逻辑已合并至 predict_stock.py，本文件仅保留中国中免的默认参数。

等效于:
    python predict_stock.py sh.601888 "China Tourism Group Duty Free"
"""
from predict_stock import run_analysis

if __name__ == "__main__":
    run_analysis(code="sh.601888", name="China Tourism Group Duty Free")
