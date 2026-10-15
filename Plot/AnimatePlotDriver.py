import matplotlib.pyplot as plt

from Chan import CChan

from .PlotDriver import CPlotDriver


class CAnimateDriver:
    """
    逐步加载动画驱动（Jupyter 环境使用）。

    每帧构造一个 CPlotDriver 全量重绘。总长 N 时复杂度 O(N^2)——
    保留全量重建的原因：step_load 每个快照都会重算笔/段/中枢/买卖点，
    增量复用需要跨帧追踪 artist 与重建 meta 的一致性，风险大于收益；
    且 trigger_step 主要用于小数据量调试回放。
    """

    def __init__(self, chan: CChan, plot_config=None, plot_para=None):
        # IPython 依赖移入方法体：Jupyter 之外仅 import 本模块不应硬依赖 IPython
        from IPython.display import clear_output, display
        if plot_config is None:
            plot_config = {}
        if plot_para is None:
            plot_para = {}
        for _ in chan.step_load():
            g = CPlotDriver(chan, plot_config, plot_para)
            try:
                clear_output(wait=True)
                display(g.figure)
            finally:
                # 正常/异常路径都必须 close，否则逐帧 figure 泄漏
                plt.close(g.figure)
