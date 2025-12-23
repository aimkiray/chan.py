export const translations = {
  zh: {
    app: {
      title: "缠论量化分析",
      about: "关于本应用",
      aboutContent: "本应用结合 <strong>缠论 (Chan Theory)</strong> 与 <strong>XGBoost</strong> 等多种机器学习模型来分析股票走势。",
      aboutChan: "<strong>缠论</strong>: 识别走势结构（笔、线段、中枢）及买卖点。",
      aboutML: "<strong>XGBoost</strong>: 基于历史表现验证信号的有效性。",
      disclaimer: "免责声明: 本工具仅供学习研究，不构成投资建议。",
      stockAnalysis: "股票分析",
      chart: "走势图",
      intradayAnalysis: "实时分析",
      history: "历史记录",
      guide: "使用指南",
      periodSelect: "周期选择:",
      dataSrc: "数据源:",
      refresh: "刷新分析",
      predict: "开始预测",
      modelSelect: "模型选择:",
      intradayChart: "分时走势",
      waitingForData: "请点击上方“刷新分析”或侧边栏“开始分析”以查看数据",
      pleasePredict: "P.S. 请点击开始预测获取模型推荐信号。",
      dataSrcOptions: {
        baostock: "BaoStock",
        akshare: "AkShare",
        jqdata: "JQData",
        mock: "Mock (1s测试)"
      },
      modelOptions: {
        xgboost: "XGBoost",
        lightgbm: "LightGBM",
        mlp: "MLP"
      }
    },
    sidebar: {
      params: "参数设置",
      stockCode: "股票代码",
      codePlaceholder: "如 002701 或 600000",
      codeHint: "支持自动识别沪深 (如 002701, 600000)",
      stepCalc: "逐步计算 (模拟实盘)",
      stepHint: "开启后将模拟真实交易环境，逐根 K 线加载并计算。",
      strictBi: "严格笔模式",
      strictHint: "要求顶底分型之间至少有3根非包含关系的K线。",
      analyze: "开始分析",
      analyzing: "正在分析...",
      download: "下载历史数据",
      downloading: "下载中...",
      expandSidebar: "展开侧边栏"
    },
    analysis: {
      result: "分析结果",
      latestPrice: "最新价格",
      latestSignal: "最新信号",
      type: "类型",
      signalDate: "信号日期",
      timeDiff: "距今",
      timeDiffDays: "距今 (天)",
      score: "信号有效性评分",
      highScore: "信号有效性较高",
      lowScore: "信号可能较弱",
      accuracy: "历史准确率 (自2020起)",
      buy: "买入",
      sell: "卖出",
      minutes: "分钟",
      hours: "小时"
    },
    signal_desc: {
      buy: {
        '1': "第一类买点：趋势背驰引发的极佳抄底机会。建议分批建仓，止损位可设在背驰低点。",
        '2': "第二类买点：一买后的次级别回踩不破前低，确立上涨趋势。风险较低，适合稳健建仓或加仓。",
        '3': "第三类买点：强力突破中枢后的回踩确认，不回中枢内部。往往预示主升浪，爆发力强，适合激进追涨。",
        '2s': "类二买：虽未形成标准二买，但出现强底分型，短期反弹概率大，可轻仓博弈。",
        '2p': "类二买：强底分型结构，具备一定反弹潜力，注意设置止损。",
        '1p': "盘整背驰买点：震荡区间下沿的背驰信号，适合做短线反弹或高抛低吸。",
        '3a': "三买 A：中枢破坏后的首次回踩确认，趋势延续的强力信号。",
        '3b': "三买 B：中枢震荡后的再次确认，趋势依然向上。",
        default: "买入信号：缠论结构发出的买入提示，请结合大级别走势研判。"
      },
      sell: {
        '1': "第一类卖点：趋势背驰引发的见顶信号。建议立即减仓或清仓，锁定利润，规避反转风险。",
        '2': "第二类卖点：一卖后的次级别反弹不过前高，确立下跌趋势。这是最后的逃命机会，建议离场。",
        '3': "第三类卖点：跌破中枢后的反抽不回中枢内部。往往预示主跌浪来临，杀伤力大，必须清仓规避。",
        '2s': "类二卖：虽未形成标准二卖，但出现强顶分型，短期回调风险大，建议减仓防守。",
        '2p': "类二卖：强顶分型结构，短期头部迹象明显，注意获利了结。",
        '1p': "盘整背驰卖点：震荡区间上沿的背驰信号，适合短线高抛。",
        '3a': "三卖 A：中枢破坏后的首次反抽确认，下跌趋势延续，切勿盲目抄底。",
        '3b': "三卖 B：中枢震荡后的再次确认，空头力量依然主导。",
        default: "卖出信号：缠论结构发出的卖出提示，注意风险控制。"
      }
    },
    periods: {
      "1s": "1秒",
      "1m": "1分钟",
      "5m": "5分钟",
      "15m": "15分钟",
      "30m": "30分钟",
      "60m": "60分钟",
      "1d": "日线",
      "1w": "周线",
      "1mo": "月线"
    },
    history: {
      title: "分析历史",
      refresh: "刷新",
      code: "代码",
      period: "周期",
      model: "模型",
      analyzeTime: "分析时间",
      dataTime: "数据时间",
      signal: "信号",
      accuracy: "准确率",
      action: "操作",
      viewChart: "查看图表"
    },
    chart: {
      legend: "图例",
      kline: "K线:",
      klineDesc: "基础K线图 (开/高/低/收)。<br/><span class='text-xs text-gray-400'>红涨绿跌。</span>",
      bi: "笔:",
      biDesc: "连接相邻顶底分型的连线。<br/><span class='text-xs text-gray-400'>实线=已确认，虚线=未完成。</span>",
      seg: "线段:",
      segDesc: "更高级别的走势结构。<br/><span class='text-xs text-green-600'>绿色连线。</span>",
      center: "中枢:",
      centerDesc: "价格密集重叠区域。<br/><span class='text-xs text-orange-500'>橙色矩形框。</span>",
      ma: "均线:",
      maDesc: "移动平均线 (如 MA5=5日均线)。",
      tooltip: {
         kline: "<b>K线</b><br/>基础K线图 (开/高/低/收)。",
         bi: "<b>笔 (Bi)</b><br/>连接相邻顶底分型的连线。<br/>实线=已确认，虚线=未完成。",
         seg: "<b>线段 (Segment)</b><br/>由3笔以上构成的更高级别结构。<br/>绿色连线。",
         center: "<b>中枢 (Center/Pivot)</b><br/>至少3笔/线段重叠的价格区域。<br/>橙色矩形框。",
         ma: "<b>移动平均线 ({name})</b><br/>过去 {n} 个周期的收盘价平均值。"
      }
    },
    help: {
      title: "缠论量化分析 - 使用指南",
      section1: {
        title: "1. 核心概念详解",
        intro: "缠论是一套基于市场走势结构的几何分析理论。本系统自动识别以下核心组件：",
        fractal: {
          title: "A. 分型 (Fractal)",
          top: "<strong>顶分型</strong>: 类似 '∧' 形状，由三根K线组成，中间K线高点最高，低点最高。",
          bottom: "<strong>底分型</strong>: 类似 '∨' 形状，由三根K线组成，中间K线低点最低，高点最低。",
          meaning: "<em>意义</em>: 分型是走势转折的雏形。"
        },
        bi: {
          title: "B. 笔 (Bi)",
          def: "<strong>定义</strong>: 连接相邻的顶分型和底分型的连线。",
          comp: "<strong>构成</strong>: 必须由顶分型+底分型（或反之）构成，且中间必须包含一定数量的K线。",
          strict: "<strong>严格笔</strong>: 要求顶底分型之间至少有<strong>3根</strong>非包含关系的K线。开启'严格笔'模式能过滤掉微小的震荡，使走势结构更清晰。",
          visual: "<em>图示</em>: 图表中用 <strong>黑色线</strong> 表示。实线为已确认的笔，虚线为未完成的笔。"
        },
        seg: {
          title: "C. 线段 (Segment)",
          def: "<strong>定义</strong>: 由连续的三笔（或更多）且有重叠的笔构成。",
          visual: "<em>图示</em>: 图表中用 <strong>绿色线</strong> 表示。代表比'笔'更高一级的走势。"
        },
        center: {
          title: "D. 中枢 (Center/Pivot)",
          def: "<strong>定义</strong>: 某级别走势类型中，被至少三个连续次级别走势类型所重叠的部分。",
          visual: "<em>图示</em>: 图表中用 <strong>橙色矩形框</strong> 表示。",
          meaning: "<em>意义</em>: 中枢是多空双方力量的平衡区，所有的买卖点都围绕中枢产生。"
        }
      },
      section2: {
        title: "2. 买卖点类型 (Type)",
        intro: "在分析结果中，您会看到不同类型的买卖点信号，它们具有不同的市场含义：",
        table: {
          type: "类型",
          name: "名称",
          desc: "详细解释",
          risk: "风险/收益",
          t1: { type: "1类 (1B/1S)", name: "趋势转折", desc: "在下跌趋势末端，因力度衰竭（背驰）而产生的转折点。通常是行情的最低点。", risk: "风险较高，收益最大" },
          t2: { type: "2类 (2B/2S)", name: "回撤确认", desc: "1类买点出现后，价格第一次回调不创新低（或微创新低但力度更弱）。是对趋势反转的确认。", risk: "风险中等，胜率较高" },
          t3: { type: "3类 (3B/3S)", name: "中枢破坏", desc: "价格强势突破中枢后，回调不进入中枢内部。意味着行情进入主升浪（或主跌浪）。", risk: "风险较低，爆发力强" },
          t2s: { type: "类二买 (2s)", name: "强力回撤", desc: "针对大级别中枢的强力底分型或次级别回撤，形态上接近二买。", risk: "-" }
        }
      },
      section3: {
        title: "3. AI 信号评分",
        intro: "本项目结合了 <strong>XGBoost</strong> 机器学习模型。",
        desc1: "系统会提取买卖点出现时的特征（如MACD力度、成交量变化、K线形态、均线位置等）。",
        desc2: "将其输入模型，预测该信号未来盈利的概率。",
        score: "<strong>Confidence Score (置信度)</strong>: 分数越高 (0~100%)，表示模型认为该信号越可靠。建议结合评分 > 50% 的信号进行参考。"
      },
      section4: {
        title: "4. 参数说明",
        step: "<strong>逐步计算 (Trigger Step)</strong>: 模拟真实盘中环境，一根根K线推进计算。",
        stepDesc: "<em>开启</em>: 能避免“未来函数”，看到当时真实的信号状态（可能会有信号消失的情况）。<br/><em>关闭</em>: 直接使用全量数据计算，速度快，但可能包含未来修正后的结果。",
        strict: "<strong>严格笔 (Strict Bi)</strong>:",
        strictDesc: "<em>开启</em>: 过滤杂波，适合看大趋势。<br/><em>关闭</em>: 反应灵敏，适合捕捉短线波动。"
      }
    }
  },
  en: {
    app: {
      title: "Chan Quant Analysis",
      about: "About",
      aboutContent: "This app combines <strong>Chan Theory</strong> and <strong>Machine Learning (XGBoost)</strong> to analyze stock trends.",
      aboutChan: "<strong>Chan Theory</strong>: Identifies trend structures (Bi, Segments, Pivots) and buy/sell points.",
      aboutML: "<strong>XGBoost</strong>: Validates signals based on historical performance.",
      disclaimer: "Disclaimer: For research purposes only, not investment advice.",
      stockAnalysis: "Stock Analysis",
      chart: "Chart",
      intradayAnalysis: "Real-time Analysis",
      history: "History",
      guide: "Guide",
      periodSelect: "Period:",
      dataSrc: "Source:",
      refresh: "Refresh",
      predict: "Predict",
      modelSelect: "Model:",
      intradayChart: "Intraday Chart",
      waitingForData: "Please click 'Refresh' or 'Start Analysis' to view data.",
      pleasePredict: "P.S. Click 'Predict' to get model recommendations.",
      dataSrcOptions: {
        baostock: "BaoStock",
        akshare: "AkShare",
        jqdata: "JQData",
        mock: "Mock (1s Test)"
      },
      modelOptions: {
        xgboost: "XGBoost",
        lightgbm: "LightGBM",
        mlp: "MLP"
      }
    },
    sidebar: {
      params: "Settings",
      stockCode: "Stock Code",
      codePlaceholder: "e.g. 002701 or 600000",
      codeHint: "Supports SH/SZ auto-detection",
      stepCalc: "Step Calculation (Sim)",
      stepHint: "Simulates real-time trading environment by loading K-lines one by one.",
      strictBi: "Strict Bi Mode",
      strictHint: "Requires at least 3 non-included K-lines between top/bottom types.",
      analyze: "Analyze",
      analyzing: "Analyzing...",
      download: "Download Data",
      downloading: "Downloading...",
      expandSidebar: "Expand Sidebar"
    },
    analysis: {
      result: "Analysis Result",
      latestPrice: "Latest Price",
      latestSignal: "Latest Signal",
      type: "Type",
      signalDate: "Signal Date",
      timeDiff: "Time Ago",
      timeDiffDays: "Days Ago",
      score: "Signal Score",
      highScore: "High Confidence",
      lowScore: "Low Confidence",
      accuracy: "Accuracy (Since 2020)",
      buy: "BUY",
      sell: "SELL",
      minutes: "min",
      hours: "hrs"
    },
    signal_desc: {
      buy: {
        '1': "Buy Type 1: Bottom reversal signal caused by trend divergence. Best opportunity for bottom fishing. Stop loss at the lowest point.",
        '2': "Buy Type 2: First pullback after Type 1 buy without breaking the new low. Confirms the uptrend. Lower risk.",
        '3': "Buy Type 3: Strong breakout above the center/pivot, followed by a pullback that doesn't re-enter the center. Signals a strong main uptrend.",
        '2s': "Buy Type 2s: Similar to Type 2 but based on strong bottom fractal or sub-level pullback.",
        '2p': "Buy Type 2p: Strong bottom fractal structure with rebound potential.",
        '1p': "Buy Type 1p: Divergence signal at the bottom of a consolidation range. Good for short-term trading.",
        '3a': "Buy Type 3a: First confirmation after center destruction.",
        '3b': "Buy Type 3b: Second confirmation after center oscillation.",
        default: "Buy Signal: Buy prompt from Chan structure."
      },
      sell: {
        '1': "Sell Type 1: Top reversal signal caused by trend divergence. Suggests clearing positions to avoid reversal risk.",
        '2': "Sell Type 2: First rebound after Type 1 sell that doesn't break the new high. Confirms the downtrend. Last chance to exit.",
        '3': "Sell Type 3: Drop below the center/pivot, followed by a rebound that doesn't re-enter. Signals a strong main downtrend.",
        '2s': "Sell Type 2s: Similar to Type 2 but based on strong top fractal.",
        '2p': "Sell Type 2p: Strong top fractal structure, indicating short-term peak.",
        '1p': "Sell Type 1p: Divergence signal at the top of a consolidation range.",
        '3a': "Sell Type 3a: First confirmation after center destruction downwards.",
        '3b': "Sell Type 3b: Second confirmation after center oscillation.",
        default: "Sell Signal: Sell prompt from Chan structure."
      }
    },
    periods: {
      "1s": "1s",
      "1m": "1m",
      "5m": "5m",
      "15m": "15m",
      "30m": "30m",
      "60m": "60m",
      "1d": "Daily",
      "1w": "Weekly",
      "1mo": "Monthly"
    },
    history: {
      title: "Analysis History",
      refresh: "Refresh",
      code: "Code",
      period: "Period",
      model: "Model",
      analyzeTime: "Analyze Time",
      dataTime: "Data Time",
      signal: "Signal",
      accuracy: "Accuracy",
      action: "Action",
      viewChart: "View Chart"
    },
    chart: {
      legend: "Legend",
      kline: "K-Line:",
      klineDesc: "Basic price chart with Open, Close, High, Low.<br/><span class='text-xs text-gray-400'>Red=Up, Green=Down.</span>",
      bi: "Bi:",
      biDesc: "Connects adjacent top/bottom fractals.<br/><span class='text-xs text-gray-400'>Solid=Confirmed, Dashed=Unfinished.</span>",
      seg: "Seg:",
      segDesc: "Higher level structure.<br/><span class='text-xs text-green-600'>Green line.</span>",
      center: "Center:",
      centerDesc: "Price consolidation area.<br/><span class='text-xs text-orange-500'>Orange rectangle.</span>",
      ma: "MA:",
      maDesc: "Moving Average (e.g. MA5=5-day avg).",
      tooltip: {
         kline: "<b>K-Line</b><br/>Basic price chart with Open, Close, High, Low.",
         bi: "<b>Bi</b><br/>Connects adjacent top/bottom fractals.<br/>Solid=Confirmed, Dashed=Unfinished.",
         seg: "<b>Segment</b><br/>Higher level structure composed of 3+ Bi.<br/>Green line.",
         center: "<b>Center/Pivot</b><br/>Price consolidation area overlapping 3+ Bi/Seg.<br/>Orange rectangle.",
         ma: "<b>Moving Average ({name})</b><br/>Average close price of past {n} periods."
      }
    },
    help: {
      title: "Chan Quant Analysis - User Guide",
      section1: {
        title: "1. Core Concepts",
        intro: "Chan Theory is a geometric analysis theory based on market trend structures. This system automatically identifies:",
        fractal: {
          title: "A. Fractal",
          top: "<strong>Top Fractal</strong>: '∧' shape, 3 K-lines, middle high is highest, low is highest.",
          bottom: "<strong>Bottom Fractal</strong>: '∨' shape, 3 K-lines, middle low is lowest, high is lowest.",
          meaning: "<em>Meaning</em>: The embryo of trend reversal."
        },
        bi: {
          title: "B. Bi (Pen)",
          def: "<strong>Definition</strong>: Connection between adjacent top and bottom fractals.",
          comp: "<strong>Composition</strong>: Must be Top+Bottom (or vice versa) with sufficient K-lines in between.",
          strict: "<strong>Strict Bi</strong>: Requires at least <strong>3</strong> non-included K-lines between fractals. Enabling 'Strict Bi' filters noise.",
          visual: "<em>Visual</em>: <strong>Black line</strong>. Solid = Confirmed, Dashed = Unfinished."
        },
        seg: {
          title: "C. Segment",
          def: "<strong>Definition</strong>: Composed of 3 or more overlapping Bi.",
          visual: "<em>Visual</em>: <strong>Green line</strong>. Higher level structure than Bi."
        },
        center: {
          title: "D. Center/Pivot",
          def: "<strong>Definition</strong>: Overlapping part of at least 3 consecutive sub-level trends.",
          visual: "<em>Visual</em>: <strong>Orange rectangle</strong>.",
          meaning: "<em>Meaning</em>: Balance area of buyer/seller forces. All buy/sell points generate around it."
        }
      },
      section2: {
        title: "2. Buy/Sell Point Types",
        intro: "Analysis results show different types of signals with different meanings:",
        table: {
          type: "Type",
          name: "Name",
          desc: "Description",
          risk: "Risk/Reward",
          t1: { type: "Type 1 (1B/1S)", name: "Trend Reversal", desc: "Reversal at end of trend due to divergence. Usually the extreme point.", risk: "High Risk, Max Reward" },
          t2: { type: "Type 2 (2B/2S)", name: "Pullback Confirm", desc: "First pullback after Type 1 not breaking new low/high. Confirms reversal.", risk: "Medium Risk, High Win Rate" },
          t3: { type: "Type 3 (3B/3S)", name: "Center Destruction", desc: "Strong breakout from center without re-entering. Signals main wave.", risk: "Low Risk, Strong Momentum" },
          t2s: { type: "Type 2s", name: "Strong Pullback", desc: "Similar to Type 2 but based on strong fractal or sub-level pullback.", risk: "-" }
        }
      },
      section3: {
        title: "3. AI Signal Score",
        intro: "This project integrates <strong>XGBoost</strong> machine learning model.",
        desc1: "Extracts features (MACD, Volume, K-line patterns) when signal appears.",
        desc2: "Predicts probability of future profit.",
        score: "<strong>Confidence Score</strong>: Higher score (0~100%) = higher reliability. Scores > 50% are recommended."
      },
      section4: {
        title: "4. Parameters",
        step: "<strong>Step Calculation</strong>: Simulates real-time trading.",
        stepDesc: "<em>On</em>: Avoids 'future function', sees state as it was.<br/><em>Off</em>: Uses full data, faster but may include future corrections.",
        strict: "<strong>Strict Bi</strong>:",
        strictDesc: "<em>On</em>: Filters noise, good for big trends.<br/><em>Off</em>: More sensitive, good for short-term."
      }
    }
  }
}
