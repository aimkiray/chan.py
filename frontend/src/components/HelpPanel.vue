<script setup>
import { MemoryBook } from '@pictogrammers/memory'
</script>

<template>
  <div class="help-content">
    <h2 class="help-title flex items-center gap-2">
      <svg width="24" height="24" viewBox="0 0 24 24" class="text-blue-600"><path :d="MemoryBook" /></svg>
      缠论量化分析 - 使用指南
    </h2>
    
    <div class="markdown-body">
      <h3>1. 核心概念详解</h3>
      <p>缠论是一套基于市场走势结构的几何分析理论。本系统自动识别以下核心组件：</p>
      
      <h4>A. 分型 (Fractal)</h4>
      <ul>
        <li><strong>顶分型</strong>: 类似 "∧" 形状，由三根K线组成，中间K线高点最高，低点最高。</li>
        <li><strong>底分型</strong>: 类似 "∨" 形状，由三根K线组成，中间K线低点最低，高点最低。</li>
        <li><em>意义</em>: 分型是走势转折的雏形。</li>
      </ul>
      
      <h4>B. 笔 (Bi)</h4>
      <ul>
        <li><strong>定义</strong>: 连接相邻的顶分型和底分型的连线。</li>
        <li><strong>构成</strong>: 必须由顶分型+底分型（或反之）构成，且中间必须包含一定数量的K线。</li>
        <li><strong>严格笔</strong>: 要求顶底分型之间至少有<strong>3根</strong>非包含关系的K线。开启"严格笔"模式能过滤掉微小的震荡，使走势结构更清晰。</li>
        <li><em>图示</em>: 图表中用 <strong>黑色线</strong> 表示。实线为已确认的笔，虚线为未完成的笔。</li>
      </ul>
      
      <h4>C. 线段 (Segment)</h4>
      <ul>
        <li><strong>定义</strong>: 由连续的三笔（或更多）且有重叠的笔构成。</li>
        <li><em>图示</em>: 图表中用 <strong>绿色线</strong> 表示。代表比"笔"更高一级的走势。</li>
      </ul>
      
      <h4>D. 中枢 (Center/Pivot)</h4>
      <ul>
        <li><strong>定义</strong>: 某级别走势类型中，被至少三个连续次级别走势类型所重叠的部分。</li>
        <li><em>图示</em>: 图表中用 <strong>橙色矩形框</strong> 表示。</li>
        <li><em>意义</em>: 中枢是多空双方力量的平衡区，所有的买卖点都围绕中枢产生。</li>
      </ul>
      
      <h3>2. 买卖点类型 (Type)</h3>
      <p>在分析结果中，您会看到不同类型的买卖点信号，它们具有不同的市场含义：</p>
      
      <table class="help-table">
        <thead>
          <tr>
            <th>类型</th>
            <th>名称</th>
            <th>详细解释</th>
            <th>风险/收益</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><strong>1类 (1B/1S)</strong></td>
            <td><strong>趋势转折</strong></td>
            <td>在下跌趋势末端，因力度衰竭（背驰）而产生的转折点。通常是行情的最低点。</td>
            <td>风险较高，收益最大</td>
          </tr>
          <tr>
            <td><strong>2类 (2B/2S)</strong></td>
            <td><strong>回撤确认</strong></td>
            <td>1类买点出现后，价格第一次回调不创新低（或微创新低但力度更弱）。是对趋势反转的确认。</td>
            <td>风险中等，胜率较高</td>
          </tr>
          <tr>
            <td><strong>3类 (3B/3S)</strong></td>
            <td><strong>中枢破坏</strong></td>
            <td>价格强势突破中枢后，回调不进入中枢内部。意味着行情进入主升浪（或主跌浪）。</td>
            <td>风险较低，爆发力强</td>
          </tr>
          <tr>
            <td><strong>类二买 (2s)</strong></td>
            <td><strong>强力回撤</strong></td>
            <td>针对大级别中枢的强力底分型或次级别回撤，形态上接近二买。</td>
            <td>-</td>
          </tr>
        </tbody>
      </table>
      
      <h3>3. AI 信号评分</h3>
      <p>本项目结合了 <strong>XGBoost</strong> 机器学习模型。</p>
      <ul>
        <li>系统会提取买卖点出现时的特征（如MACD力度、成交量变化、K线形态、均线位置等）。</li>
        <li>将其输入模型，预测该信号未来盈利的概率。</li>
        <li><strong>Confidence Score (置信度)</strong>: 分数越高 (0~100%)，表示模型认为该信号越可靠。建议结合评分 > 50% 的信号进行参考。</li>
      </ul>
      
      <h3>4. 参数说明</h3>
      <ul>
        <li><strong>逐步计算 (Trigger Step)</strong>: 模拟真实盘中环境，一根根K线推进计算。
          <ul>
            <li><em>开启</em>: 能避免“未来函数”，看到当时真实的信号状态（可能会有信号消失的情况）。</li>
            <li><em>关闭</em>: 直接使用全量数据计算，速度快，但可能包含未来修正后的结果。</li>
          </ul>
        </li>
        <li><strong>严格笔 (Strict Bi)</strong>:
          <ul>
            <li><em>开启</em>: 过滤杂波，适合看大趋势。</li>
            <li><em>关闭</em>: 反应灵敏，适合捕捉短线波动。</li>
          </ul>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.help-content {
  padding: 2rem;
  max-width: 900px;
  margin: 0 auto;
  background-color: white;
  border-radius: 0.5rem;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.help-title {
  color: #1f2937;
  margin-bottom: 2rem;
  border-bottom: 2px solid #e5e7eb;
  padding-bottom: 1rem;
}

.markdown-body h3 {
  color: #374151;
  margin-top: 1.5rem;
  margin-bottom: 1rem;
  font-size: 1.25rem;
  font-weight: 600;
}

.markdown-body h4 {
  color: #4b5563;
  margin-top: 1.25rem;
  margin-bottom: 0.5rem;
  font-size: 1rem;
  font-weight: 600;
}

.markdown-body p {
  color: #6b7280;
  line-height: 1.6;
  margin-bottom: 1rem;
}

.markdown-body ul {
  padding-left: 1.5rem;
  margin-bottom: 1rem;
  color: #6b7280;
}

.markdown-body li {
  margin-bottom: 0.5rem;
}

.help-table {
  width: 100%;
  border-collapse: collapse;
  margin: 1.5rem 0;
  font-size: 0.9rem;
}

.help-table th, .help-table td {
  border: 1px solid #d1d5db;
  padding: 0.75rem;
  text-align: left;
}

.help-table th {
  background-color: #f9fafb;
  font-weight: 600;
  color: #374151;
}

.help-table td {
  color: #4b5563;
}
</style>
