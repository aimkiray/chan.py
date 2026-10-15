"""数据源包。

各数据源为可选依赖（baostock/ccxt/pytdx/akshare/clickhouse-driver 按源独立安装），
本包不在此处做顶层重导出：Chan.GetStockAPI / backend 按 data_src 惰性 import
对应模块，避免某个缺失的三方库把整包的 import 拖崩。
"""
