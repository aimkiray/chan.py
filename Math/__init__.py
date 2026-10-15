"""技术指标库：MACD / BOLL / KDJ / RSI / Demark 序列 / 趋势线。

接口约定（各指标输入形状不同，入口不强求同名同参）：
- ``add(value)``：CMACD / BollModel / CTrendModel / RSI，逐根喂入单值；
- ``add(high, low, close)``：KDJ，需要 K 线三元组；
- ``update(idx, close, high, low) -> CDemarkIndex``：CDemarkEngine，事件流式，
  返回本根新产生的 setup/countdown 标记，而非逐点指标值；
- ``CTrendLine(lst, side)``：构造即计算，结果读 ``.line``（Optional[Line]，
  None = 无合法趋势线，下游 PlotMeta 已判空兜底）。

输出约定：
- RSI 预热期返回 NaN；窗口内全程无涨跌返回中性 50.0；
- KDJ 一字板（hn==ln）时 RSV 未定义，沿用上期 RSV；
- 各模型构造期校验窗口参数，非法参数抛 ``CChanException(ErrCode.PARA_ERROR)``；
- 历史数组（CMACD.macd_info、Demark kl_lst 等）不裁剪，内存 O(n)，
  属会话级可接受设计（裁剪会改变相对索引语义，暂不实施）。
"""
